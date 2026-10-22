# =============================================================================
# E2iPTV
# Screen 1 — Main Browse Screen
# B1 icon rail (collapsed) | B2 provider/category list | B3 channel list
# PiG top-right
# =============================================================================

from . import theme as _T
from .info_box import E2iBox as _E2iBox
from .paths import HDD, LOG  # data folder / debug logs
from .bg import run_now  # user-facing jobs skip the busy worker pool
from .paths import DATA  # E2iPTV_Data folder
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.GUIComponent import GUIComponent
from Components.MultiContent import MultiContentEntryText, MultiContentEntryPixmapAlphaBlend
from Tools.LoadPixmap import LoadPixmap
from enigma import eTimer, eListbox, eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_HALIGN_CENTER, RT_VALIGN_CENTER, gRGB, eServiceReference, ePoint, eSize

# BT_SCALE/BT_KEEP_ASPECT_RATIO tell MultiContentEntryPixmapAlphaBlend to
# actually scale a picon down to fit its reserved box, preserving aspect
# ratio rather than stretching. Without this, a picon larger than the
# box (BMX's own picons are not pre-sized to this plugin's 56x58 slot)
# gets blitted at its native resolution — only whichever fraction of the
# image happens to land inside the box is visible, which is why picons
# were showing as small, inconsistent, cropped-looking fragments rather
# than the whole logo. Imported defensively: these constants are
# effectively universal across Enigma2 images, but falling back to 0
# (old, unscaled behaviour) rather than crashing the plugin if a given
# image build genuinely lacks them is a lot cheaper than finding out the
# hard way.
try:
    from enigma import BT_SCALE, BT_KEEP_ASPECT_RATIO
    PICON_FLAGS = BT_SCALE | BT_KEEP_ASPECT_RATIO
except ImportError:
    PICON_FLAGS = 0
from twisted.internet import reactor
from .backend.provider import shows as _shows
from .backend.private import hidden as _private_hidden
from .state import state, _epg_recording_colour, _nav_debug
from .b1common import B1_ICONS, B1_ICON_PATH, B1_Y_POS, B1_LABELS, load_b1_last_choice
from .b2common import B2Common
from .b3common import B3Common
from .screen3 import Screen3
from .grid_mode import (GridMode, grid_skin_xml, IE2iList as _GridList,
                        ITEM_H as _G_ITEM_H, SMENU_ROWS as _G_SMENU_ROWS)
from enigma import eActionMap as _eActionMap
from .screen2 import Screen2
from .media_browser import MediaBrowser
from .player import play_channel
from .search import open_search
from .favourites import open_favourites
import os
import time

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

B2_STATE_FILE = "/etc/enigma2/e2iptvlab_b2"
CAT_INDENT    = "   "

RAIL_COLOUR   = _T.hexs("rail")
BG_COLOUR     = _T.hexs("panel")
TILE_COLOUR   = _T.hexs("hi")

# B2 category source: live / series / movies
B2_CONTENT = "live"

# The B1 icon rail here is deliberately collapsed/static (no labels, no
# selection tile — that interactive behaviour lives in Screen2). The icon
# list, path and vertical positions are still the single copy owned by
# b1common.py so a rail change (e.g. adding a menu item) only has to be
# made there and is picked up by every screen automatically.
ICON_PATH = B1_ICON_PATH
ICONS     = B1_ICONS
Y_POS     = B1_Y_POS
# -----------------------------------------------------------------------------
# State persistence helpers
# -----------------------------------------------------------------------------

B2_OPEN_FILE  = DATA + "/cache/b2_open.json"


def _cat_log(provider, msg):
    """Live TV category loading, per provider (Xtream connection log -
    the name only, never login details)."""
    try:
        from .paths import LOG
        with open(LOG("e2iptv_xtream.log"), "a") as f:
            f.write("%s %s categories: %s\n" % (
                time.strftime("%H:%M:%S"), getattr(provider, "name", "?"), msg))
    except Exception:
        pass

def _load_b2_state():
    # Live TV opens on the category of the channel last played (with
    # that channel highlighted), whatever was browsed after it.
    try:
        from .last_live import load as _ll_load
        _ll = _ll_load()
        if _ll.get("view_provider_key") and _ll.get("view_category_id"):
            return str(_ll["view_provider_key"]), str(_ll["view_category_id"])
    except Exception:
        pass
    try:
        parts = open(B2_STATE_FILE).read().strip().split(":", 1)
        return parts[0], parts[1] if len(parts) > 1 else ""
    except:
        return "", ""

def _save_b2_state(provider_key, category_id):
    """Remember the last provider/category (and the last category per
    provider). Called every time B2 scrolling stops, so it must never
    make the GUI wait: values are kept in memory, files are only written
    when something actually changed, and the writes happen on a
    background thread. (Writing straight to the hard disk here made B2
    show a spinner whenever the disk had gone to sleep and had to spin
    up first.)"""
    global _b2_last_written
    pk, cid = str(provider_key), str(category_id)
    cats = _b2_last_cats()
    state_changed = _b2_last_written != (pk, cid)
    cat_changed = cats.get(pk) != cid
    if not (state_changed or cat_changed):
        return
    _b2_last_written = (pk, cid)
    cats[pk] = cid
    snapshot = dict(cats)

    def write():
        import json
        if state_changed:
            try:
                with open(B2_STATE_FILE, "w") as f:
                    f.write("%s:%s" % (pk, cid))
            except Exception:
                pass
        if cat_changed:
            try:
                with open(_B2_LAST_CATS_FILE, "w") as f:
                    json.dump(snapshot, f)
            except Exception:
                pass

    try:
        reactor.callInThread(write)
    except Exception:
        write()


_B2_LAST_CATS_FILE = DATA + "/cache/b2_last_cat.json"
_b2_last_written = None
_b2_last_cats_cache = None


def _b2_last_cats():
    """Per-provider last category, read from disk once per session."""
    global _b2_last_cats_cache
    if _b2_last_cats_cache is None:
        import json
        try:
            with open(_B2_LAST_CATS_FILE) as f:
                data = json.load(f)
            _b2_last_cats_cache = data if isinstance(data, dict) else {}
        except Exception:
            _b2_last_cats_cache = {}
    return _b2_last_cats_cache


def _last_cat_for(provider_key):
    return str(_b2_last_cats().get(str(provider_key), "") or "")


_LIVE_LAST_CHANNEL_FILE = DATA + "/cache/live_last_channel.json"


def _load_last_live_channel():
    from .last_live import load
    return load()


def _save_last_live_channel(provider_key, category_id, stream_id,
                            name="", direct_source=""):
    # name/direct_source let the channel be restarted on the next open
    # without first loading its category's channel list.
    import json
    try:
        json.dump({
            "provider_key": str(provider_key),
            "category_id": str(category_id),
            "stream_id": str(stream_id),
            "name": name or "",
            "direct_source": direct_source or "",
        }, open(_LIVE_LAST_CHANNEL_FILE, "w"))
    except Exception:
        pass

# How long a successful provider check / built category list is reused
# before OK on that provider goes back to the network again.
_B2_RECHECK_SECS = 10 * 60

# -----------------------------------------------------------------------------
# B2 snapshot — the last known category list of every provider, saved to
# disk so the next time Live TV opens, B2 (and the last provider/category)
# can be shown instantly instead of waiting on every open provider's
# server. The real lists are then refreshed quietly in the background.
# -----------------------------------------------------------------------------

_B2_SNAPSHOT_FILE = DATA + "/cache/b2_snapshot_live.json"
_B2_SNAPSHOT_FIELDS = ("source_id", "source_type", "category_id",
                       "category_name", "parent_id", "category_type")
import threading as _threading
_b2_snapshot_lock = _threading.Lock()


def _snapshot_load():
    import json
    try:
        with open(_B2_SNAPSHOT_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _snapshot_save(provider, cats):
    """Remember one provider's built category list (as shown in B2)."""
    import json
    rows = []
    for _label, data in cats or []:
        c = data.get("category")
        if c is None:
            continue
        rows.append(dict((k, getattr(c, k, "")) for k in _B2_SNAPSHOT_FIELDS))
    if not rows:
        return
    with _b2_snapshot_lock:
        snap = _snapshot_load()
        snap[str(provider.provider_key)] = rows
        tmp = _B2_SNAPSHOT_FILE + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(snap, f)
            os.replace(tmp, _B2_SNAPSHOT_FILE)
        except Exception:
            pass


def _cats_from_snapshot(rows, provider=None):
    """Rebuild B2's (label, data) category list from saved rows. The saved
    position number (source_id) is replaced with the provider's current
    one - it changes when playlists.txt is reordered."""
    from .backend.category import Category
    cats = []
    for row in rows or []:
        c = Category()
        for k in _B2_SNAPSHOT_FIELDS:
            if k in row:
                setattr(c, k, row[k])
        if provider is not None:
            c.source_id = provider.provider_id
        cats.append((c.category_name, {
            "type": "category",
            "name": c.category_name,
            "category": c,
        }))
    return cats


def _bouquet_cat_list():
    """My Bouquets' categories, read fresh from the bouquet files."""
    try:
        from .backend.bouquets import categories
        return [(c.category_name, {"type": "category", "name": c.category_name,
                                   "category": c}) for c in categories()]
    except Exception:
        return []


def _cats_signature(cats):
    return [
        (str(getattr(d.get("category"), "category_id", "")),
         getattr(d.get("category"), "category_name", ""))
        for _l, d in (cats or [])
    ]


def _load_b2_open():
    """Return stable provider keys that were open last session."""
    try:
        import json
        return json.load(open(B2_OPEN_FILE))
    except:
        return []

def _save_b2_open(open_provider_keys):
    """Persist the stable provider keys of open providers."""
    try:
        import json
        json.dump(list(open_provider_keys), open(B2_OPEN_FILE, "w"))
    except:
        pass

# -----------------------------------------------------------------------------
# Reusable listbox component
# -----------------------------------------------------------------------------

class IE2iList(GUIComponent):
    GUI_WIDGET = eListbox

    def __init__(self, item_h):
        GUIComponent.__init__(self)
        self.l = eListboxPythonMultiContent()
        self.l.setFont(0, gFont("E2iRegular", 28))
        self.l.setFont(1, gFont("E2iRegular", 24))
        self.l.setItemHeight(item_h)
        self._entries = []

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setWrapAround(False)

    def preWidgetRemove(self, instance):
        pass

    def setList(self, entries):
        self._entries = entries
        self.l.setList(entries)

    def moveToIndex(self, idx):
        if self.instance and self._entries:
            self.instance.moveSelectionTo(
                min(idx, max(0, len(self._entries) - 1))
            )

# -----------------------------------------------------------------------------
# Screen 1
# -----------------------------------------------------------------------------

class Screen1(GridMode, B2Common, B3Common, Screen):

    # B2 layout constants
    B2_X          = 100
    B2_Y          = 85
    B2_W          = 460
    B2_H          = 941
    B2_ITEM_H     = 66
    B2_TILE_ROW   = 7
    B2_ROW_OFFSET = 2
    B2_VISIBLE    = (B2_H - (B2_H % B2_ITEM_H)) // B2_ITEM_H - 1
    B2_TOTAL_ROWS = B2_VISIBLE + 4

    # B3 layout constants
    B3_X          = 565
    B3_W          = 620
    B3_ITEM_H     = 66
    B3_Y_TIME     = 381
    B3_Y_CHANNELS = 415        # 85 + 5*66 — aligns on B2 row grid
    B3_VISIBLE    = (1080 - 415) // 66   # 10 rows

    def __init__(self, session):
        Screen.__init__(self, session)
        # The plugin is open from now until this screen closes; the Menu
        # key's icon rail (player / grid) only works while it is.
        state.plugin_open = True
        self.onClose.append(self._mark_plugin_closed)

        # B2 UI state
        # B2Common configuration — this screen's B2 is Live TV
        self._b2_content    = "live"
        self._b2_cat_indent = CAT_INDENT

        self._b2_tile_row   = self.B2_TILE_ROW
        self._b2_row_offset = self.B2_ROW_OFFSET
        self._b2_visible    = self.B2_VISIBLE
        self._b2_total_rows = self.B2_TOTAL_ROWS
        self._b2_flat       = []
        self._b2_open_set   = set()    # set of open provider indices
        self._b2_open_cats  = {}       # prov_idx -> categories list
        self._b2_sel        = 0

        # B3 UI state
        self._b3_fetch_gen   = 0
        self._epg_prefetched = set()

        # ── Build skin ────────────────────────────────────────────────
        x2     = self.B2_X
        y2     = self.B2_Y
        w2     = self.B2_W
        ih     = self.B2_ITEM_H
        tile_y = y2 + self._b2_tile_row * ih

        icon_xml = ""
        for i, (icon, y) in enumerate(zip(ICONS, Y_POS)):
            icon_xml += (
                '<widget name="ic%d" position="22,%d" size="56,56"'
                ' pixmap="%s%s" zPosition="4" alphatest="blend"/>' % (
                    i, y + 22, ICON_PATH, icon)
            )

        rail_xml = ""
        for i, y in enumerate(Y_POS):
            rail_xml += (
                '<widget name="s1_rl%d" position="100,%d" size="190,56"'
                ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
                ' backgroundColor="#%s" valign="center" halign="left"'
                ' transparent="1" zPosition="4"/>' % (i, y + 22, RAIL_COLOUR))
        rail_xml += ('<widget name="s1_rtile" position="5,3000" size="290,66" cornerRadius="14"'
                     ' backgroundColor="E2iHi" zPosition="3"/>')

        b2_xml = ""
        for i in range(self._b2_total_rows):
            if i - self._b2_row_offset == self._b2_tile_row:
                continue
            ry = y2 + ((i - self._b2_row_offset) * ih)
            b2_xml += (
                '<widget name="b2_row%d" position="%d,%d" size="%d,%d"'
                ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
                ' backgroundColor="#%s" valign="center" noWrap="1" zPosition="3"/>' % (
                    i, x2 + 5, ry, w2 - 10, ih, BG_COLOUR)
            )

        self.skin = (
            '<screen name="E2iPTV_S1" position="0,0" size="1920,1080" flags="wfNoBorder">'

            '<eLabel position="0,0"   size="1920,1080" backgroundColor="E2iPanel" zPosition="0"/>'
            '<widget name="s1_rail_bg" position="0,0" size="100,1080" backgroundColor="#%s" zPosition="1"/>' % RAIL_COLOUR +
            '<widget name="b1_title" position="0,10" size="100,80"'
            ' font="E2iRegular;60" foregroundColor="#FFFFFF"'
            ' backgroundColor="#%s" valign="center" halign="center" zPosition="4"/>' % RAIL_COLOUR +
            '<widget name="s1_b2_bg" position="100,0" size="460,1080" backgroundColor="#%s" zPosition="2"/>' % BG_COLOUR +
            '<widget name="s1_sep" position="560,0" size="5,1080" backgroundColor="E2iPanel" zPosition="3"/>'
            '<widget name="s1_b3_bg" position="565,0" size="620,1080" backgroundColor="#%s" zPosition="2"/>' % BG_COLOUR +
            rail_xml +

            icon_xml +
            b2_xml +

            '<widget name="b2_tile" position="%d,%d" size="%d,%d" cornerRadius="14"'
            ' font="E2iRegular;28" foregroundColor="#000000"'
            ' backgroundColor="#%s" valign="center" noWrap="1" zPosition="4"/>' % (
                x2 + 5, tile_y, w2 - 10, ih, TILE_COLOUR) +

            '<widget source="session.VideoPicture" render="Pig"'
            ' position="584,20" size="560,315"'
            ' backgroundColor="#ff000000" transparent="1" zPosition="10"/>'
            '<widget name="s1_pig_frame" position="594,10" size="560,315" backgroundColor="transparent" zPosition="11"/>'

            # Info panel — above B5 grid, same as screen3 but clipped at screen edge
            '<widget name="s1_info_title"'
            ' position="1190,10" size="720,54"'
            ' font="E2iRegular;40" foregroundColor="#FFFFFF"'
            ' backgroundColor="E2iPanel" valign="center" zPosition="5"/>'
            '<widget name="s1_info_times"'
            ' position="1190,68" size="720,36"'
            ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
            ' backgroundColor="E2iPanel" valign="center" zPosition="5"/>'
            '<widget name="s1_info_desc"'
            ' position="1190,108" size="720,165"'
            ' font="E2iRegular;26" foregroundColor="#FFFFFF"'
            ' backgroundColor="E2iPanel" valign="top" zPosition="5"/>'

            '<widget name="b3_time"'
            ' position="%d,%d" size="%d,36"'
            ' font="E2iRegular;26" foregroundColor="#FFFFFF"'
            ' backgroundColor="#%s" valign="center" halign="center" zPosition="3"/>' % (
                self.B3_X, self.B3_Y_TIME, self.B3_W, BG_COLOUR) +

            '<widget name="b3_list"'
            ' position="%d,%d" size="%d,%d"'
            ' zPosition="3" backgroundColor="#%s"/>' % (
                self.B3_X, self.B3_Y_CHANNELS,
                self.B3_W, self.B3_VISIBLE * self.B3_ITEM_H,
                BG_COLOUR) +

            '<widget name="s1_b5_list"'
            ' position="1185,415" size="730,660"'
            ' zPosition="3" backgroundColor="E2iPanel"/>'

            # Time markers — same 30-min intervals as screen3, first 3 fit in 730px
            '<widget name="s1_b5_date"  position="1185,381" size="325,34"'
            ' font="E2iRegular;24" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
            ' valign="center" halign="left" zPosition="3"/>'
            '<widget name="s1_b5_mark1" position="1510,381" size="325,34"'
            ' font="E2iRegular;24" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
            ' valign="center" halign="left" zPosition="3"/>'
            '<widget name="s1_b5_mark2" position="1835,381" size="80,34"'
            ' font="E2iRegular;24" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
            ' valign="center" halign="left" zPosition="3"/>'

            # LAB: the EPG grid as a mode of this screen (grid_mode.py)
            '<widget name="g_bg" position="0,0" size="1920,1080" backgroundColor="E2iPanel" zPosition="8"/>' +
            grid_skin_xml() +
            '</screen>'
        )

        # ── Register widgets ──────────────────────────────────────────
        for _n in ("s1_rail_bg", "s1_b2_bg", "s1_sep", "s1_b3_bg", "s1_rtile", "s1_pig_frame"):
            self[_n] = Label("")
        for _i, _lbl in enumerate(B1_LABELS):
            self["s1_rl%d" % _i] = Label(_lbl)
        self._rail_open = False
        self._rail_saved = {}
        self._g_setup()
        self.onLayoutFinish.append(self._rail_hide_labels)
        self["b2_tile"]       = Label("")
        self["b3_time"]       = Label("")
        self["b3_list"]       = IE2iList(self.B3_ITEM_H)
        self["b1_title"]      = Label("E2")
        self["s1_b5_list"]    = IE2iList(self.B3_ITEM_H)
        self["s1_b5_date"]    = Label("")
        self["s1_b5_mark1"]   = Label("")
        self["s1_b5_mark2"]   = Label("")
        self["s1_info_title"] = Label("")
        self["s1_info_times"] = Label("")
        self["s1_info_desc"]  = Label("")

        for i in range(self._b2_total_rows):
            if i - self._b2_row_offset == self._b2_tile_row:
                continue
            self["b2_row%d" % i] = Label("")

        for i in range(len(ICONS)):
            self["ic%d" % i] = Pixmap()

        # ── Timers ────────────────────────────────────────────────────
        self._scroll_timer = eTimer()
        self._scroll_timer.callback.append(self._on_scroll_stopped)
        self._b5_timer = eTimer()
        self._b5_timer.callback.append(self._s1_epg_refresh_both)
        # =============================================================================
        # LEGACY EPG TIMER — DISABLED
        # =============================================================================
        # EPG scheduling is now owned exclusively by plugin.py EpgScheduler.
        # The old Screen1 03:00 timer could delete the persistent EPG cache and
        # compete with the real scheduler.  Retained below for rollback/reference.
        # DELETE the marked legacy method block after validation.
        # =============================================================================

        # ── Providers (non-blocking, channels load in onLayoutFinish) ──
        if not state.providers:
            # playlists_path must be restored from the saved setting
            # BEFORE providers ever load for the first time — not
            # after. _apply_provider_settings() (called later, from
            # the background _load_initial_data thread) does restore
            # it, but only updates state.playlists_path itself; it
            # never re-loads state.providers to match, since by then
            # providers already exist and it's only meant to apply
            # per-provider settings (enabled flags etc.) on top of an
            # already-loaded list, not reload the list itself. Without
            # this, the very first load below always uses whatever
            # state.py's own starting default is, regardless of
            # whatever the user actually saved via Settings -> Other.
            try:
                import json as _json
                _app = _json.load(open(DATA + '/cache/app_settings.json'))
                if "playlists_path" in _app:
                    state.playlists_path = _app["playlists_path"]
            except Exception:
                pass
            state.load_providers()

        # ── Key map ───────────────────────────────────────────────────
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "up":     self._on_up,
                "down":   self._on_down,
                "ok":     self._on_ok,
                "left":   self._on_left,
                "right":  self._on_right,
            },
            -1
        )
        # Separate map for the EPG key alone. RIGHT (_on_right above) is
        # now the primary, universal way from b2 into the full EPG grid —
        # added after diagnostic logging showed _on_epg never fires via
        # the EPG key on OpenBH no matter what context or priority is
        # used here, because that image's "EPG key behaviour" system
        # setting intercepts KEY_EPG before Enigma2's normal per-dialog
        # ActionMap dispatch even runs. This EPG-key map is kept anyway
        # since it still works fine as an equivalent shortcut on images
        # that don't do that interception (OpenATV).
        self["epg_actions"] = ActionMap(
            ["InfobarEPGActions", "EPGSelectActions", "InfobarActions"],
            {
                "epg":           self._on_epg,
                "showEPG":       self._on_epg,
                "EPGPressed":    self._on_epg,
                "showEventInfo": self._on_epg,
            },
            1
        )
        # 0 = back to whatever the mini TV is playing, full screen, with
        # B2 moved back to that channel's provider/category.
        self["rail_actions"] = ActionMap(
            ["OkCancelActions"], {"cancel": self._rail_cancel}, -1)
        self["zero_actions"] = ActionMap(
            ["NumberActions"],
            {"0": self._on_zero,
             # 5 twice quickly: show/hide Private categories
             "5": self._private_key},
            -1
        )
        # Private categories always start hidden when the plugin opens.
        from .backend.private import hide as _private_hide
        _private_hide()
        self.onLayoutFinish.append(self._layout_finished)
        self.onExecBegin.append(self._goto_check)
        # Private shown/hidden while in the grid or player: redraw B2.
        self._private_seen = False
        self.onExecBegin.append(self._private_recheck)
        # Going straight to full screen when the plugin opens (last rail
        # choice Live TV and a channel to resume) - see _goto_check.
        self._hide_until_fullscreen = (
            load_b1_last_choice() == "Live TV"
            and bool(_load_last_live_channel().get("stream_id")))
        self._layout_ready = False
        self.onShown.append(self._s1_b5_refresh)
        self.onShow.append(self._s1_b5_refresh)
        self.onShown.append(self._refresh_b2_after_settings)
        self.onShown.append(self._b3_refresh)
        self.onShow.append(self._b3_refresh)

        # ── Resume into the last real B1 destination ────────────────
        # This screen is always Live TV, which is already what's showing
        # here by default. If the last thing actually chosen before the
        # plugin last closed was Series, Movies, Recordings or Settings,
        # reopen straight
        # back into it once this screen's own layout is ready, instead
        # of silently dropping back to Live TV every time. Exit / Close
        # is never a candidate here — see b1common.save_b1_last_choice().
        self._resume_done = False
        _last_label = load_b1_last_choice()
        if _last_label in B1_LABELS:
            state.b1_sel = B1_LABELS.index(_last_label)
        if _last_label in ("Series", "Movies", "Recordings", "Settings"):
            # Cannot open another screen synchronously here:
            # onLayoutFinish fires from inside session.open(Screen1)
            # itself (via applySkin/createGUIScreen), before that call
            # has returned and before session.current_dialog is set to
            # this screen. Enigma2's "modal open allowed only from the
            # very first screen" exemption doesn't recognise this screen
            # as open yet at that point, so a nested openWithCallback()
            # here raises "Modal open are allowed only from a screen
            # which is modal!". A single-shot zero-delay timer defers
            # the actual open to the next event-loop tick, by which
            # point session.open(Screen1) has fully returned.
            self._resume_label = _last_label
            self._resume_timer = eTimer()
            self._resume_timer.callback.append(self._open_resume_on_start)
            self._resume_timer.start(0, True)

    def _open_resume_on_start(self):
        if self._resume_done:
            return
        self._resume_done = True
        self._open_destination(self._resume_label)

    def _open_destination(self, label):
        """Open whatever screen/panel a given B1 label actually means,
        using self._handle_child_close as the callback either way, so
        however it later closes gets resolved by the one shared
        handler. Used both for the startup resume and (in principle)
        anywhere else a label needs turning into an actual screen."""
        if label == "Series":
            self.session.openWithCallback(
                self._handle_child_close, MediaBrowser, "series"
            )
        elif label == "Movies":
            self.session.openWithCallback(
                self._handle_child_close, MediaBrowser, "movies"
            )
        elif label == "Recordings":
            from .recordings_screen import RecordingsScreen
            self.session.openWithCallback(
                self._handle_child_close, RecordingsScreen
            )
        elif label == "Settings":
            s2 = self.session.openWithCallback(
                self._handle_child_close, Screen2, self
            )
            try:
                s2._show_b6()
            except Exception:
                pass

    def _handle_child_close(self, *args):
        """Single shared callback for every rail-screen this screen
        opens on top of itself — Screen2 (normal '<' entry, or the
        startup resume) and Series (startup resume). Resolves the
        shared "goto:<action>" close protocol from B1Common._b1_select:
        opens whatever's needed, or does nothing further if the action
        is "live" (this screen already IS Live TV, underneath)."""

        result = args[0] if args else None

        """Screen2's Recordings panel can choose to hand off playback
        to the box's own InfoBar entirely (see screen2._recordings_play)
        rather than opening a player screen of our own — when it does,
        it stashes the file to play on state.pending_play_path/_title
        and sets state.close_to_livetv, then closes itself, as the
        signal to close this screen too. Deliberately does NOT go
        through _on_close's restore-previous-channel logic — the newly
        playing recording is the intended result here, not whatever was
        on before the plugin opened.

        Crucially, playService() for the recording is only called here,
        AFTER this screen has also closed — not from within Screen2
        before either screen closes. This screen's skin binds a live
        "Pig" preview (session.VideoPicture) that stays mounted and
        actively rendering the current service the whole time Screen2
        sits on top of it; switching the current service while that
        widget is still live caused an immediate freeze on Play (seen
        on the Pig preview itself, not the recording), so the service
        switch now only happens once nothing in our own GUI is still
        watching it change."""
        if getattr(state, 'close_to_livetv', False):
            state.close_to_livetv = False
            path  = getattr(state, 'pending_play_path', None)
            title = getattr(state, 'pending_play_title', "")
            state.pending_play_path  = None
            state.pending_play_title = None
            self.close()
            if path and os.path.exists(path):
                # Back to 4097 — both ServiceApp types tried (5002, then
                # 5001) froze the box immediately on Play, even with the
                # close-before-play reordering. 4097 is the only variant
                # that has actually started playback cleanly every time
                # so far; its problem was always seeking mid-playback,
                # never starting. Needs to be a plain eServiceReference
                # constructor call issue (wrong flags/format for
                # ServiceApp) rather than the type number itself before
                # trying ServiceApp again — see the research before the
                # next attempt.
                ref = eServiceReference(4097, 0, path)
                ref.setName(title or os.path.basename(path))
                self.session.nav.playService(ref)
            return

        if not result or not str(result).startswith("goto:"):

            # Not a "goto:" string — check for the other close payload
            # this callback can receive: Search/Favourites bubbling up
            # an actually-chosen result (see search.py's module
            # docstring for the full reasoning). Every intermediate
            # screen along the way just forwards this same tuple by
            # closing itself with it — this is the one place, always
            # safely modal since it's the root, that actually acts on
            # it.
            if (
                isinstance(result, tuple) and
                len(result) == 4 and
                result[0] == "activate"
            ):
                _marker, content_type, provider, item = result
                try:
                    with open(LOG("e2iptv_jump_debug.log"), "a") as f:
                        f.write(
                            "screen1._handle_child_close: received "
                            "activate tuple, content_type=%r item=%r "
                            "provider=%r\n" % (
                                content_type,
                                getattr(item, "name", ""),
                                getattr(provider, "name", "")
                            )
                        )
                except Exception:
                    pass
                if content_type == "series":
                    from .media_browser import MediaBrowser
                    self.session.openWithCallback(
                        self._handle_child_close,
                        MediaBrowser,
                        "series",
                        jump_to_series=item,
                        jump_to_provider=provider
                    )
                return

            # Normal close — make sure the last Live channel is playing
            # again (skipped if it already is).
            self._resume_last_live()
            return

        action = result[len("goto:"):]

        if action == "exit":
            self._on_close()
            return

        if action == "rail":
            # Left from a screen's left-most panel: back to Live TV with
            # the rail open.
            self._resume_last_live()
            self._rail_open_now()
            return

        if action == "search":
            open_search(self.session, self._handle_child_close)
            return

        if action == "favourites":
            open_favourites(self.session, self._handle_child_close)
            return

        if action == "music":
            from .music import open_music
            open_music(self.session, self._handle_child_close)
            return

        if action == "live":
            # Explicitly re-run this screen's own refreshes rather than
            # assuming it's already showing the right thing — when
            # reached via the startup resume, this screen's own async
            # initial-load chain (providers -> categories -> channels,
            # several thread hops) may still be mid-flight, or may have
            # finished without ever actually being visible to render
            # onto, since whatever was opened on top of it opened
            # almost immediately at startup.
            try:
                self._refresh_b2_after_settings()
            except Exception:
                pass
            try:
                self._b3_refresh()
            except Exception:
                pass
            try:
                self._s1_b5_refresh()
            except Exception:
                pass
            # Live TV (not EPG Grid) was picked: straight to full screen
            # on the last channel, as the 0 key does.
            from .goto import nav_log
            nav_log("LIVE TV hand-off received, live_choice=%r" % (
                getattr(state, "live_choice", None),))
            if getattr(state, "live_choice", None) == "full":
                state.live_choice = None
                reactor.callLater(0.3, self._live_fullscreen, 0)
            return

        # "series" / "recordings" / "settings" — open the destination.
        # B1_LABELS/action share the same names B1_ACTIONS maps from,
        # just inverted here since we only have the action, not the
        # label; the three actions reaching this point are exactly the
        # three _open_destination knows how to open.
        label = {
            "series":     "Series",
            "movies":     "Movies",
            "recordings": "Recordings",
            "settings":   "Settings",
        }.get(action)
        if label:
            # Remember it as where to open next time (also when reached
            # from the Menu rail, not only the normal rail).
            try:
                from .b1common import save_b1_last_choice
                save_b1_last_choice(label)
            except Exception:
                pass
            self._open_destination(label)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_up(self):
        if getattr(self, '_rail_open', False):
            self._rail_move(-1)
            return
        self._last_scroll = time.time()
        _hold_picon_repaint()
        if self._b2_sel > 0:
            self._b2_sel -= 1
            self._b2_refresh()
            self._scroll_timer.stop()
            self._scroll_timer.start(300, True)

    def _on_down(self):
        if getattr(self, '_rail_open', False):
            self._rail_move(1)
            return
        self._last_scroll = time.time()
        _hold_picon_repaint()
        if self._b2_sel < len(self._b2_flat) - 1:
            self._b2_sel += 1
            self._b2_refresh()
            self._scroll_timer.stop()
            self._scroll_timer.start(300, True)

    def _on_scroll_stopped(self):
        if not self._b2_flat:
            return
        self._b2_sel = min(self._b2_sel, len(self._b2_flat) - 1)
        label, data, is_cat = self._b2_flat[self._b2_sel]
        if not is_cat:
            return
        prov_idx, provider, cat_idx, category = self._get_sel_provider_cat()
        if category is None or provider is None:
            return
        state.open_prov     = provider
        state.open_prov_idx = prov_idx
        state.categories    = self._b2_open_cats.get(prov_idx, [])
        _save_b2_state(provider.provider_key, category.category_id)
        state.sel_cat_idx = cat_idx
        state.sel_cat     = category
        _nav_debug(
            "screen1._on_scroll_stopped: open_prov set to %r sel_cat set "
            "to %r — calling _load_channels_async" % (
                getattr(provider, 'name', None),
                getattr(category, 'category_name', None)
            )
        )
        self._load_channels_async(provider, category)

    # ------------------------------------------------------------------
    # LAB: the rail opened in place - no second screen
    #
    # The background panels are named widgets (they were fixed eLabels),
    # so everything right of the rail can slide RAIL_SHIFT pixels across,
    # the mini TV too: moving its video window moves the picture, and its
    # stored Position keeps it there after a pop-up (Pig.onShow).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # LAB: the EPG grid as a mode of this screen
    #
    # The grid (grid_mode.py, made from screen3.py) has its own widgets
    # (g_ names) over a full-screen background (g_bg) that covers the list
    # layout. Entering switches the keys over to the grid's own, moves the
    # mini TV top left and runs what the grid screen did when it opened;
    # leaving undoes it and does what closing the grid screen did.
    # ------------------------------------------------------------------

    _G_WIDGETS = (["g_bg", "g_info_title", "g_info_times", "g_info_prov",
                   "g_info_desc", "g_b5_date", "g_b3_list", "g_b5_list",
                   "g_smenu_bg", "g_smenu_hdr", "g_smenu_sel"]
                  + ["g_b5_mark%d" % _i for _i in range(4)]
                  + ["g_smenu_t%d" % _i for _i in range(_G_SMENU_ROWS)])
    _LIST_MAPS = ("actions", "epg_actions", "zero_actions", "rail_actions")

    def _g_setup(self):
        self._grid_mode = False
        for n in self._G_WIDGETS:
            if n not in ("g_b3_list", "g_b5_list"):
                self[n] = Label("")
        self["g_b3_list"] = _GridList(_G_ITEM_H)
        self["g_b5_list"] = _GridList(_G_ITEM_H)
        self["g_actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions",
             "InfobarEPGActions", "EPGSelectActions",
             "InfobarActions", "InfobarInstantRecord",
             "NumberActions"],
            {
                "cancel":        self._g_on_cancel,
                "ok":            self._g_on_ok,
                "up":            self._g_on_up,
                "down":          self._g_on_down,
                "left":          self._g_on_left,
                "right":         self._g_on_right,
                "epg":           self._g_on_epg_key,
                "showEPG":       self._g_on_epg_key,
                "EPGPressed":    self._g_on_epg_key,
                "showEventInfo": self._g_on_epg_key,
                "0":             self._g_on_epg_key,
                "5":             self._g_private_key,
                "record":        self._g_on_ok,
                "instantRecord": self._g_on_ok,
            },
            -1
        )
        self["g_actions"].setEnabled(False)
        self._g_menu_key_handler = self._g_on_raw_menu_key
        self._g_epg_debounce = eTimer()
        self._g_epg_debounce.callback.append(self._g_epg_debounce_fired)
        self._g_refresh_timer = eTimer()
        self._g_refresh_timer.callback.append(self._g_refresh_tick)
        self._g_pig_saved = None
        self.onLayoutFinish.append(self._g_hide_all)
        self.onShow.append(self._g_on_show_hook)
        self.onClose.append(self._g_on_close)

    def _g_hide_all(self):
        for n in self._G_WIDGETS:
            try:
                self[n].hide()
            except Exception:
                pass

    def _g_on_show_hook(self):
        if self._grid_mode:
            self._g_on_shown()

    def _g_on_close(self):
        try:
            self._g_refresh_timer.stop()
            self._g_epg_debounce.stop()
        except Exception:
            pass
        # only let go of the MENU/EPG keys if the grid actually holds them
        # (unbinding when not bound logs an "illegal python reference")
        if getattr(self, "_g_key_bound", False):
            self._g_key_bound = False
            self._g_unbind_menu_key()

    def _g_pig_place(self, grid):
        pig = self._rail_pig()
        frame = None
        try:
            frame = self["s1_pig_frame"].instance
        except Exception:
            pass
        if grid:
            try:
                self._g_pig_saved = (pig.instance.position() if pig else None,
                                     frame.position() if frame else None)
            except Exception:
                self._g_pig_saved = None
            targets = (ePoint(50, 20), ePoint(40, 10))
        else:
            if not self._g_pig_saved:
                return
            targets = self._g_pig_saved
            self._g_pig_saved = None
        try:
            if pig is not None and targets[0] is not None:
                pig.Position = targets[0]
                pig.instance.move(targets[0])
            if frame is not None and targets[1] is not None:
                frame.move(targets[1])
        except Exception:
            pass

    def _g_enter(self):
        if self._grid_mode or not state.channels:
            return
        if getattr(self, "_rail_open", False):
            self._rail_close()
        self._grid_mode = True
        # what the grid screen's constructor set up each time it opened
        self._g_focused = 0
        self._g_scroll = 0
        self._g_is_historical = False
        now = int(time.time())
        self._g_time_base = now
        self._g_cursor_time = now
        self._g_window_start = now
        self._g_menu_active = False
        self._g_menu_sel = 0
        self._g_custom_active = False
        self._g_custom_sel = 0
        self._g_custom_start = 0
        self._g_custom_end = 0
        self._g_repeat_active = False
        self._g_repeat_sel = 0
        self._g_repeat_days = [False] * 7
        self._g_last_event_id = None
        self._g_last_now_slot = None
        self._g_move_active = False
        for n in self._LIST_MAPS:
            try:
                self[n].setEnabled(False)
            except Exception:
                pass
        self["g_actions"].setEnabled(True)
        try:
            _eActionMap.getInstance().bindAction("", -0x7FFFFFFF, self._g_menu_key_handler)
            self._g_key_bound = True
        except Exception:
            pass
        for n in self._G_WIDGETS:
            try:
                self[n].show()
            except Exception:
                pass
        self._g_hide_ctx()
        self._g_pig_place(True)
        self._g_on_ready()
        self._g_goto_check()
        self._g_on_shown()

    def _g_leave(self):
        if not self._grid_mode:
            return
        for check, close in (("_g_repeat_active", self._g_hide_repeat_panel),
                             ("_g_custom_active", self._g_hide_custom),
                             ("_g_menu_active", self._g_hide_ctx)):
            if getattr(self, check, False):
                try:
                    close()
                except Exception:
                    pass
        if getattr(self, "_g_move_active", False):
            try:
                self._g_finish_move(save=False)
            except Exception:
                pass
        self._grid_mode = False
        self._g_on_close()
        self["g_actions"].setEnabled(False)
        for n in self._LIST_MAPS:
            try:
                self[n].setEnabled(True)
            except Exception:
                pass
        self._g_hide_all()
        self._g_pig_place(False)
        self._screen3_closed()
        # as when the grid screen closed and this one became active again
        reactor.callLater(0, self._goto_check)

    RAIL_SHIFT = 200
    _RAIL_FIXED = ("ic", "s1_rl", "s1_rtile", "s1_rail_bg", "b1_title")

    def _rail_hide_labels(self):
        for i in range(len(B1_LABELS)):
            try:
                self["s1_rl%d" % i].hide()
            except Exception:
                pass

    def _rail_widgets(self):
        out = []
        for name in list(self.keys()):
            if name.startswith(self._RAIL_FIXED):
                continue
            try:
                inst = self[name].instance
            except Exception:
                inst = None
            if inst is not None:
                out.append((name, inst))
        return out

    def _rail_pig(self):
        for r in getattr(self, "renderer", []) or []:
            if r.__class__.__name__ == "Pig" and r.instance is not None:
                return r
        return None

    def _rail_slide(self, dx):
        # Sliding right pushes the right-hand column (programme info, the
        # small guide) past the screen edge: those are narrowed to fit while
        # the rail is open - text wraps instead of running off - and get
        # their own width back when it closes.
        saved = self._rail_saved
        for name, inst in self._rail_widgets():
            try:
                p = inst.position()
                nx = p.x() + dx
                inst.move(ePoint(nx, p.y()))
                if dx > 0:
                    sz = inst.size()
                    room = 1910 - nx
                    if nx + sz.width() > 1920 and room >= 60:
                        saved[name] = (sz.width(), sz.height())
                        inst.resize(eSize(room, sz.height()))
                elif name in saved:
                    w, h = saved.pop(name)
                    inst.resize(eSize(w, h))
            except Exception:
                pass
        pig = self._rail_pig()
        if pig is not None:
            try:
                p = pig.instance.position()
                pig.Position = ePoint(p.x() + dx, p.y())
                pig.instance.move(pig.Position)
            except Exception:
                pass

    def _rail_open_now(self):
        if self._rail_open:
            return
        self._rail_open = True
        self._rail_slide(self.RAIL_SHIFT)
        try:
            self["s1_rail_bg"].instance.resize(eSize(100 + self.RAIL_SHIFT, 1080))
            self["b1_title"].instance.resize(eSize(100 + self.RAIL_SHIFT, 80))
            self["b1_title"].setText("E2iPTV Lab")
        except Exception:
            pass
        for i in range(len(B1_LABELS)):
            try:
                self["s1_rl%d" % i].show()
            except Exception:
                pass
        self._rail_sel = min(max(getattr(state, "b1_sel", 1) or 1, 0), len(B1_LABELS) - 1)
        self._rail_draw()

    def _rail_close(self):
        if not self._rail_open:
            return
        self._rail_open = False
        try:
            self["s1_rtile"].instance.move(ePoint(5, 3000))
            self["s1_rail_bg"].instance.resize(eSize(100, 1080))
            self["b1_title"].instance.resize(eSize(100, 80))
            self["b1_title"].setText("E2")
        except Exception:
            pass
        self._rail_hide_labels()
        self._rail_slide(-self.RAIL_SHIFT)

    def _rail_draw(self):
        try:
            self["s1_rtile"].instance.move(ePoint(5, Y_POS[self._rail_sel] + 17))
        except Exception:
            pass
        for i in range(len(B1_LABELS)):
            try:
                self["s1_rl%d" % i].instance.setForegroundColor(
                    gRGB(0x000000) if i == self._rail_sel else gRGB(0xffffff))
            except Exception:
                pass

    def _rail_move(self, d):
        self._rail_sel = (self._rail_sel + d) % len(B1_LABELS)
        state.b1_sel = self._rail_sel
        self._rail_draw()

    def _rail_cancel(self):
        if self._rail_open:
            self._rail_close()

    def _rail_ok(self):
        from .b1common import B1_ACTIONS, save_b1_last_choice
        label = B1_LABELS[self._rail_sel]
        action = B1_ACTIONS.get(label)
        self._rail_close()
        if not action:
            return
        if label == "EPG Grid":
            save_b1_last_choice(label)
            self._on_epg()
            return
        state.live_choice = "full" if label == "Live TV" else None
        if action not in ("exit", "live"):
            save_b1_last_choice(label)
        self._handle_child_close("goto:%s" % action)

    def _on_left(self):
        if not getattr(self, "_rail_open", False):
            self._rail_open_now()
        return

    def _on_left_old(self):
        _nav_debug(
            "screen1._on_left: opening Screen2 — open_prov=%r sel_cat=%r" % (
                getattr(getattr(state, 'open_prov', None), 'name', None),
                getattr(getattr(state, 'sel_cat', None), 'category_name', None)
            )
        )
        self.session.openWithCallback(self._handle_child_close, Screen2, self)

    def _mark_plugin_closed(self):
        state.plugin_open = False

    def _on_close(self):
        # Closing from here on: the full-screen player's freeze watch
        # stands down before the channel is switched back.
        state.plugin_open = False
        self._live_closed = True
        self._prefetch_stop = True
        self._epg_queue = []
        try:
            pass    # the playing channel is saved when it starts (last_live.py)
        except Exception:
            pass
        """Put back the box's own channel that was playing when the plugin
        opened - but only a real TV channel (satellite/cable/terrestrial).
        If that was an internet stream (e.g. the last IPTV channel the box
        remembered from before a restart), leave the channel you're
        watching now playing instead of jumping back to an old one."""
        try:
            prev = getattr(state, 'prev_service_ref', None)
            if prev and prev.valid():
                text = prev.toString().lower()
                is_stream = ("http%3a" in text or "https%3a" in text
                             or "http://" in text or "https://" in text
                             or text.split(":", 1)[0] in ("4097", "5001", "5002"))
                if not is_stream:
                    self.session.nav.playService(prev)
            state.prev_service_ref = None
        except Exception:
            pass
        self.close()

    def _resume_last_live(self):
        """Play the Live channel saved in live_last_channel.json, unless
        it is already what's playing. Network work happens off the GUI
        thread; playService() happens back on it."""
        last = _load_last_live_channel()
        if not last.get("provider_key") or not last.get("stream_id"):
            return
        try:
            cur = self.session.nav.getCurrentlyPlayingServiceReference()
            if (cur is not None and getattr(self, "_live_preview_url", None)
                    and cur.getPath() == self._live_preview_url):
                return
        except Exception:
            pass
        # Fast path: everything needed is in the saved file, so there's
        # no need to load the channel's category first (that could mean
        # a server request just to get the list).
        provider = next(
            (p for _n, p in state.providers
             if str(p.provider_key) == str(last.get("provider_key"))
             and _shows(p, 'live')
             # never reopen on a Private channel while Private is hidden
             and str(last.get("category_id", "")) not in _private_hidden(p, "live")),
            None)
        if (provider is not None and last.get("name")
                and not getattr(provider, "is_bouquet", False)):
            from .backend.live_channel import LiveChannel
            ch = LiveChannel()
            ch.provider_key = str(provider.provider_key)
            ch.stream_id = str(last.get("stream_id", ""))
            ch.name = last.get("name", "")
            ch.direct_source = last.get("direct_source", "")
            if provider.source_type != "STALKER":
                # Xtream: the address is just text — play right now.
                from .player import build_live_url
                self._play_live_preview(ch, build_live_url(provider, ch))
                return
            if ch.direct_source:
                # Stalker: one create_link request, nothing else.
                run_now(self._resume_stalker_bg, provider, ch)
                return

        run_now(self._resume_last_live_bg, last)

    def _resume_stalker_bg(self, provider, channel):
        try:
            from .player import build_live_url
            url = build_live_url(provider, channel)
        except Exception as e:
            print("[E2iPTV] resume last Live channel failed:", e)
            return
        if url:
            reactor.callFromThread(self._play_live_preview, channel, url)

    def _resume_last_live_bg(self, last):
        pkey = str(last.get("provider_key", ""))
        cid = str(last.get("category_id", ""))
        sid = str(last.get("stream_id", ""))
        try:
            prov_idx, provider = next(
                (i, p) for i, (_n, p) in enumerate(state.providers)
                if str(p.provider_key) == pkey and _shows(p, 'live')
                and cid not in _private_hidden(p, "live")
            )
        except StopIteration:
            return

        # The channel's category: from B2 if that provider is open,
        # else from the saved snapshot.
        category = None
        for cats in (self._b2_open_cats.get(prov_idx),
                     _cats_from_snapshot(_snapshot_load().get(pkey), provider)):
            for _l, d in cats or []:
                c = d.get("category")
                if c is not None and str(c.category_id) == cid:
                    category = c
                    break
            if category is not None:
                break
        if category is None:
            return

        try:
            channels = state.backend.get_live_channels(provider, category)
            channel = next(
                (c for c in channels if str(getattr(c, "stream_id", "")) == sid),
                None)
            if channel is None:
                return
            from .player import build_live_url
            url = build_live_url(provider, channel)
        except Exception as e:
            print("[E2iPTV] resume last Live channel failed:", e)
            return
        if url:
            reactor.callFromThread(self._play_live_preview, channel, url)

    def _play_live_preview(self, channel, url):
        if getattr(self, "_live_closed", False):
            return          # user already left Live TV — don't override
        try:
            from .player import make_live_ref
            ref = make_live_ref(url, channel.name)     # Settings -> Playback
            self.session.nav.playService(ref)
            self._live_preview_url = url
            # So the 0 key knows what the mini TV is showing.
            last = _load_last_live_channel()
            state.last_play_chan = channel
            state.last_play_provider = next(
                (p for _n, p in state.providers
                 if str(p.provider_key) == str(last.get("provider_key"))), None)
            state.last_play_cat_id = str(last.get("category_id", "") or "")
            state.last_play_channels = None
        except Exception as e:
            print("[E2iPTV] resume last Live channel: playService failed:", e)

    def _goto_check(self):
        """Became the active screen again: if the player's Menu asked
        for another section, open it here, as the rail would (goto.py)."""
        if getattr(self, "_grid_mode", False):
            self._g_goto_check()
            return
        # Going straight to full screen (Live TV picked from a rail or the
        # Menu rail, or the plugin opening with Live TV as last choice):
        # if the channel is already playing, open the full-screen player
        # right here. Opening a screen while this one is becoming active
        # makes the box skip showing it, so it never flashes up first.
        # (Hiding it doesn't work: the box shows it again straight after.)
        if (getattr(state, "live_choice", None) == "full"
                or getattr(self, "_hide_until_fullscreen", False)):
            try:
                cur = self.session.nav.getCurrentlyPlayingServiceReference()
                url = cur.getPath() if cur is not None else ""
            except Exception:
                url = ""
            if url:
                from .goto import nav_log
                nav_log("FULLSCREEN opened at once (no Live TV screen shown)")
                state.live_choice = None
                self._hide_until_fullscreen = False
                from .goto import take as _take
                _take()             # the hand-off is done
                self._on_zero()
                return
        # EPG Grid picked (from any rail or the Menu rail): straight into
        # the grid on the last channel's category.
        if getattr(state, "live_choice", None) == "grid":
            state.live_choice = None
            if self._open_grid_for_last():
                return
        # Back from the full-screen picture opened above: this screen was
        # hidden when it opened, so the box won't show it by itself.
        if not getattr(self, "shown", True):
            try:
                self.show()
            except Exception:
                pass
        from .goto import take
        action = take()
        if action is None:
            return
        from .goto import nav_log
        nav_log("LIVE TV active again, Menu asked for %r" % action)
        reactor.callLater(0, self._handle_child_close, "goto:%s" % action)

    def _startup_grid(self, tries):
        """Plugin opening with EPG Grid as the last choice: wait (up to
        about 10 seconds) for the last channel's category to load, then
        open the grid on it."""
        try:
            if self.session.current_dialog is not self:
                return
        except Exception:
            return
        if self._open_grid_for_last(load=(tries == 6)):
            return
        if tries < 20:
            reactor.callLater(0.5, self._startup_grid, tries + 1)

    def _open_grid_for_last(self, load=True):
        """Open the EPG grid on the category of the last channel played,
        with that channel highlighted. If that category's channels are
        already loaded the grid opens at once (so this screen never
        shows); otherwise they're loaded first and the grid opens as soon
        as they're in. Returns True if the grid was opened at once."""
        from .goto import nav_log
        last = _load_last_live_channel()
        pkey = str(last.get("view_provider_key") or last.get("provider_key") or "")
        cid = str(last.get("view_category_id") or last.get("category_id") or "")
        sid = str(last.get("stream_id") or "")
        prov = next((p for _n, p in state.providers
                     if str(getattr(p, "provider_key", "")) == pkey), None)
        if prov is None or not cid:
            nav_log("EPG GRID: no last channel/category - showing Live TV")
            return False
        now = int(time.time())
        loaded = (state.open_prov is prov and state.channels
                  and str(getattr(state.sel_cat, "category_id", "")) == cid)
        if loaded:
            idx = next((i for i, c in enumerate(state.channels)
                        if str(getattr(c, "stream_id", "")) == sid), 0)
            state.b5_focused = idx
            state.b5_scroll = max(0, idx - 4)
            state.b5_cursor_time = now
            state.b5_window_start = (now // 1800) * 1800 - 1800
            nav_log("EPG GRID opened at once")
            self._on_epg()
            return True
        if not load:
            return False
        nav_log("EPG GRID: loading that category first")
        self._b2_jump_to(prov, cid)
        if str(getattr(state.sel_cat, "category_id", "")) == cid:
            self._grid_after_load = state.sel_cat
        return False

    def _end_fullscreen_wait(self, opened):
        """Stop keeping this screen hidden; show it again if the
        full-screen picture didn't open after all."""
        self._hide_until_fullscreen = False
        if not opened:
            try:
                self.show()
            except Exception:
                pass

    def _live_fullscreen(self, tries, max_tries=10):
        """Full screen on the channel in the mini TV (as 0 does). If
        nothing is playing yet (e.g. just after a film), start the last
        channel first (tries == 0) and wait for it, half a second per try
        (about 5 seconds; 10 when opening the plugin)."""
        from .goto import nav_log
        try:
            front = self.session.current_dialog
            cur = self.session.nav.getCurrentlyPlayingServiceReference()
            url = cur.getPath() if cur is not None else ""
        except Exception:
            front, url = None, ""
        nav_log("FULLSCREEN try %d/%d front=%s playing=%s" % (
            tries, max_tries,
            front.__class__.__name__ if front is not None else "?",
            "yes" if url else "nothing"))
        if front is not self:
            self._hide_until_fullscreen = False
            return              # something else opened meanwhile
        if url:
            nav_log("FULLSCREEN opening player")
            self._end_fullscreen_wait(True)
            self._on_zero()
            return
        if tries == 0:
            self._resume_last_live()
        if tries < max_tries:
            reactor.callLater(0.5, self._live_fullscreen, tries + 1, max_tries)
        else:
            nav_log("FULLSCREEN gave up - showing Live TV")
            self._end_fullscreen_wait(False)

    def _private_recheck(self):
        from .backend.private import shown
        if shown() != self._private_seen:
            self._private_seen = shown()
            try:
                self._private_changed()
            except Exception:
                pass

    def _private_changed(self):
        from .backend.private import shown
        self._private_seen = shown()
        B2Common._private_changed(self)

    def _on_zero(self):
        """0: go full screen on whatever the mini TV is playing, and move
        B2 back to that channel's provider and category."""
        cur = self.session.nav.getCurrentlyPlayingServiceReference()
        url = cur.getPath() if cur is not None else ""
        if not url:
            return
        chan = getattr(state, "last_play_chan", None)
        prov = getattr(state, "last_play_provider", None)
        cid = str(getattr(state, "last_play_cat_id", "") or "")
        title = cur.getName() or getattr(chan, "name", "")
        chans = None

        # Last played from My Bouquets: go back into that bouquet (its grid
        # position and channel list), not the channel's own provider. The
        # stream itself still comes from the real provider.
        ctx = self._bouquet_context(chan)
        if ctx is not None:
            prov, cid, chans, chan = ctx

        if chan is not None and prov is not None:
            chans = list(chans or getattr(state, "last_play_channels", None) or [chan])
            self._b2_jump_to(prov, cid)
            state.open_prov = prov
            state.channels = chans
            idx = next((i for i, c in enumerate(chans)
                        if str(c.stream_id) == str(chan.stream_id)), None)
        else:
            idx = None

        # Same address as the mini TV, so Enigma2 keeps the stream running
        # (no re-tune, no extra portal request) and just goes full screen.
        from .player import E2iPTVPlayer
        self.session.open(E2iPTVPlayer, url, title, channel_idx=idx)

    def _bouquet_context(self, chan):
        """(bouquet provider, bouquet name, its channels, this channel) if
        the last live channel was played from My Bouquets and is the one
        in the mini TV; otherwise None."""
        try:
            last = _load_last_live_channel()
            if last.get("view_provider_key") != "bouquets":
                return None
            sid = str(last.get("stream_id", ""))
            if chan is not None and str(getattr(chan, "stream_id", "")) != sid:
                return None         # something else is playing now
            bq_prov = next((p for _n, p in state.providers
                            if getattr(p, "is_bouquet", False)), None)
            if bq_prov is None:
                return None
            from .backend import bouquets as _bq
            name = str(last.get("view_category_id", ""))
            cat = next((c for c in _bq.categories()
                        if str(c.category_id) == name), None)
            if cat is None:
                return None
            chans = _bq.channels(cat, state.providers)
            pkey = str(last.get("provider_key", ""))
            match = next((c for c in chans
                          if str(c.stream_id) == sid
                          and str(getattr(c, "provider_key", "")) == pkey), None)
            if match is None:
                return None
            return bq_prov, name, chans, match
        except Exception as e:
            print("[E2iPTV] bouquet context:", e)
            return None

    def _b2_jump_to(self, provider, category_id):
        """Open `provider` in B2 if needed and select `category_id`."""
        try:
            prov_idx = next(i for i, (_n, p) in enumerate(state.providers)
                            if p is provider)
        except StopIteration:
            return
        if prov_idx not in self._b2_open_set:
            cats = self._saved_cats_for(provider, prov_idx)
            if not cats:
                return
            self._b2_open_cats[prov_idx] = cats
            self._b2_open_set.add(prov_idx)
            self._b2_rebuild_flat()
        for row, (_l, data, is_cat) in enumerate(self._b2_flat):
            cat = data.get("category") if is_cat else None
            if (cat is not None and data.get("provider_idx") == prov_idx
                    and str(cat.category_id) == category_id):
                self._b2_sel = row
                cats = self._b2_open_cats.get(prov_idx, [])
                state.open_prov_idx = prov_idx
                state.categories = cats
                state.sel_cat = cat
                state.sel_cat_idx = next(
                    (i for i, (_x, d) in enumerate(cats) if d.get("category") is cat), 0)
                _save_b2_state(provider.provider_key, cat.category_id)
                self._b2_refresh()
                # Refill the channel preview for that category behind the
                # player, so it's right when you come back.
                self._load_channels_async(provider, cat)
                return
        self._b2_refresh()

    _notice_shown = False

    def _startup_notice(self):
        """Once per session: say if there are no providers yet, or no
        hard disk / USB stick to keep the plugin's data on."""
        if Screen1._notice_shown or getattr(self, "_live_closed", False):
            return
        Screen1._notice_shown = True
        from Screens.MessageBox import MessageBox
        from .paths import NO_DISK, HDD
        notes = []
        real = [p for _n, p in (state.providers or [])
                if not getattr(p, "is_bouquet", False)]
        if not real:
            notes.append(
                "No providers yet. Add your provider lines to\n%s\n"
                "(it has examples showing the format), then restart the GUI."
                % state.playlists_path)
        if NO_DISK:
            notes.append(
                "No hard disk or USB stick found, so E2iPTV is keeping its "
                "data in memory (%s). It will be lost at every restart.\n"
                "Fit a disk or USB stick, or choose a folder in Settings -> "
                "Other -> Data Folder." % HDD)
        if notes:
            try:
                self.session.open(_E2iBox, "\n\n".join(notes),
                                  MessageBox.TYPE_INFO)
            except Exception:
                pass

    def _on_right(self):
        if getattr(self, '_rail_open', False):
            self._rail_close()
            return
        # Was a no-op; now the primary way from b2 into the full EPG grid
        # (see the ActionMap comments above) — unused for anything else in
        # this pane, and doesn't touch the grid's own left/right scrolling
        # or its EPG-key "jump to now" binding once you're inside it.
        _nav_debug(
            "screen1._on_right: calling _on_epg — open_prov=%r "
            "channels_len=%r" % (
                getattr(getattr(state, 'open_prov', None), 'name', None),
                len(state.channels) if state.channels else 0
            )
        )
        self._on_epg()


    def _on_epg(self):
        # Temporary diagnostic: confirms whether the EPG key handler
        # actually fires at all on this image, and whether state.channels
        # is populated at the moment it fires — narrows down a reported
        # "b2 -> full EPG grid" transition failure on OpenBH (safe to
        # remove once confirmed).
        _nav_debug(
            "screen1._on_epg: open_prov=%r channels_len=%r "
            "first_channel=%r sel_cat=%r" % (
                getattr(getattr(state, 'open_prov', None), 'name', None),
                len(state.channels) if state.channels else 0,
                getattr(state.channels[0], 'name', None) if state.channels else None,
                getattr(getattr(state, 'sel_cat', None), 'category_name', None)
            )
        )
        try:
            with open(LOG("e2iptv_epg_key.log"), "a") as _lg:
                _lg.write("%s _on_epg fired, state.channels=%r\n" %
                          (time.strftime("%Y-%m-%d %H:%M:%S"), bool(state.channels)))
        except Exception:
            pass
        if state.channels:
            try:
                self._g_enter()
            except Exception:
                import traceback
                try:
                    with open(LOG("e2iptv_epg_key.log"), "a") as _lg:
                        _lg.write("Screen3 OPEN ERROR:\n" + traceback.format_exc() + "\n")
                except Exception:
                    pass


    def _screen3_closed(self, *args):
        self._b3_refresh()
        self._s1_b5_refresh()

    def _provider_check_async(self, provider, prov_idx):
        """Check one provider only after OK is pressed."""

        def worker():
            try:
                result = state.backend.check_provider(provider)
            except Exception as e:
                print(
                    "[E2iPTV] Provider check exception:",
                    getattr(provider, "name", ""),
                    e
                )
                result = False

            reactor.callFromThread(
                self._provider_check_finished,
                provider,
                prov_idx,
                result
            )

        run_now(worker)

    def _provider_check_finished(self, provider, prov_idx, result):
        """Handle provider check result on the Enigma2 GUI thread."""

        if result:
            provider.online = True
            provider._last_ok_check = time.time()

            print(
                "[E2iPTV] Provider check OK:",
                getattr(provider, "name", "")
            )

            # Immediately repaint B2 so a previously red provider
            # returns to normal without selecting another row.
            self._b2_rebuild_flat()

            self._b2_refresh()

            self._provider_ok_after_check(
                provider,
                prov_idx
            )

            return

        provider.online = False

        print(
            "[E2iPTV] Provider check FAILED:",
            getattr(provider, "name", "")
        )

        # Immediately repaint B2 while the failed provider is still
        # the selected provider.
        self._b2_rebuild_flat()

        flat_idx = next(
            (
                i for i, (l, d, ic)
                in enumerate(self._b2_flat)
                if not ic and
                d.get("provider") is provider
            ),
            self._b2_sel
        )

        self._b2_sel = flat_idx
        self._b2_refresh()

    def _provider_ok_after_check(self, provider, prov_idx):
        if not getattr(provider, "is_bouquet", False):
            _cat_log(provider, "OK pressed (%s, %s)" % (
                "open - closing it" if prov_idx in self._b2_open_set else "closed - opening it",
                "online" if getattr(provider, "online", True) is not False else "red"))
        try:
            self._provider_open_or_close(provider, prov_idx)
        except Exception as e:
            import traceback
            _cat_log(provider, "ERROR: %r | %s" % (
                e, traceback.format_exc().strip().replace("\n", " | ")[-900:]))

    def _provider_open_or_close(self, provider, prov_idx):

        if prov_idx in self._b2_open_set:
            # Already open: preserve the existing B2 toggle behaviour.
            self._b2_open_set.discard(prov_idx)
            self._b2_open_cats.pop(
                prov_idx,
                None
            )

            _save_b2_open(
                [
                    state.providers[i][1].provider_key
                    for i in self._b2_open_set
                ]
            )

            self._b2_rebuild_flat()

            flat_idx = next(
                (
                    i for i, (l, d, ic)
                    in enumerate(self._b2_flat)
                    if not ic and
                    d.get("provider") is provider
                ),
                0
            )

            self._b2_sel = flat_idx
            self._b2_refresh()
            _nav_debug(
                "screen1._provider_ok_after_check: provider %r already "
                "open — TOGGLING CLOSED, open_prov/state.channels "
                "UNCHANGED (still %r)" % (
                    getattr(provider, 'name', None),
                    getattr(getattr(state, 'open_prov', None), 'name', None)
                )
            )
            return

        # Show the provider's categories straight away from the last
        # list we have (this session's cache, else the on-disk snapshot)
        # and check the server in the background. Only a provider never
        # opened before has to wait for the server — and even then the
        # wait happens off the GUI thread.
        try:
            cats = self._saved_cats_for(provider, prov_idx)
        except Exception as e:
            _cat_log(provider, "saved list unreadable (%r) - asking the server" % (e,))
            cats = []
        if not getattr(provider, "is_bouquet", False):
            _cat_log(provider, "OK pressed: %d from the saved list%s" % (
                len(cats), "" if cats else " - asking the server"))
        if cats:
            self._apply_provider_cats(provider, prov_idx, cats)
            # Checked in the last 10 minutes: no need to ask again.
            if time.time() - getattr(provider, "_last_ok_check", 0) >= _B2_RECHECK_SECS:
                reactor.callInThread(self._refresh_one_provider_bg, provider, prov_idx)
        else:
            # No saved list: wait for the server - but not for ever. A
            # server that never answers used to leave the provider looking
            # fine with nothing opening; after 25 s it's shown red.
            provider._cats_wait = time.time()
            run_now(self._fetch_provider_cats_bg, provider, prov_idx)
            reactor.callLater(25, self._cats_wait_check, provider, provider._cats_wait)

    def _cats_wait_check(self, provider, started):
        if getattr(provider, "_cats_wait", None) != started:
            return                      # the answer came in meanwhile
        provider._cats_wait = None
        try:
            from .paths import LOG
            with open(LOG("e2iptv_xtream.log"), "a") as f:
                f.write("%s %s categories: no answer after 25 s - shown red\n" % (
                    time.strftime("%H:%M:%S"), getattr(provider, "name", "?")))
        except Exception:
            pass
        self._mark_provider_offline(provider)

    def _pick_category(self, provider, cats, preferred_id=""):
        """Index of the category to show for this provider: the one
        remembered for it if it's still there and not hidden, otherwise
        the first category actually shown in B2 (disabled categories
        are hidden in B2, and loading one would show an empty list)."""
        if not hasattr(provider, "disabled_cats"):
            try:
                provider.disabled_cats = self._load_disabled_cats(provider)
            except Exception:
                provider.disabled_cats = set()
        disabled = set(str(x) for x in (provider.disabled_cats or ()))
        disabled |= _private_hidden(provider, "live")
        first_visible = None
        for i, (_label, data) in enumerate(cats):
            cat = data.get("category")
            cid = str(getattr(cat, "category_id", ""))
            if cid in disabled:
                continue
            if preferred_id and cid == str(preferred_id):
                return i
            if first_visible is None:
                first_visible = i
        return first_visible if first_visible is not None else 0

    def _saved_cats_for(self, provider, prov_idx):
        # My Bouquets: always the current list of bouquet files (cheap,
        # no server) — a saved copy would miss bouquets made since.
        if getattr(provider, "is_bouquet", False):
            return _bouquet_cat_list()
        cache = self.__dict__.setdefault("_b2_cats_cache", {})
        hit = cache.get((prov_idx, getattr(self, "_b2_content", "live")))
        if hit and time.time() - hit[0] < _B2_RECHECK_SECS:
            return hit[1]
        return _cats_from_snapshot(_snapshot_load().get(str(provider.provider_key)), provider)

    def _fetch_cats_now(self, provider, prov_idx):
        """Background thread only: ask the server for this provider's
        category list and remember it. Returns [] on failure."""
        t0 = time.time()
        try:
            raw = self._get_b2_categories(provider)
            cats = self._cats_to_list(raw)
            _cat_log(provider, "server sent %d categories, %d kept (%.1f s)%s" % (
                len(raw or []), len(cats), time.time() - t0,
                "" if not raw else " [first from provider no. %s, this is no. %s]" % (
                    getattr(raw[0], "source_id", "?"), getattr(provider, "provider_id", "?"))))
        except Exception as e:
            print("[E2iPTV] LiveTV category fetch failed:", e)
            _cat_log(provider, "fetch FAILED after %.1f s: %s" % (time.time() - t0, e))
            cats = []
        if cats:
            cache = self.__dict__.setdefault("_b2_cats_cache", {})
            cache[(prov_idx, getattr(self, "_b2_content", "live"))] = (time.time(), cats)
            provider._last_ok_check = time.time()
            _snapshot_save(provider, cats)
        return cats

    def _fetch_provider_cats_bg(self, provider, prov_idx):
        cats = self._fetch_cats_now(provider, prov_idx)
        reactor.callFromThread(self._apply_provider_cats, provider, prov_idx, cats)

    def _refresh_one_provider_bg(self, provider, prov_idx):
        cats = self._fetch_cats_now(provider, prov_idx)
        if cats:
            provider.online = True
            reactor.callFromThread(self._apply_bg_category_refresh, [(prov_idx, cats)])
        else:
            reactor.callFromThread(self._mark_provider_offline, provider)

    def _mark_provider_offline(self, provider):
        """Background check failed: show the provider red, the same as
        a failed OK check always has, keeping the highlight on it."""
        provider.online = False
        self._b2_rebuild_flat()
        self._b2_sel = next(
            (i for i, (l, d, ic) in enumerate(self._b2_flat)
             if not ic and d.get("provider") is provider),
            min(self._b2_sel, max(0, len(self._b2_flat) - 1))
        )
        self._b2_refresh()

    def _apply_provider_cats(self, provider, prov_idx, cats):
        provider._cats_wait = None      # answered (see _cats_wait_check)
        try:
            if not getattr(provider, "is_bouquet", False):
                hide = set(str(x) for x in (getattr(provider, "disabled_cats", None) or ()))
                hide |= _private_hidden(provider, "live")
                shown = [1 for _l, d in cats
                         if str(getattr(d.get("category"), "category_id", "")) not in hide]
                _cat_log(provider, "opening: %d categories, %d visible (rest hidden/private)"
                         % (len(cats), len(shown)))
        except Exception:
            pass
        if not cats:
            provider.online = False

            self._b2_open_cats.pop(
                prov_idx,
                None
            )
            self._b2_open_set.discard(
                prov_idx
            )

            self._b2_rebuild_flat()

            flat_idx = next(
                (
                    i for i, (l, d, ic)
                    in enumerate(self._b2_flat)
                    if not ic and
                    d.get("provider") is provider
                ),
                self._b2_sel
            )

            self._b2_sel = flat_idx
            self._b2_refresh()

            print(
                "[E2iPTV] LiveTV provider has no categories:",
                getattr(provider, "name", "")
            )
            return

        provider.online = True

        self._b2_open_cats[prov_idx] = cats
        self._b2_open_set.add(prov_idx)

        _save_b2_open(
            [
                state.providers[i][1].provider_key
                for i in self._b2_open_set
            ]
        )

        state.open_prov_idx = prov_idx
        state.open_prov = provider
        state.categories = cats

        self._b2_rebuild_flat()

        flat_idx = next(
            (
                i for i, (l, d, ic)
                in enumerate(self._b2_flat)
                if not ic and
                d.get("provider") is provider
            ),
            self._b2_sel
        )

        self._b2_sel = flat_idx
        self._b2_refresh()

        cat_idx = self._pick_category(
            provider, cats, _last_cat_for(provider.provider_key))
        cat_label, cat_data = cats[cat_idx]

        _save_b2_state(
            provider.provider_key,
            cat_data["category"].category_id
        )

        state.sel_cat_idx = cat_idx
        state.sel_cat = cat_data["category"]

        _nav_debug(
            "screen1._provider_ok_after_check: NEW provider expanded — "
            "open_prov set to %r, AUTO-selected first category %r — "
            "calling _load_channels_async" % (
                getattr(provider, 'name', None),
                getattr(cat_data["category"], 'category_name', None)
            )
        )

        self._load_channels_async(
            provider,
            cat_data["category"]
        )

    def _on_ok(self):
        if getattr(self, '_rail_open', False):
            self._rail_ok()
            return
        if not self._b2_flat:
            return

        self._b2_sel = min(
            self._b2_sel,
            len(self._b2_flat) - 1
        )

        label, data, is_cat = self._b2_flat[self._b2_sel]

        if is_cat:
            self._scroll_timer.stop()

            prov_idx, provider, cat_idx, category = (
                self._get_sel_provider_cat()
            )

            if category is None or provider is None:
                return

            state.open_prov = provider
            state.open_prov_idx = prov_idx
            state.categories = self._b2_open_cats.get(
                prov_idx,
                []
            )

            _save_b2_state(
                provider.provider_key,
                category.category_id
            )

            state.sel_cat_idx = cat_idx
            state.sel_cat = category

            _nav_debug(
                "screen1._on_ok: category selected, open_prov set to %r "
                "sel_cat set to %r — calling _load_channels_async" % (
                    getattr(provider, 'name', None),
                    getattr(category, 'category_name', None)
                )
            )

            # OK on a category opens the EPG grid — the way in that works
            # on every image (OpenBH swallows the EPG key, and Right is no
            # longer used here). It opens as soon as the category's channels
            # are in — at once if they're already cached.
            self._grid_after_load = category
            self._load_channels_async(
                provider,
                category
            )
            return

        provider = data.get("provider")

        if provider is None:
            return

        prov_idx = data.get("provider_idx")

        if prov_idx is None:
            prov_idx = next(
                (
                    i for i, (n, p)
                    in enumerate(state.providers)
                    if p is provider
                ),
                None
            )

        if prov_idx is None:
            return

        # --------------------------------------------------------------
        # Every deliberate OK on a provider performs ONE health check.
        #
        # A red provider is never toggled closed by OK.  OK means:
        # "check this provider".
        # --------------------------------------------------------------

        _nav_debug(
            "screen1._on_ok: PROVIDER ROW selected — provider=%r "
            "prov_idx=%r already_open=%r — starting async health check" % (
                getattr(provider, 'name', None), prov_idx,
                prov_idx in self._b2_open_set
            )
        )

        if getattr(provider, "online", True) is False:
            self._provider_check_async(
                provider,
                prov_idx
            )
            return

        # Closing an open provider needs no network check at all, and
        # reopening one that passed a check recently doesn't either —
        # both used to wait on a full live request to the provider
        # before anything moved, which is what made open/close slow.
        # A working provider opens/closes immediately: from its saved
        # category list when there is one, with the server checked in
        # the background (see _provider_ok_after_check). Only a provider
        # already marked red still waits on an explicit check first.
        self._provider_ok_after_check(provider, prov_idx)

    # ------------------------------------------------------------------
    # Channel fetch — threaded so UI never blocks
    # ------------------------------------------------------------------

    def _load_channels_async(self, provider, category):
        self._b3_fetch_gen += 1
        gen = self._b3_fetch_gen
        run_now(self._fetch_channels_thread, provider, category, gen)

    def _fetch_channels_thread(self, provider, category, gen):
        try:
            channels = state.backend.get_live_channels(provider, category)
        except Exception:
            channels = []
        # Read the saved guides of the channels about to be shown here,
        # in this background thread, so drawing them doesn't have to read
        # and decode them from the hard disk on the screen's own thread
        # (that was the pause each time scrolling stopped on a category).
        try:
            store = state.backend.epg_store
            for ch in [c for c in channels if (c.name or "").strip()][:25]:
                if gen != self._b3_fetch_gen:
                    break           # user has already moved on
                store.get_for(ch)
        except Exception:
            pass
        reactor.callFromThread(self._apply_channels, channels, gen)

    def _apply_channels(self, channels, gen):
        if not self._layout_ready:
            # Layout not done yet — defer and retry
            reactor.callLater(0.2, self._apply_channels, channels, gen)
            return
        if gen != self._b3_fetch_gen:
            _nav_debug(
                "screen1._apply_channels: STALE generation gen=%r != "
                "current=%r — discarding %d fetched channels" % (
                    gen, self._b3_fetch_gen, len(channels)
                )
            )
            return
        channels = [ch for ch in channels if ch.name.strip()]
        state.channels = channels

        last_live = _load_last_live_channel()
        restore_idx = 0
        try:
            if (str(last_live.get("view_provider_key") or last_live.get("provider_key")) ==
                    str(getattr(state.open_prov, "provider_key", "")) and
                str(last_live.get("view_category_id") or last_live.get("category_id")) ==
                    str(getattr(state.sel_cat, "category_id", ""))):
                wanted_stream = str(last_live.get("stream_id", ""))
                for i, ch in enumerate(channels):
                    if str(getattr(ch, "stream_id", "")) == wanted_stream:
                        restore_idx = i
                        break
        except Exception:
            restore_idx = 0

        state.b5_focused      = restore_idx
        state.b5_scroll       = max(0, restore_idx - 4)
        state.b5_window_start = 0
        state.b5_cursor_time  = 0
        self._epg_queue = []
        _nav_debug(
            "screen1._apply_channels: APPLIED — gen=%r open_prov=%r "
            "channels_len=%r first_channel=%r" % (
                gen, getattr(getattr(state, 'open_prov', None), 'name', None),
                len(channels),
                getattr(channels[0], 'name', None) if channels else None
            )
        )
        self._b3_refresh()
        self._s1_b5_refresh()
        # _prefetch_epg() was already called once, synchronously,
        # right when this category was selected — but state.channels
        # was still empty (or the previous category's list) at that
        # exact moment, since loading them is itself asynchronous.
        # It saw nothing to do and returned immediately, and nothing
        # called it again once the real channel list actually landed
        # here — the prefetch this whole mechanism depends on was
        # silently never starting for a freshly-selected category.
        # Now that state.channels genuinely holds this category's own
        # channels, this is where it actually needs to run.
        self._prefetch_epg()
        # OK on a category asked for the EPG grid once its channels
        # were in (see _on_ok).
        if getattr(self, "_grid_after_load", None) is state.sel_cat and state.channels:
            self._grid_after_load = None
            self._on_epg()

    def _preload_epg_bg(self, channels, gen=0):
        """Background: load EPG from disk cache for visible channels."""
        for ch in channels:
            if getattr(self, '_prefetch_stop', False): return
            if gen != getattr(self, '_preload_gen', 0): return
            key = state.backend.epg_store._composite_key(ch)
            if not state.backend.epg_store.has(key):
                cached = state.backend.epg_store._load_disk_cache(key)
                if cached:
                    state.backend.epg_store.put(key, cached)
        reactor.callFromThread(self._s1_b5_refresh)

    def _prefetch_epg(self):
        """Fetch EPG for ALL channels using a rate-limited queue (3 concurrent).
        Screen3 finds data already in store — no row-by-row loading."""
        if not state.open_prov or not state.channels:
            return
        # Building the queue (see _prefetch_epg_build_queue) means
        # checking has_for() for every channel already marked as
        # tried — cheap per channel, but this runs on the MAIN
        # thread and a large provider's full channel list adds up to
        # a real, visible stall right as the category opens, before
        # a single fetch has even started. Building the queue on a
        # background thread instead keeps that off the UI thread
        # entirely; only starting the actual fetches needs to happen
        # back on the reactor thread.
        reactor.callInThread(self._prefetch_epg_build_queue)

    def _prefetch_epg_build_queue(self):
        # Queue channels not yet fetched — but "already fetched" only
        # counts if that data is actually still there. epg_prefetched
        # has no way to know when something OUTSIDE this screen (the
        # scheduled overnight job, a manual per-provider refresh)
        # deletes and rebuilds a provider's cache files out from under
        # it — confirmed directly this leaves a channel's guide data
        # showing blank with nothing ever willing to check again for
        # the rest of the session, even minutes after it was working
        # fine. has_for() is cheap once real data isn't found (an
        # in-memory/disk check, not a network call), so checking it
        # here costs nothing for the common case and only re-queues
        # the channels that actually need it.
        channels = state.channels
        queue = []
        for ch in channels:
            if ch.stream_id in state.epg_prefetched:
                if state.backend.epg_store.has_for(ch):
                    continue
                state.epg_prefetched.discard(ch.stream_id)
            queue.append(ch)
        reactor.callFromThread(self._prefetch_epg_start, queue)

    def _prefetch_epg_start(self, queue):
        if state.channels is None or not state.open_prov:
            return
        self._epg_queue = queue
        # Start up to 6 concurrent fetches — doubled from 3. Doesn't
        # make any single slow reply faster (that's the portal's own
        # response time, confirmed directly tonight to vary a lot),
        # but more of the visible row's channels are in flight at
        # once, so the first screenful has a better chance of filling
        # in before the slowest of a smaller batch would have
        # finished alone.
        for _ in range(min(6, len(self._epg_queue))):
            self._epg_next()

    def _epg_next(self):
        if not hasattr(self, '_epg_queue') or not self._epg_queue:
            return
        if not state.open_prov:
            return
        # Hold the guide download while Series/Movies, a player or any
        # other screen is in front of Live TV. It fetches a whole
        # category, up to 6 at a time, from the same provider account
        # those screens are using, so it was slowing their requests
        # (series info, stream links) right down. It carries on from
        # where it stopped once Live TV or its EPG grid is back on top.
        if getattr(self, "_prefetch_stop", False):
            return              # Live TV has been closed
        if not self._live_on_top():
            reactor.callLater(3, self._epg_next)
            return
        # Hold off while you're scrolling B2: decoding a provider's guide
        # (large for Xtream, several days per channel) competes with the
        # screen for the box's CPU and made scrolling stutter.
        if time.time() - getattr(self, "_last_scroll", 0) < 1.5:
            reactor.callLater(1, self._epg_next)
            return
        # Provider said "too many requests": wait out its pause instead of
        # running through the queue (see backend/xtream_client.py).
        try:
            from .backend.xtream_client import _read_wait
            wait = _read_wait(state.open_prov)[0] - time.time()
            if wait > 0:
                reactor.callLater(min(wait, 300) + 1, self._epg_next)
                return
        except Exception:
            pass
        ch = self._epg_queue.pop(0)
        state.epg_prefetched.add(ch.stream_id)
        reactor.callInThread(self._epg_worker, state.open_prov, ch)

    def _live_on_top(self):
        try:
            top = self.session.current_dialog
        except Exception:
            return True
        return top is None or top is self or isinstance(top, Screen3)

    def _epg_worker(self, provider, channel):
        if getattr(self, "_prefetch_stop", False): return
        try:
            state.backend.get_epg(provider, channel)
        except Exception:
            pass
        reactor.callFromThread(self._epg_done)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _fetch_initial_data(self):
        if not state.providers:
            return

        saved_provider_key, saved_cat_id = _load_b2_state()
        open_provider_keys = set(str(x) for x in _load_b2_open())

        # Find focused provider by stable provider key.
        # Legacy numeric state is accepted once for migration.
        focused_provider = None
        focused_prov_idx = 0
        for i, (name, p) in enumerate(state.providers):
            if not _shows(p, 'live'):
                continue
            if focused_provider is None:
                focused_provider, focused_prov_idx = p, i
            if str(p.provider_key) == str(saved_provider_key):
                focused_provider, focused_prov_idx = p, i
                break
            if str(p.provider_id) == str(saved_provider_key):
                focused_provider, focused_prov_idx = p, i

        if focused_provider is None:
            self._b2_rebuild_flat()
            self._b2_refresh()
            return

        # Make sure focused provider is in open set
        open_provider_keys.add(str(focused_provider.provider_key))

        # Load categories for ALL open providers
        for i, (name, p) in enumerate(state.providers):
            if not _shows(p, 'live'):
                continue
            if str(p.provider_key) not in open_provider_keys:
                continue
            try:
                cats_raw = self._get_b2_categories(p)
                cats = self._cats_to_list(cats_raw)
            except Exception:
                cats = []
            if cats:
                self._b2_open_cats[i] = cats
                self._b2_open_set.add(i)

        # Set focused provider state
        focused_cats = self._b2_open_cats.get(focused_prov_idx, [])
        state.open_prov_idx = focused_prov_idx
        state.open_prov     = focused_provider
        state.categories    = focused_cats

        if not focused_cats:
            self._b2_rebuild_flat()
            self._b2_refresh()
            return

        # Find focused category
        cat_idx = 0
        for i, (label, data) in enumerate(focused_cats):
            cat_obj = data.get("category")
            if cat_obj and str(cat_obj.category_id) == str(saved_cat_id):
                cat_idx = i
                break

        self._b2_rebuild_flat()

        # Find correct flat index for focused category
        cat_target  = focused_cats[cat_idx][0].strip()
        flat_idx    = focused_prov_idx
        in_provider = False
        for i, (lbl, data, is_cat) in enumerate(self._b2_flat):
            if not is_cat and data.get("provider") is focused_provider:
                in_provider = True
                flat_idx    = i
            elif in_provider and is_cat and lbl.strip() == cat_target:
                flat_idx = i
                break

        self._b2_sel = flat_idx
        self._b2_refresh()

        cat_label, cat_data = focused_cats[cat_idx]
        state.sel_cat_idx = cat_idx
        state.sel_cat     = cat_data["category"]
        self._load_channels_async(focused_provider, cat_data["category"])

    # ------------------------------------------------------------------
    # B2
    # ------------------------------------------------------------------

    def get_mirror_b2(self):
        """Standard content-screen method — Screen2 calls this to
        mirror this screen's category/provider list, rather than
        assuming Live TV and reading global state. Returns (flat, sel)
        exactly as B2Common tracks them internally, so no data is
        duplicated or copied — this just hands back the live reference."""
        return self._b2_flat, self._b2_sel

    def get_mirror_b3(self):
        """Standard content-screen method — Screen2 calls this to
        mirror this screen's programme/channel list. Returns
        (channels, scroll, focused)."""
        return state.channels, state.b5_scroll, state.b5_focused

    # ------------------------------------------------------------------
    # B3
    # ------------------------------------------------------------------

    def _b3_refresh(self):
        self["b3_time"].setText(
            time.strftime("%a %d %b  %H:%M", time.localtime())
        )

        channels = state.channels
        vis_start, vis_end = self._b3_window(
            len(channels), state.b5_scroll, self.B3_VISIBLE
        )

        entries = self._b3_build_entries(
            channels, vis_start, vis_end, state.b5_focused,
            item_w=self.B3_W, item_h=self.B3_ITEM_H,
            picon_x=0, text_x=66,
        )

        self["b3_list"].setList(entries)
        self._b3_move_to_focused("b3_list", state.b5_focused, vis_start)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _layout_finished(self):
        self._layout_ready = True
        from twisted.internet import reactor
        reactor.callLater(1.0, self._startup_notice)
        # Start the last Live channel playing again. Runs in the
        # background (a Stalker stream address is a request to the
        # portal), independently of B2/B3 drawing.
        self._resume_last_live()
        # Last rail choice was Live TV: once that channel is playing in
        # the mini TV, go straight to full screen on it (as 0 does).
        if getattr(self, "_hide_until_fullscreen", False):
            reactor.callLater(1.0, self._live_fullscreen, 1, 20)
        # Last rail choice was EPG Grid: open the grid on the last
        # channel's category once its channels are in.
        elif load_b1_last_choice() == "EPG Grid":
            reactor.callLater(0.3, self._startup_grid, 0)
        # Show B2 straight away from the saved snapshot when there is
        # one; the servers are only asked afterwards, in the background.
        # The old path (below) waited on every open provider's server,
        # one after another, before showing anything at all.
        try:
            restored = self._instant_restore()
        except Exception:
            restored = False
        if restored:
            reactor.callInThread(self._refresh_open_providers_bg)
            return
        reactor.callInThread(self._fetch_data_bg)

    def _instant_restore(self):
        """Main thread, local files only: rebuild B2 from the snapshot,
        reselect the last provider and category, and start loading its
        channels. Returns False (falls back to the old path) if the
        snapshot has nothing for the last-used provider."""
        if not state.providers:
            return False
        snap = _snapshot_load()
        if not snap:
            return False

        self._apply_provider_settings()
        saved_provider_key, saved_cat_id = _load_b2_state()

        # Same provider choice rules as _load_initial_data.
        provider = None
        prov_idx = 0
        for i, (name, p) in enumerate(state.providers):
            if not _shows(p, 'live'):
                continue
            if provider is None:
                provider, prov_idx = p, i
            if str(p.provider_key) == str(saved_provider_key):
                provider, prov_idx = p, i
                break
            if str(p.provider_id) == str(saved_provider_key):
                provider, prov_idx = p, i
        if provider is None:
            return False

        focused_cats = (_bouquet_cat_list() if getattr(provider, "is_bouquet", False)
                        else _cats_from_snapshot(snap.get(str(provider.provider_key)), provider))
        if not focused_cats:
            return False

        open_provider_keys = set(str(x) for x in _load_b2_open())
        open_provider_keys.add(str(provider.provider_key))
        for i, (name, p) in enumerate(state.providers):
            if not _shows(p, 'live'):
                continue
            key = str(p.provider_key)
            if key not in open_provider_keys:
                continue
            cats = (focused_cats if i == prov_idx
                    else _bouquet_cat_list() if getattr(p, "is_bouquet", False)
                    else _cats_from_snapshot(snap.get(key), p))
            if cats:
                self._b2_open_cats[i] = cats
                self._b2_open_set.add(i)

        cat_idx = self._pick_category(
            provider, focused_cats,
            saved_cat_id or _last_cat_for(provider.provider_key))
        category = focused_cats[cat_idx][1]["category"]

        state.open_prov_idx = prov_idx
        state.open_prov     = provider
        state.categories    = focused_cats
        state.sel_cat_idx   = cat_idx
        state.sel_cat       = category

        self._finish_restore(prov_idx, focused_cats, cat_idx, provider, category)
        return True

    def _refresh_open_providers_bg(self):
        """Background, after an instant restore: fetch the open
        providers' real category lists, save them, and update B2 only
        if something actually changed on the server."""
        updates = []
        for i in sorted(list(self._b2_open_set)):
            try:
                p = state.providers[i][1]
                cats = self._cats_to_list(self._get_b2_categories(p))
            except Exception:
                continue
            if cats:
                p.online = True
                p._last_ok_check = time.time()
                _snapshot_save(p, cats)
                updates.append((i, cats))
        reactor.callFromThread(self._apply_bg_category_refresh, updates)

    def _apply_bg_category_refresh(self, updates):
        cache = self.__dict__.setdefault("_b2_cats_cache", {})
        content = getattr(self, "_b2_content", "live")
        changed = False
        for i, cats in updates:
            cache[(i, content)] = (time.time(), cats)
            if i not in self._b2_open_set:
                continue          # closed by the user in the meantime
            if _cats_signature(self._b2_open_cats.get(i)) == _cats_signature(cats):
                continue
            self._b2_open_cats[i] = cats
            if i == state.open_prov_idx:
                state.categories = cats
            changed = True
        if not changed:
            return

        # Keep the highlight on the same provider/category row.
        want = None
        if 0 <= self._b2_sel < len(self._b2_flat):
            _l, data, is_cat = self._b2_flat[self._b2_sel]
            cat = data.get("category") if is_cat else None
            want = (data.get("provider_idx"),
                    str(cat.category_id) if cat is not None else None)
        self._b2_rebuild_flat()
        if want:
            for idx, (_l, data, is_cat) in enumerate(self._b2_flat):
                cat = data.get("category") if is_cat else None
                key = (data.get("provider_idx"),
                       str(cat.category_id) if cat is not None else None)
                if key == want:
                    self._b2_sel = idx
                    break
        self._b2_refresh()

    def _fetch_data_bg(self):
        """Background thread — data only, no UI calls."""
        from twisted.internet import reactor
        result = self._load_initial_data()
        reactor.callFromThread(self._apply_initial_data, result)

    def _apply_provider_settings(self):
        """Load persisted per-provider settings and app-wide settings."""
        import json
        # App-wide settings
        try:
            app = json.load(open(DATA + "/cache/app_settings.json"))
            if "recording_path"       in app: state.recording_path       = app["recording_path"]
            if "playlists_path"       in app: state.playlists_path       = app["playlists_path"]
            if "rec_start_offset"     in app: state.rec_start_offset     = app["rec_start_offset"]
            if "rec_end_offset"       in app: state.rec_end_offset       = app["rec_end_offset"]
            if "catchup_start_offset" in app: state.catchup_start_offset = app["catchup_start_offset"]
            if "catchup_end_offset"   in app: state.catchup_end_offset   = app["catchup_end_offset"]
        except Exception:
            try:
                json.dump({
                    "recording_path":       state.recording_path,
                    "playlists_path":       state.playlists_path,
                    "rec_start_offset":     0,
                    "rec_end_offset":       0,
                    "catchup_start_offset": 0,
                    "catchup_end_offset":   0,
                }, open(DATA + "/cache/app_settings.json", "w"))
            except Exception:
                pass
        # Per-provider settings
        for name, provider in state.providers:
            provider_key = getattr(provider, "provider_key", "")
            legacy_id = getattr(provider, "provider_id", None)

            # Load provider settings using stable provider identity.
            # Fall back to the old numeric file during migration.
            try:
                settings = None

                if provider_key:
                    try:
                        settings = json.load(
                            open(
                                DATA + "/cache/provider_%s.json"
                                % provider_key
                            )
                        )
                    except Exception:
                        settings = None

                # Old position-numbered settings files are only used for a
                # provider with no key at all: once playlists.txt has been
                # reordered, "provider_3.json" belongs to someone else.
                if settings is None and legacy_id is not None and not provider_key:
                    settings = json.load(
                        open(
                            DATA + "/cache/provider_%s.json"
                            % legacy_id
                        )
                    )

                if isinstance(settings, dict):
                    # Either switch turns a provider off: a #http line in
                    # playlists.txt (already applied when it was read) or
                    # Enable: No here. A saved "Yes" must not switch a #'d
                    # provider back on.
                    if settings.get("enabled") is False:
                        provider.enabled = False
                    for kind in ("live", "movies", "series"):
                        if "show_" + kind in settings:
                            setattr(provider, "show_" + kind,
                                    bool(settings["show_" + kind]))
                    if "refresh_interval" in settings:
                        provider.refresh_interval = settings["refresh_interval"]
                    # Saved by Settings -> provider -> EPG Offset, but
                    # was never read back, so it reset on every restart.
                    if "epg_offset" in settings:
                        provider.epg_offset = int(settings["epg_offset"] or 0)
            except Exception:
                pass
            try:
                from .backend.epg_store import set_provider_offset
                set_provider_offset(provider_key, getattr(provider, "epg_offset", 0))
            except Exception:
                pass
            # Pre-load disabled_cats so B2 filter works immediately.
            # Use the same provider-name based persistent settings
            # as Manage Categories in screen2.py.
            try:
                from .backend.provider_category_settings import load
                provider.disabled_cats = load(provider)
            except Exception:
                provider.disabled_cats = set()

    def _load_initial_data(self):
        """Pure data loading — called from background thread."""
        if not state.providers:
            return None
        # Apply persisted provider settings (enabled flag etc.)
        self._apply_provider_settings()
        saved_provider_key, saved_cat_id = _load_b2_state()

        # Find provider by stable provider key.
        # Legacy numeric state is accepted once for migration.
        provider = None
        prov_idx = 0
        for i, (name, p) in enumerate(state.providers):
            if not _shows(p, 'live'):
                continue
            if provider is None:
                provider, prov_idx = p, i
            if str(p.provider_key) == str(saved_provider_key):
                provider, prov_idx = p, i
                break
            if str(p.provider_id) == str(saved_provider_key):
                provider, prov_idx = p, i

        if provider is None:
            return None

        try:
            cats_raw = self._get_b2_categories(provider)
        except Exception:
            cats_raw = []
        cats = self._cats_to_list(cats_raw)
        if not cats:
            return (prov_idx, [], 0, provider, None)
        _snapshot_save(provider, cats)

        cat_idx = self._pick_category(
            provider, cats,
            saved_cat_id or _last_cat_for(provider.provider_key))

        category = cats[cat_idx][1].get("category")
        return (prov_idx, cats, cat_idx, provider, category)

    def _apply_initial_data(self, result):
        """Main thread — all UI updates after background data load."""
        if not self._layout_ready:
            reactor.callLater(0.2, self._apply_initial_data, result)
            return
        if result is None:
            self._b2_rebuild_flat()
            self._b2_refresh()
            return
        prov_idx, cats, cat_idx, provider, category = result
        state.open_prov_idx = prov_idx
        state.open_prov     = provider
        state.categories    = cats
        if not cats or not category:
            self._b2_rebuild_flat()
            self._b2_sel = prov_idx
            self._b2_refresh()
            return
        state.sel_cat_idx = cat_idx
        state.sel_cat     = category

        # Restore previously open providers
        open_provider_keys = set(str(x) for x in _load_b2_open())
        open_provider_keys.add(str(provider.provider_key))  # ensure focused is open
        reactor.callInThread(self._restore_open_providers,
                             open_provider_keys, prov_idx, cats, cat_idx, provider, category)

    def _restore_open_providers(self, open_provider_keys, focused_prov_idx, focused_cats,
                                cat_idx, provider, category):
        """Background: load categories for all previously open providers then refresh UI."""
        # Load categories for each open provider (excluding focused, already loaded)
        for i, (name, p) in enumerate(state.providers):
            if not _shows(p, 'live'):
                continue
            if str(p.provider_key) not in open_provider_keys:
                continue
            if i == focused_prov_idx:
                self._b2_open_cats[i] = focused_cats
                self._b2_open_set.add(i)
                continue
            try:
                cats_raw = self._get_b2_categories(p)
                cats = self._cats_to_list(cats_raw)
            except Exception:
                cats = []
            if cats:
                self._b2_open_cats[i] = cats
                self._b2_open_set.add(i)
                _snapshot_save(p, cats)

        reactor.callFromThread(self._finish_restore,
                               focused_prov_idx, focused_cats, cat_idx,
                               provider, category)

    def _finish_restore(self, focused_prov_idx, focused_cats, cat_idx,
                        provider, category):
        """Main thread: rebuild B2 with all open providers then select focused category."""
        self._b2_rebuild_flat()
        cat_target  = focused_cats[cat_idx][0].strip() if cat_idx < len(focused_cats) else ""
        flat_idx    = focused_prov_idx
        in_provider = False
        for i, (lbl, data, is_cat) in enumerate(self._b2_flat):
            if not is_cat:
                in_provider = (data.get("provider") is provider)
                if in_provider:
                    flat_idx = i
            elif in_provider and lbl.strip() == cat_target:
                flat_idx = i
                break
        self._b2_sel = flat_idx
        self._b2_refresh()
        self._load_channels_async(provider, category)
        self._prefetch_epg()

    def _s1_epg_refresh_both(self):
        # The category list (B3) and the grid (B5) are separate
        # displays reading the same underlying EPG data — confirmed
        # directly that refreshing only one of them (as both the
        # debounce timer and _epg_done used to) leaves the category
        # list stuck showing "No EPG Available" forever once first
        # rendered, even as real data keeps arriving in the
        # background; only navigating away and back (which happens to
        # trigger a full B3 rebuild elsewhere) or switching to the
        # grid itself ever showed it. Every place that means "EPG
        # data just changed, please redraw" goes through here so
        # both stay in sync together.
        self._b3_refresh()
        self._s1_b5_refresh()

    def _epg_done(self):
        self._epg_next()
        # Only refresh B5 when all visible channels have EPG, otherwise debounce
        vis_end = min(len(state.channels), state.b5_scroll + 10)
        all_ready = all(
            state.backend.epg_store.has_for(state.channels[i])
            for i in range(state.b5_scroll, vis_end)
        ) if state.channels else True
        if all_ready:
            self._b5_timer.stop()
            self._s1_epg_refresh_both()
        else:
            self._b5_timer.stop()
            self._b5_timer.start(2000, True)

    def _refresh_b2_after_settings(self):
        try:
            from .backend.provider_category_settings import load

            for name, provider in state.providers:
                provider.disabled_cats = load(provider)

            # My Bouquets: last bouquet deleted -> remove the entry.
            from .backend.bouquets import remove_from_state_if_empty
            gone = remove_from_state_if_empty(state)
            if gone is not None:
                self._b2_open_set.discard(gone)
                self._b2_open_cats.pop(gone, None)
                self.__dict__.setdefault("_b2_cats_cache", {}).pop(
                    (gone, getattr(self, "_b2_content", "live")), None)
                if getattr(state.open_prov, "is_bouquet", False):
                    state.open_prov = None
                    state.open_prov_idx = None

            # My Bouquets: bouquets are local files, so re-read them
            # (a new one may have been made, or one deleted, from the
            # EPG grid).
            for i, (name, provider) in enumerate(state.providers):
                if getattr(provider, "is_bouquet", False) and i in self._b2_open_set:
                    from .backend.bouquets import categories as _bq_cats
                    cats = [(c.category_name, {"type": "category",
                                               "name": c.category_name,
                                               "category": c})
                            for c in _bq_cats()]
                    self._b2_open_cats[i] = cats
                    self.__dict__.setdefault("_b2_cats_cache", {})[
                        (i, getattr(self, "_b2_content", "live"))] = (time.time(), cats)

            self._b2_rebuild_flat()
            if self._b2_flat:
                self._b2_sel = min(self._b2_sel, len(self._b2_flat) - 1)
            self._b2_refresh()

        except Exception:
            pass

    def _s1_b5_refresh(self):
        """Viewport into screen3 B5 — identical scale, clipped at 730px."""
        W    = 730
        S3_W = 1300
        WIN  = 7200
        IH   = 66
        VIS  = 10

        channels = state.channels
        if not channels:
            self["s1_b5_list"].setList([])
            self._s1_info_update()
            return

        now    = int(time.time())
        ws     = state.b5_window_start or now
        we     = ws + WIN
        cursor = state.b5_cursor_time or now
        pps    = float(S3_W) / WIN

        self["b3_time"].setText(
            time.strftime("%a %d %b  %H:%M", time.localtime(ws)) +
            "  -  " +
            time.strftime("%H:%M", time.localtime(ws + WIN)))
        self["s1_b5_date"].setText(
            time.strftime("%H:%M", time.localtime(ws)))
        self["s1_b5_mark1"].setText(
            time.strftime("%H:%M", time.localtime(ws + 1800)))
        self["s1_b5_mark2"].setText(
            time.strftime("%H:%M", time.localtime(ws + 3600)))

        vis_start = state.b5_scroll
        vis_end   = min(len(channels), vis_start + VIS)
        entries   = []

        for idx, ch in enumerate(channels[vis_start:vis_end]):
            abs_idx    = vis_start + idx
            is_focused = abs_idx == state.b5_focused
            events     = state.backend.epg_store.get_for(ch)

            # Build complete timeline with synthetic tiles for any gaps
            from .backend.epg import Epg
            def _make_dummy(t_start, t_end):
                e = Epg(); e.stream_id = ch.stream_id
                e.title = "No EPG Available"; e.description = ""
                e.start = t_start; e.end = t_end
                return e

            in_window = [e for e in events if e.end > ws and e.start < we]
            if not in_window:
                filled = []
                t = (ws // 1800) * 1800
                while t < we:
                    filled.append(_make_dummy(t, t + 1800))
                    t += 1800
            else:
                filled = []
                last_end = ws
                for ev in sorted(in_window, key=lambda e: e.start):
                    if ev.start > last_end + 60:
                        t = last_end
                        while t < ev.start:
                            next_b = ((t // 1800) + 1) * 1800
                            slot_end = min(next_b, ev.start)
                            filled.append(_make_dummy(t, slot_end))
                            t = slot_end
                    filled.append(ev)
                    last_end = max(last_end, ev.end)
                if last_end < we:
                    t = last_end
                    while t < we:
                        next_b = ((t // 1800) + 1) * 1800
                        slot_end = min(next_b, we)
                        filled.append(_make_dummy(t, slot_end))
                        t = slot_end
            events = filled
            entry      = [None]

            entry.append(MultiContentEntryText(
                pos=(0, 0), size=(W, IH),
                font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                text="", color=0xffffff, color_sel=0xffffff,
                backcolor=_T.PANEL, backcolor_sel=_T.PANEL))

            cursor_found = False
            last_x2      = 0
            for ev in sorted(events, key=lambda e: e.start):
                if ev.end <= ws or ev.start >= we:
                    continue
                x1 = max(last_x2, max(0, int((ev.start - ws) * pps)))
                x2 = min(W,            int((ev.end   - ws) * pps))
                w  = x2 - x1
                if w < 6:
                    last_x2 = max(last_x2, x2)
                    continue
                last_x2      = x2
                is_cursor    = is_focused and not cursor_found and ev.start <= cursor < ev.end
                if is_cursor:
                    cursor_found = True
                    bg = _T.HI; fg = 0x000000
                else:
                    bg = _T.RAIL; fg = 0xffffff

                entry.append(MultiContentEntryText(
                    pos=(x1 + 2, 2), size=(w - 4, IH - 4),
                    font=1, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text="", color=fg, color_sel=fg,
                    backcolor=bg, backcolor_sel=bg, cornerRadius=12))

                rec_colour = _epg_recording_colour(
                    state, ch.stream_id, ev.start, ev.end, channel=ch
                )

                if rec_colour:
                    bg = rec_colour
                    fg = 0xffffff
                elif is_cursor:
                    cursor_found = True
                    bg = _T.HI
                    fg = 0x000000
                else:
                    bg = _T.RAIL
                    fg = 0xffffff

                entry.append(MultiContentEntryText(
                    pos=(x1 + 2, 2), size=(w - 4, IH - 4),
                    font=1, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text="", color=fg, color_sel=fg,
                    backcolor=bg, backcolor_sel=bg, cornerRadius=12))

                if w > 10:
                    entry.append(MultiContentEntryText(
                        pos=(x1 + 24, 4), size=(max(1, w - 40), IH - 8),
                        font=1, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                        text=ev.title, color=fg, color_sel=fg,
                        backcolor=bg, backcolor_sel=bg))

            now_x = int((now - ws) * pps)
            if 0 <= now_x < W:
                entry.append(MultiContentEntryText(
                    pos=(now_x, 0), size=(3, IH),
                    font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text="", color=0xff0000, color_sel=0xff0000,
                    backcolor=0xff0000, backcolor_sel=0xff0000))
            entries.append(entry)

        self["s1_b5_list"].setList(entries)
        self._s1_info_update()

    def _s1_info_update(self):
        """Mirror of screen3 info panel, clipped to 565px wide."""
        if not state.channels:
            self["s1_info_title"].setText("")
            self["s1_info_times"].setText("")
            self["s1_info_desc"].setText("")
            return

        now    = int(time.time())
        cursor = state.b5_cursor_time or now
        idx    = state.b5_focused
        if idx < 0 or idx >= len(state.channels):
            return

        ch     = state.channels[idx]
        events = state.backend.epg_store.get_for(ch)
        ev     = None
        for e in events:
            if e.start <= cursor < e.end:
                ev = e
                break

        if ev:
            start = time.strftime("%H:%M", time.localtime(ev.start))
            end   = time.strftime("%H:%M", time.localtime(ev.end))
            dur   = max(0, (ev.end - ev.start) // 60)
            self["s1_info_title"].setText(ev.title)

            # Recording indicator — scoped to this specific event, same
            # matching as the main EPG grid and its badge above.
            indicator, colour = "", 0xffffff
            # Bouquet channels are recorded under their own provider.
            _rec_prov = getattr(ch, "_bq_provider", None) or state.open_prov
            if _rec_prov:
                _matches = [
                    (_rk, _rec) for _rk, _rec in state.active_recordings.items()
                    if (_rec.get("provider_id") == _rec_prov.provider_id
                        and _rec.get("stream_id") == ch.stream_id
                        and _rec.get("event_start") == ev.start)
                ]
                if len(_matches) > 1:
                    try:
                        with open(LOG("e2iptv_rec_indicator_debug.log"), "a") as _f:
                            _f.write(
                                "%s MULTIPLE active_recordings entries match "
                                "provider_id=%r stream_id=%r event_start=%r: %r\n" % (
                                    time.strftime("%H:%M:%S"),
                                    _rec_prov.provider_id, ch.stream_id,
                                    ev.start, _matches
                                )
                            )
                    except Exception:
                        pass
                if _matches:
                    _rk, _rec = _matches[0]
                    try:
                        with open(LOG("e2iptv_rec_indicator_debug.log"), "a") as _f:
                            _f.write(
                                "%s indicator match key=%r status=%r full=%r\n" % (
                                    time.strftime("%H:%M:%S"), _rk,
                                    _rec.get("status"), _rec
                                )
                            )
                    except Exception:
                        pass
                    if _rec.get("status") == "active":
                        indicator, colour = "   ●●● REC", 0xff4444
                    elif _rec.get("status") == "scheduled":
                        indicator, colour = "   ●●● SCHEDULED", 0x44cc44

            self["s1_info_times"].setText(
                "%s - %s  %d min%s" % (start, end, dur, indicator))
            try:
                self["s1_info_times"].instance.setForegroundColor(gRGB(colour))
            except Exception:
                pass
            self["s1_info_desc"].setText(ev.description)
        else:
            self["s1_info_title"].setText(ch.name)
            self["s1_info_times"].setText("")
            self["s1_info_desc"].setText("")


# -----------------------------------------------------------------------------
# Slow-screen log: any of these taking longer than 150 ms is noted in
# /tmp/e2iptv_slow.log, to find what makes scrolling pause.
# -----------------------------------------------------------------------------

def _hold_picon_repaint():
    try:
        from .backend.picon import hold_refresh
        hold_refresh(2.0)
    except Exception:
        pass


def _timed(name, fn):
    def wrapper(self, *a, **k):
        t0 = time.time()
        try:
            return fn(self, *a, **k)
        finally:
            ms = (time.time() - t0) * 1000
            if ms > 150:
                try:
                    with open(LOG("e2iptv_slow.log"), "a") as f:
                        f.write("%s %-22s %5.0f ms  %s / %s\n" % (
                            time.strftime("%H:%M:%S"), name, ms,
                            getattr(getattr(state, "open_prov", None), "name", ""),
                            getattr(getattr(state, "sel_cat", None), "category_name", "")))
                except Exception:
                    pass
    wrapper.__name__ = fn.__name__
    return wrapper


for _n in ("_b2_refresh", "_b3_refresh", "_s1_b5_refresh", "_apply_channels",
           "_on_scroll_stopped", "_epg_done", "_s1_info_update"):
    _f = getattr(Screen1, _n, None)
    if _f is not None:
        setattr(Screen1, _n, _timed(_n, _f))
