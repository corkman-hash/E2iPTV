# =============================================================================
# E2iPTV — Screen 2 — Expanded B1 Rail
# B1 expanded to 300px | B2 read-only | B3 passive | B5+info mirror
# Called from screen1 via left key from B2
# =============================================================================

from .paths import HDD, LOG, cap  # data folder / debug logs
from .paths import DATA  # E2iPTV_Data folder
from Screens.Screen import Screen
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.GUIComponent import GUIComponent
from Components.MultiContent import MultiContentEntryText, MultiContentEntryPixmapAlphaBlend
from Tools.LoadPixmap import LoadPixmap
from enigma import eTimer, eListbox, eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT, RT_HALIGN_CENTER, RT_HALIGN_RIGHT, RT_VALIGN_CENTER, ePoint, eSize, gRGB, eServiceReference

# See screen1.py's identical block for why this exists: without
# BT_SCALE/BT_KEEP_ASPECT_RATIO, MultiContentEntryPixmapAlphaBlend draws
# a picon at its native resolution instead of scaled to fit the box,
# which is why picons here were showing as small cropped fragments.
try:
    from enigma import BT_SCALE, BT_KEEP_ASPECT_RATIO
    PICON_FLAGS = BT_SCALE | BT_KEEP_ASPECT_RATIO
except ImportError:
    PICON_FLAGS = 0
from .state import state, _epg_recording_colour, _nav_debug
from .media_browser import MediaBrowser
from .b1common import B1Common
from .b2common import B2Common
from .b3common import B3Common
import os
import time

# -----------------------------------------------------------------------------
# Thin ChoiceBox/MessageBox subclasses — recolour ONLY the selected-row
# highlight tile to the same whiteish colour used by every other
# list/menu highlight in this plugin (see e.g. the b6_highlight/
# b7_highlight/... widgets below, backgroundColor="#d0e8ff" with black
# selected-item text), instead of OpenATV's own default highlight
# colour. Everything else — skin layout, positioning, focus/input
# handling — is untouched native ChoiceBox/MessageBox, which is what
# actually works reliably on this box; only the two colour calls below
# are new.
# -----------------------------------------------------------------------------

class E2iChoiceBox(ChoiceBox):
    def __init__(self, *args, **kwargs):
        ChoiceBox.__init__(self, *args, **kwargs)
        # Must be a real bound method, not a lambda/plain function —
        # Screen.createGUIScreen() only treats onLayoutFinish entries as
        # callables when isinstance(entry, type(self.close)) is True
        # (i.e. a bound method); anything else it tries to exec() as a
        # skin-applet code string instead, which crashes on a lambda.
        self.onLayoutFinish.append(self._e2i_recolour)

    def _e2i_recolour(self):
        try:
            self["list"].instance.setBackgroundColorSelected(gRGB(0xd0e8ff))
            self["list"].instance.setForegroundColorSelected(gRGB(0x000000))
        except Exception:
            pass


class E2iMessageBox(MessageBox):
    def __init__(self, *args, **kwargs):
        MessageBox.__init__(self, *args, **kwargs)
        self.onLayoutFinish.append(self._e2i_recolour)

    def _e2i_recolour(self):
        try:
            self["list"].instance.setBackgroundColorSelected(gRGB(0xd0e8ff))
            self["list"].instance.setForegroundColorSelected(gRGB(0x000000))
        except Exception:
            pass


class RecordingActionMenu(Screen):
    """Compact Play/Delete menu for a selected recording — used instead
    of OpenATV's own ChoiceBox (even the recoloured E2iChoiceBox above)
    specifically for this menu, because ChoiceBox's skin/background is
    still OpenATV's own opaque native panel underneath the recolour —
    only the selected-row highlight was ever ours. This is a genuinely
    separate small screen, sized to its own content (same proven
    pattern as RecordingInfoOverlay/RecordingHud in player.py) and
    styled with this plugin's own dark panel look throughout, not just
    the selection highlight.

    Full 1920x1080 screen with an opaque blue backdrop behind the
    actual panel, rather than just the panel's own small 640x200 — the
    panel alone being opaque still left everything outside its own
    bounds showing whatever's behind it (Live TV/the recording's own
    playback), same problem search.py's SearchTypeMenu had and was
    fixed the same way (see its own comment for the fuller
    reasoning) — this is that same fix applied to the screen the
    pattern was actually modelled from. All the panel's own widget
    coordinates below are shifted by the same (640,400) the panel
    itself used to be positioned at, so it still looks exactly as it
    did — only the area around it actually changed.

    Behaves the same as the ChoiceBox call it replaces: Up/Down moves
    the selection, OK confirms and closes with a (label, action) tuple
    — same shape ChoiceBox returned — and Cancel closes with None, so
    the existing _recordings_menu_choice callback needed no changes.

    The selection highlight is one widget that MOVES between rows
    (the same off/on-screen move convention used throughout player.py
    for RecordingHud/RecordingSkipOsd) rather than two rows each
    toggling their own background color at runtime — an earlier version
    tried that and on-device testing showed clearing a row's background
    back to dark didn't repaint reliably, while lighting one up did, so
    the previous row's highlight never actually went away. Moving a
    single rect sidesteps that repaint issue entirely, same as it has
    everywhere else in this project. Text colour switching (white/black
    for contrast) DID repaint correctly in that same test, so that part
    is untouched."""

    _OPTIONS = [("Play", "play"), ("Delete", "delete")]
    _SEL_FG    = 0x000000
    _NORMAL_FG = 0xffffff
    _ROW_Y = {0: 460, 1: 530}
    _OFFSCREEN_X = 3500

    skin = (
        '<screen name="RecordingActionMenu"'
        ' position="0,0" size="1920,1080"'
        ' flags="wfNoBorder">'
        '<eLabel position="0,0" size="1920,1080"'
        ' backgroundColor="#0a4aa8" zPosition="0"/>'
        '<eLabel position="640,400" size="640,200"'
        ' backgroundColor="#0d1117" zPosition="0"/>'
        '<widget name="title" position="660,410" size="600,40"'
        ' font="E2iRegular;28" foregroundColor="#1a98ff"'
        ' halign="left" valign="center" transparent="1" zPosition="2"/>'
        '<eLabel position="640,460" size="640,1"'
        ' backgroundColor="#30ffffff" zPosition="2"/>'
        # Starts off-screen (x=3500); _update_highlight moves it onto
        # whichever row is selected.
        '<widget name="highlight_bg" position="3500,460" size="640,70"'
        ' backgroundColor="#d0e8ff" zPosition="1" transparent="0"/>'
        '<widget name="row0_text" position="670,460" size="580,70"'
        ' font="E2iRegular;30" foregroundColor="#ffffff"'
        ' halign="left" valign="center" transparent="1" zPosition="2"/>'
        '<widget name="row1_text" position="670,530" size="580,70"'
        ' font="E2iRegular;30" foregroundColor="#ffffff"'
        ' halign="left" valign="center" transparent="1" zPosition="2"/>'
        '</screen>'
    )

    def __init__(self, session, title):
        Screen.__init__(self, session)
        self._sel = 0
        self["title"]        = Label(title)
        self["highlight_bg"] = Label("")
        self["row0_text"]    = Label(self._OPTIONS[0][0])
        self["row1_text"]    = Label(self._OPTIONS[1][0])
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self._confirm,
                "cancel": self._dismiss,
                "up":     self._move_up,
                "down":   self._move_down,
            }, -1)
        self.onLayoutFinish.append(self._update_highlight)

    def _move_up(self):
        self._sel = (self._sel - 1) % len(self._OPTIONS)
        self._update_highlight()

    def _move_down(self):
        self._sel = (self._sel + 1) % len(self._OPTIONS)
        self._update_highlight()

    def _update_highlight(self):
        try:
            self["highlight_bg"].instance.move(
                ePoint(640, self._ROW_Y[self._sel]))
        except Exception:
            pass
        for i, txt_name in enumerate(("row0_text", "row1_text")):
            selected = (i == self._sel)
            try:
                self[txt_name].instance.setForegroundColor(
                    gRGB(self._SEL_FG if selected else self._NORMAL_FG))
            except Exception:
                pass

    def _confirm(self):
        self.close(self._OPTIONS[self._sel])

    def _dismiss(self):
        self.close(None)

# -----------------------------------------------------------------------------
# Local IE2iList (same as screen1/screen3, avoids circular import)
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
# Constants
# -----------------------------------------------------------------------------

ICON_PATH   = "/usr/lib/enigma2/python/Plugins/Extensions/E2iPTV/assets/transPNG/"
CAT_INDENT  = "   "

RAIL_COLOUR = "1a98ff"
BG_COLOUR   = "0a4aa8"
TILE_COLOUR = "d0e8ff"

from .b1common import (
    B1_ACTIONS,
    B1_ICONS,
    B1_LABELS,
    B1_Y_POS,
    B1_COLLAPSED_W,
)

ICONS = B1_ICONS
LABELS = B1_LABELS
Y_POS = B1_Y_POS
# Layout — everything shifted 200px right vs screen1
B1_W          = 300    # expanded rail
B2_X          = 300
B2_W          = 460
B2_Y          = 85
B2_ITEM_H     = 66
B2_TILE_ROW   = 7
B2_ROW_OFFSET = 2
B2_H          = 941
B2_VISIBLE    = (B2_H - (B2_H % B2_ITEM_H)) // B2_ITEM_H - 1
B2_TOTAL_ROWS = B2_VISIBLE + 4

B3_X          = B2_X + B2_W + 5   # 765
B3_W          = 620
B3_Y_TIME     = 381
B3_Y_CHANNELS = 415
B3_ITEM_H     = 66
B3_VISIBLE    = 10

B5_X          = B3_X + B3_W       # 1385
B5_W_SKIN     = 530                # 1385+530=1915, 5px from edge


# =============================================================================
# Screen 2
# =============================================================================


def _data_folder_label():
    """Data folder for Settings -> Other (a change shows after restart)."""
    try:
        from .paths import HDD, NO_DISK, DATA_FOLDER_FILE
        try:
            with open(DATA_FOLDER_FILE) as f:
                saved = f.read().strip()
        except Exception:
            saved = ""
        if saved and saved.rstrip("/") != HDD.rstrip("/"):
            return "%s (after restart)" % saved
        return HDD + ("  (memory only!)" if NO_DISK else "")
    except Exception:
        return "unknown"


def _picon_root():
    """Current picon folder (Settings -> Other -> Picons)."""
    try:
        from .backend.picon import get_root
        return get_root()
    except Exception:
        return HDD + "/picon/"


def _real_providers(providers):
    """Provider settings list: actual providers only (not My Bouquets),
    switched-on ones first and switched-off ones (#http in playlists.txt,
    or Enable: No) grouped at the bottom, each group in playlist order."""
    real = [(n, p) for n, p in (providers or [])
            if not getattr(p, "is_bouquet", False)]
    return ([t for t in real if getattr(t[1], "enabled", True)]
            + [t for t in real if not getattr(t[1], "enabled", True)])


class Screen2(B1Common, B2Common, B3Common, Screen):

    def __init__(self, session, content_screen=None):
        Screen.__init__(self, session)

        # Whichever screen opened this one — Screen1 (Live TV) today,
        # eventually Series/Movies too. B2/B3 mirroring asks it
        # directly for its rows (see _b2_mirror/_b3_mirror and
        # Screen1.get_mirror_b2/get_mirror_b3) instead of assuming
        # Live TV and reading global state, which is what let Series'
        # own B2 data leak into this mirror before. A content screen
        # that doesn't implement these (or is None) just mirrors
        # nothing — honest blank, not wrong data.
        self._content_screen = content_screen

        # Focus state: b1 | b6 | b7 | b8
        self._focus   = "b1"
        self._b1_sel  = state.b1_sel
        # This screen's rail is always expanded (300px, always
        # interactive) — unlike Series' collapsible one, it never
        # toggles. B1Common._b1_ok() checks this before dispatching.
        self._b1_expanded = True
        self._other_sel = 0

        # Which of this screen's own full-width local panels (if any)
        # is currently on screen — None | "recordings". Independent of
        # self._focus, which can move to "b1" for rail navigation while
        # the panel itself stays visible underneath (see
        # _b1_before_dispatch and each panel's show/hide methods).
        self._b2_active_local_panel = None

        # Whether the Settings sidebar is currently shown. Tracked
        # separately from _b2_active_local_panel above — Settings only
        # ever covers its own narrow sidebar, so it can be stacked on
        # top of Recordings instead of replacing it, unlike everything
        # else here.
        self._b6_showing = False

        # What Settings should restore on Exit — None (bare Live TV
        # mirror) | "recordings" | "series". Settings is a narrow
        # right-hand sidebar (see OV_X below), not a full-width
        # replacement like Recordings/Series, so it doesn't "replace"
        # what was showing — it needs to remember it and hand it back.
        self._b6_return_to = None

        # Shared B1 dispatch config (see B1Common._b1_select). This
        # screen isn't itself a destination (no _b1_own_action) — it's
        # a rail overlay on top of Live TV. Recordings/Settings are
        # panels it can show directly; Series needs a whole other
        # screen opened on top.
        self._b1_local_actions = {
            "recordings": self._show_recordings,
            "settings":   self._show_b6,
        }
        self._b1_nested_actions = {
            "series": self._open_series,
            "movies": self._open_movies,
        }

        # Tracks which of the above was actually opened, so the close
        # handler can restore the correct rail selection instead of
        # assuming it's always Series.
        self._nested_media_label = "Series"

        # B10 — Directory browser state
        self._b10_entries = []
        self._b10_sel     = 0
        self._b10_scroll  = 0
        self._b10_visible = 12
        self._b10_target  = None   # "recording" or "playlists"

        # Recordings browser state — lists the contents (files and
        # folders) of the recordings folder set in Settings > Other.
        # Browsing never goes above that configured root. Listing only
        # for now; play/delete/etc. are a deliberate later follow-up.
        self._rec_entries = []
        self._rec_sel     = 0
        self._rec_scroll  = 0
        self._rec_root    = None
        self._rec_path    = None

        # B6 — Settings menu
        self._b6_sel   = 0
        self._b6_items = ["Status & Logs", "Playlists", "EPG", "Appearance",
                          "Playback", "Parental Controls", "Other", "About"]

        # EPG settings panel
        self._epg_sel = 0
        self._epg_items = [
            "Refresh Time", "Refresh All", "Status",
            "Clear Picon Cache", "Picons",
            "Fill Guide Gaps", "Fill Gaps From", "Fill Gaps Now"
        ]
        self._epg_fetch_hour = 8


        # B7 — Provider list
        self._b7_sel      = 0
        self._b7_scroll   = 0
        self._b7_visible  = 9

        # B8 — Provider settings (7 items)
        self._b8_provider = None
        self._b8_sel      = 0
        self._b8_items    = [
            "Enabled",
            "Live TV",
            "Movies",
            "Series",
            "Refresh Interval",
            "EPG Offset",
            "Update Now",
            "Update EPG",
            "Test Connection",
        ]
        self._b8_values   = {}
        self._b8_scroll   = 0       # 8 rows show at once; the list scrolls

        # B9 — Category management
        self._b9_sel      = 0
        self._b9_scroll   = 0
        # Must match the actual number of b9_item widgets created below
        # (range(10)) — this was 12, so the scroll math assumed 2 more
        # rows existed than were ever built. Whenever the highlight
        # landed on tile_row 10/11 it moved to a widget that doesn't
        # exist, and the highlight itself scrolled past the last real
        # row — exactly the "tile disappears at the bottom" symptom.
        self._b9_visible  = 10
        self._b9_cats     = []   # snapshot — never changes while B9 is open
        self._b9_type     = "live"
        self._b9_disabled = set()

        # B2 display mirrors screen1 state (read-only)
        self._b2_flat       = []
        self._b2_open       = None
        self._b2_sel        = 0
        self._b2_providers  = []
        self._b2_tile_row   = B2_TILE_ROW
        self._b2_row_offset = B2_ROW_OFFSET
        self._b2_total_rows = B2_TOTAL_ROWS

        # ── Build skin ────────────────────────────────────────────────
        x2     = B2_X
        y2     = B2_Y
        ih     = B2_ITEM_H
        tile_y = y2 + B2_TILE_ROW * ih

        # B1 icon and label widgets at original Y_POS positions
        icon_xml = ""
        for i, (icon, y) in enumerate(zip(ICONS, Y_POS)):
            icon_xml += (
                '<widget name="ic%d" position="22,%d" size="56,56"'
                ' pixmap="%s%s" zPosition="4" alphatest="blend"/>' % (
                    i, y + 22, ICON_PATH, icon)
            )
            icon_xml += (
                '<widget name="b1_lbl%d" position="100,%d" size="190,56"'
                ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
                ' backgroundColor="#%s" valign="center" halign="left"'
                ' transparent="1" zPosition="4"/>' % (i, y + 22, RAIL_COLOUR)
            )

        b2_xml = ""
        for i in range(B2_TOTAL_ROWS):
            if i - B2_ROW_OFFSET == B2_TILE_ROW:
                continue
            ry = y2 + ((i - B2_ROW_OFFSET) * ih)
            b2_xml += (
                '<widget name="b2_row%d" position="%d,%d" size="%d,%d"'
                ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
                ' backgroundColor="#%s" valign="center" noWrap="1" zPosition="3"/>' % (
                    i, x2 + 5, ry, B2_W - 10, ih, BG_COLOUR)
            )

        self.skin = (
            '<screen name="E2iPTV_S2" position="0,0" size="1920,1080" flags="wfNoBorder">'

            '<eLabel position="0,0"   size="1920,1080" backgroundColor="#0a4aa8" zPosition="0"/>'
            '<widget name="b1_bg" position="0,0"   size="%d,1080"   backgroundColor="#%s"     zPosition="1"/>' % (B1_W, RAIL_COLOUR) +
            '<widget name="b1_title" position="0,10" size="%d,80"'
            ' font="E2iRegular;80" foregroundColor="#FFFFFF"'
            ' backgroundColor="#%s" valign="center" halign="center" zPosition="4"/>' % (B1_W, RAIL_COLOUR) +
            # B1 selection tile — starts off-screen, moved by navigation
            '<widget name="b1_tile" position="5,3000" size="290,66"'
            ' backgroundColor="#33ddcc" zPosition="3"/>'
            '<eLabel position="%d,0" size="%d,1080"   backgroundColor="#%s"     zPosition="2"/>' % (B2_X, B2_W, BG_COLOUR) +
            '<widget name="b2b3_divider" position="%d,0" size="5,1080"    backgroundColor="#0a4aa8" zPosition="3"/>' % (B2_X + B2_W,) +
            '<eLabel position="%d,0" size="%d,1080"   backgroundColor="#%s"     zPosition="2"/>' % (B3_X, B3_W, BG_COLOUR) +

            icon_xml +
            b2_xml +

            '<widget name="b2_tile" position="%d,%d" size="%d,%d"'
            ' font="E2iRegular;28" foregroundColor="#000000"'
            ' backgroundColor="#%s" valign="center" noWrap="1" zPosition="4"/>' % (
                x2 + 5, tile_y, B2_W - 10, ih, TILE_COLOUR) +

            # Same position/size as b2_tile above — only ever shown
            # when _b2_mirror's clamping (see its own comment) has
            # moved b2_tile away from this static row to somewhere
            # else near the top/bottom of the list. No b2_rowN widget
            # was ever built for this exact row (the skin loop below
            # deliberately skips it, on the assumption b2_tile always
            # sits here) — without this, clamping left it as a genuine
            # gap in the rendered rows: no widget at all covering that
            # position, rather than the row content that actually
            # belongs there whenever the highlight has moved off.
            '<widget name="b2_row_tr_fallback" position="%d,%d" size="%d,%d"'
            ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
            ' backgroundColor="#%s" valign="center" noWrap="1" zPosition="3"/>' % (
                x2 + 5, tile_y, B2_W - 10, ih, BG_COLOUR) +

            # PiG — same position as screen1 (user-adjusted)
            '<widget source="session.VideoPicture" render="Pig"'
            ' position="784,20" size="560,315"'
            ' backgroundColor="#ff000000" transparent="1" zPosition="10"/>'
            '<eLabel position="794,10" size="560,315" backgroundColor="transparent" zPosition="11"/>'

            # Plain opaque cover for the Pig above — shown by default,
            # only moved aside once the content screen is confirmed to
            # have genuine Live TV data (see _sync_pig_cover). This is
            # a completely separate GUI panel, not the Pig/video-source
            # widget itself — no video-service interaction, unlike the
            # documented freeze risk elsewhere in this file.
            # Sized generously beyond both the Pig (784,20,560,315) and
            # its decorative border eLabel just above (794,10,560,315)
            # — they don't share exactly the same bounds, and a small
            # gap left a strip of the border visible.
            '<widget name="pig_cover" position="764,0" size="610,355"'
            ' backgroundColor="#%s" zPosition="12"/>'
            % BG_COLOUR +

            # Info panel — above B5, clipped to available width
            '<widget name="s1_info_title"'
            ' position="%d,10" size="%d,54"'
            ' font="E2iRegular;40" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0a4aa8" valign="center" zPosition="5"/>' % (B5_X + 5, B5_W_SKIN - 10) +
            '<widget name="s1_info_times"'
            ' position="%d,68" size="%d,36"'
            ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0a4aa8" valign="center" zPosition="5"/>' % (B5_X + 5, B5_W_SKIN - 10) +
            '<widget name="s1_info_desc"'
            ' position="%d,108" size="%d,165"'
            ' font="E2iRegular;26" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0a4aa8" valign="top" zPosition="5"/>' % (B5_X + 5, B5_W_SKIN - 10) +

            # Time bar
            '<widget name="b3_time"'
            ' position="%d,%d" size="%d,34"'
            ' font="E2iRegular;24" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0a4aa8" valign="center" halign="center" zPosition="3"/>' % (B3_X, B3_Y_TIME, B3_W) +
            '<widget name="s1_b5_date"'
            ' position="%d,%d" size="325,34"'
            ' font="E2iRegular;24" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0a4aa8" valign="center" halign="left" zPosition="3"/>' % (B5_X, B3_Y_TIME) +
            '<widget name="s1_b5_mark1"'
            ' position="%d,%d" size="200,34"'
            ' font="E2iRegular;24" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0a4aa8" valign="center" halign="left" zPosition="3"/>' % (B5_X + 325, B3_Y_TIME) +

            # B3 channel list (passive)
            '<widget name="b3_list"'
            ' position="%d,%d" size="%d,%d"'
            ' zPosition="3" backgroundColor="#0a4aa8"/>' % (
                B3_X, B3_Y_CHANNELS, B3_W, B3_VISIBLE * B3_ITEM_H) +

            # B5 EPG mirror
            '<widget name="s1_b5_list"'
            ' position="%d,%d" size="%d,%d"'
            ' zPosition="3" backgroundColor="#0a4aa8"/>' % (
                B5_X, B3_Y_CHANNELS, B5_W_SKIN, B3_VISIBLE * B3_ITEM_H) +

            # B10 — Directory browser (embedded, no separate screen)
            '<widget name="b10_header"    position="3500,20" size="540,60"'
            ' font="E2iRegular;32" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0d1117" halign="left" valign="center" zPosition="27"/>'
            '<widget name="b10_highlight" position="3500,100" size="540,66"'
            ' backgroundColor="#d0e8ff" zPosition="26"/>'
            + "".join(
                '<widget name="b10_item%d" position="3500,100" size="540,66"'
                ' font="E2iRegular;26" foregroundColor="#FFFFFF"'
                ' halign="left" valign="center" transparent="1" zPosition="27"/>' % i
                for i in range(12)
            ) +

            # Recordings browser (embedded, no separate screen) — lists
            # the contents of the folder set in Settings > Other. Full
            # width (everything right of the rail), same blue as B2/B3
            # rather than the near-black Settings uses, since this is a
            # content-browsing screen, not a utility/settings one.
            '<widget name="rec_bg"     position="3500,0" size="%d,1080"'
            ' backgroundColor="#%s" zPosition="20"/>'
            % (1920 - B1_W, BG_COLOUR) +
            '<widget name="rec_header" position="3500,20" size="%d,60"'
            ' font="E2iRegular;36" foregroundColor="#FFFFFF"'
            ' backgroundColor="#%s" halign="left" valign="center" zPosition="22"/>'
            % (1920 - B1_W - 60, BG_COLOUR) +
            # One row's worth of gap at the top of the content area,
            # showing the currently-selected recording's programme name
            # and description — the actual list starts one row lower.
            '<widget name="rec_info_title" position="3500,112" size="%d,36"'
            ' font="E2iRegular;32" foregroundColor="#FFFFFF"'
            ' backgroundColor="#%s" halign="left" valign="center" zPosition="22"/>'
            % (1920 - B1_W - 60, BG_COLOUR) +
            '<widget name="rec_info_desc" position="3500,152" size="%d,28"'
            ' font="E2iRegular;22" foregroundColor="#aaddff"'
            ' backgroundColor="#%s" halign="left" valign="center" zPosition="22"/>'
            % (1920 - B1_W - 60, BG_COLOUR) +
            '<widget name="rec_list"   position="3500,183" size="%d,877"'
            ' zPosition="22" backgroundColor="#%s"/>'
            % (1920 - B1_W - 60, BG_COLOUR) +
            # Semi-transparent dark background. Deliberately a higher
            # zPosition than rec_bg (20) — Settings can now be shown
            # on top of Recordings without hiding it first (see
            # _b1_before_dispatch), so it must reliably win the
            # stacking order in the area they overlap.
            '<widget name="ov_bg" position="3500,0" size="568,1080"'
            ' backgroundColor="#0d1117" zPosition="25"/>'

            # B6 Settings menu
            '<widget name="b6_header" position="3500,20" size="548,60"'
            ' font="E2iRegular;36" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0d1117" halign="left" valign="center" zPosition="27"/>'
            '<widget name="b6_highlight" position="3500,100" size="548,100"'
            ' backgroundColor="#d0e8ff" zPosition="26"/>'
            + "".join(
                '<widget name="b6_item%d" position="3500,100" size="548,100"'
                ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
                ' halign="left" valign="center" transparent="1" zPosition="27"/>' % i
                for i in range(8)
            ) +

            # EPG settings panel
            '<widget name="epg_header" position="3500,20" size="548,60"'
            ' font="E2iRegular;36" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0d1117" halign="left" valign="center" zPosition="27"/>'
            '<widget name="epg_highlight" position="3500,100" size="548,100"'
            ' backgroundColor="#d0e8ff" zPosition="26"/>'
            + "".join(
                '<widget name="epg_item%d" position="3500,100" size="548,100"'
                ' font="E2iRegular;26" foregroundColor="#FFFFFF"'
                ' halign="left" valign="center" transparent="1" zPosition="27"/>' % i
                for i in range(8)
            ) +

            # B7 Provider list — two rows per provider (name+status, expires+conn)
            '<widget name="b7_header" position="3500,20" size="548,60"'
            ' font="E2iRegular;36" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0d1117" halign="left" valign="center" zPosition="27"/>'
            '<widget name="b7_highlight" position="3500,100" size="548,100"'
            ' backgroundColor="#d0e8ff" zPosition="26"/>'
            + "".join(
                '<widget name="b7_name%d" position="3500,100" size="398,52"'
                ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
                ' halign="left" valign="center" transparent="1" zPosition="27"/>' % i
                for i in range(10)
            )
            + "".join(
                '<widget name="b7_status%d" position="3500,100" size="140,52"'
                ' font="E2iRegular;24" foregroundColor="#FFFFFF"'
                ' halign="right" valign="center" transparent="1" zPosition="27"/>' % i
                for i in range(10)
            )
            + "".join(
                '<widget name="b7_info%d" position="3500,100" size="548,40"'
                ' font="E2iRegular;22" foregroundColor="#aaaaaa"'
                ' halign="left" valign="center" transparent="1" zPosition="27"/>' % i
                for i in range(10)
            ) +

            # B8 Provider settings (7 items)
            '<widget name="b8_header" position="3500,20" size="548,60"'
            ' font="E2iRegular;36" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0d1117" halign="left" valign="center" zPosition="27"/>'
            '<widget name="b8_highlight" position="3500,100" size="548,100"'
            ' backgroundColor="#d0e8ff" zPosition="26"/>'
            + "".join(
                '<widget name="b8_item%d" position="3500,100" size="548,100"'
                ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
                ' halign="left" valign="center" transparent="1" zPosition="27"/>' % i
                for i in range(8)
            ) +

            # B9 Category management
            '<widget name="b9_header" position="3500,20" size="548,60"'
            ' font="E2iRegular;36" foregroundColor="#FFFFFF"'
            ' backgroundColor="#0d1117" halign="left" valign="center" zPosition="27"/>'
            '<widget name="b9_highlight" position="3500,100" size="548,100"'
            ' backgroundColor="#d0e8ff" zPosition="26"/>'
            + "".join(
                '<widget name="b9_item%d" position="3500,100" size="548,100"'
                ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
                ' halign="left" valign="center" transparent="1" zPosition="27"/>' % i
                for i in range(10)
            ) +

            '</screen>'
        )

        # ── Register widgets ──────────────────────────────────────────
        self["b1_bg"]         = Label("")
        self["b2_tile"]       = Label("")
        self["b2_row_tr_fallback"] = Label("")
        self["b2b3_divider"]  = Label("")
        self["b3_time"]       = Label("")
        self["b3_list"]       = IE2iList(B3_ITEM_H)
        self["s1_b5_list"]    = IE2iList(B3_ITEM_H)
        self["s1_b5_date"]    = Label("")
        self["s1_b5_mark1"]   = Label("")
        self["s1_info_title"] = Label("")
        self["s1_info_times"] = Label("")
        self["s1_info_desc"]  = Label("")

        for i in range(B2_TOTAL_ROWS):
            if i - B2_ROW_OFFSET == B2_TILE_ROW:
                continue
            self["b2_row%d" % i] = Label("")

        self["b1_tile"]  = Label("")
        self["b1_title"] = Label("E2iPTV")

        for i in range(len(LABELS)):
            self["ic%d"      % i] = Pixmap()
            self["b1_lbl%d"  % i] = Label(LABELS[i])

        # Overlay widgets
        self["ov_bg"]       = Label("")
        self["pig_cover"]   = Label("")
        self["b6_header"]   = Label("")
        self["b6_highlight"] = Label("")
        self["b7_header"]   = Label("")
        self["b7_highlight"] = Label("")
        self["b8_header"]   = Label("")
        self["b8_highlight"] = Label("")
        for i in range(8):
            self["b6_item%d" % i] = Label("")

        self["epg_header"] = Label("")
        self["epg_highlight"] = Label("")
        for i in range(8):
            self["epg_item%d" % i] = Label("")

        for i in range(10):
            self["b7_name%d"   % i] = Label("")
            self["b7_status%d" % i] = Label("")
            self["b7_info%d"   % i] = Label("")
        for i in range(8):
            self["b8_item%d" % i] = Label("")
        self["b10_header"]    = Label("")
        self["b10_highlight"] = Label("")
        for i in range(12):
            self["b10_item%d" % i] = Label("")
        self["rec_bg"]        = Label("")
        self["rec_header"]    = Label("")
        self["rec_info_title"] = Label("")
        self["rec_info_desc"]  = Label("")
        self["rec_list"]   = IE2iList(self.REC_ITEM_H)
        # Recording titles get their own larger font now that the row is
        # just title / channel+times (no date prefix eating into line 1
        # any more) — bumped from the shared IE2iList default of 28.
        self["rec_list"].l.setFont(0, gFont("E2iRegular", 32))
        self["b9_header"]    = Label("")
        self["b9_highlight"] = Label("")
        for i in range(10):
            self["b9_item%d" % i] = Label("")

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "cancel": self._on_exit,
                "left":   self._on_left_key,
                "right":  self._on_right_key,
                "up":     self._on_up,
                "down":   self._on_down,
                "ok":     self._on_ok,
            },
            -1
        )

        self.onLayoutFinish.append(self._on_ready)
        self.onShown.append(self._refresh)
        self.onShow.append(self._refresh)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _on_ready(self):
        self._b1_refresh()
        self._refresh()

    # ------------------------------------------------------------------
    # Exit / Back navigation
    # ------------------------------------------------------------------

    def _on_exit(self):
        _nav_debug("screen2._on_exit: focus=%r" % (self._focus,))
        if (self._focus == "recordings"
                and getattr(self, "_rec_mode", "files") in ("sched", "trash")):
            self._recordings_load(self._rec_path)
            self._recordings_render()
            return
        if self._focus == "b10":
            self._hide_b10()
            if self._b10_target == "music":
                self._show_b6()
                self._playback_menu()
            else:
                self._show_other()
        elif self._focus == "b9":
            self._hide_panel("b9", 10)

            # Manage Categories changes are saved immediately.
            # Refresh the corresponding B2 category list now so
            # newly hidden/unhidden categories are reflected without
            # requiring a provider switch.
            try:
                p = self._b8_provider

                if p:

                    from .backend.provider_category_settings import load

                    # Reload the authoritative persistent category state.
                    p.disabled_cats = load(p)

                    # Refresh the live category list using the same
                    # mechanism B2 uses when opening a provider.
                    cats_raw = state.backend.get_live_categories(p)
                    cats = self._b2_categories_to_list(cats_raw)

                    # Find this provider in the shared provider list.
                    prov_idx = next(
                        (
                            i
                            for i, (name, provider) in enumerate(state.providers)
                            if provider is p
                        ),
                        None
                    )

                    if prov_idx is not None:

                        self._b2_open_cats[prov_idx] = cats
                        self._b2_open_set.add(prov_idx)

                        state.open_prov_idx = prov_idx
                        state.open_prov = p
                        state.categories = cats

                        # Rebuild the actual B2 flat list.
                        self._b2_rebuild_flat()

                        # Keep the selection on this provider.
                        flat_idx = next(
                            (
                                i
                                for i, (label, data, is_cat)
                                in enumerate(self._b2_flat)
                                if (
                                    not is_cat
                                    and data.get("provider") is p
                                )
                            ),
                            0
                        )

                        self._b2_sel = flat_idx
                        self._b2_refresh()

            except Exception:
                pass

            self._show_b8()
        elif self._focus == "b8":
            self._hide_panel("b8", 8)
            # Switching a provider on/off moves it between the groups in
            # the list - keep the highlight on the same provider.
            try:
                order = [p for _n, p in _real_providers(state.providers)]
                if self._b8_provider in order:
                    self._b7_sel = order.index(self._b8_provider)
                    if self._b7_sel < self._b7_scroll:
                        self._b7_scroll = self._b7_sel
                    elif self._b7_sel >= self._b7_scroll + self._b7_visible:
                        self._b7_scroll = self._b7_sel - self._b7_visible + 1
            except Exception:
                pass
            self._show_b7()
        elif self._focus == "b7":
            self._hide_b7()
            self._show_b6()
        elif self._focus in ("other", "about"):
            self._hide_panel("b6", 8)
            self._hide_overlay()
            self._show_b6()
        elif self._focus == "epg":
            self._hide_epg()
            self._show_b6()
        elif self._focus == "b6":
            # Unlike Recordings, Settings is a narrow sidebar meant to
            # be a quick, dismissable dialog — Exit actually closes it
            # and hands back whatever was open before it, rather than
            # leaving it parked on the rail.
            self._b1_close_settings()
        elif self._focus == "recordings":
            # Only move keyboard focus to the rail here — do NOT hide
            # Recordings. It should stay on screen until an actually
            # different destination is chosen (see _on_ok's b1 branch,
            # which hides it at that point instead). Same fix as
            # _on_left_key's identical branch — this is the other of
            # the two possible handlers for the same physical < key
            # (see the comment on _hide_recordings for why both exist)
            # and had the exact same gap: never actually re-expanded
            # the rail or shifted Recordings' content back to match.
            self._focus = "b1"
            self._b1_sel = LABELS.index("Recordings")
            state.b1_sel = self._b1_sel
            self._b1_expand_rail()
            self._reposition_recordings_content(B1_W)
            self._b1_refresh()
            self._recordings_render()
        elif self._focus == "b1":
            # The physical < key on this box can arrive as both
            # DirectionActions "left" and OkCancelActions "cancel".
            # _on_left_key() has already handled the B1 navigation.
            # Do not close Screen2 again when the duplicate cancel
            # action arrives.
            pass
        else:
            self.close()

    def _on_left_key(self):
        _nav_debug("screen2._on_left_key: focus=%r" % (self._focus,))
        if self._focus == "b1":
            self.close()
        elif self._focus == "recordings":
            # Same as _on_exit above — keep Recordings on screen, just
            # move keyboard focus to the rail. The rail itself still
            # needs to actually re-expand here though (labels, title,
            # tile, background — see _b1_expand_rail) — this branch
            # never called anything that did that at all before, so
            # the rail stayed visually collapsed (no labels, no
            # highlight) despite focus genuinely having moved to it.
            # Recordings' own content also needs shifting back out to
            # the now-wider rail's actual width, or the rail's re-
            # expansion covers the first couple hundred pixels of it.
            self._focus = "b1"
            self._b1_sel = LABELS.index("Recordings")
            state.b1_sel = self._b1_sel
            self._b1_expand_rail()
            self._reposition_recordings_content(B1_W)
            self._b1_refresh()
            self._recordings_render()
        elif self._focus == "b8":
            self._b8_change_value(-1)
        elif self._focus == "b9":
            self._b9_private_toggle()
        elif self._focus == "epg" and self._epg_sel == 0:
            self._epg_change_hour(-1)
        elif self._focus == "epg" and self._epg_sel == 5:
            self._fill_toggle()
        elif self._focus == "other" and 2 <= self._other_sel <= 5:
            self._other_change_offset(-1)

    def _on_right_key(self):
        _nav_debug("screen2._on_right_key: focus=%r" % (self._focus,))
        if self._focus == "b1":
            action = B1_ACTIONS.get(LABELS[self._b1_sel])
            if action == "recordings" and self._b2_active_local_panel == "recordings":
                # Recordings is still on screen behind the rail — go
                # back into it instead of closing Screen2 out from
                # under it. Mirrors _on_left_key's own fix: the rail
                # needs to actually re-collapse here (it had re-
                # expanded while focus was on it), and Recordings'
                # content needs shifting back to match, or the two end
                # up sized for different rail widths again.
                self._focus = "recordings"
                self._b1_collapse_rail()
                self._reposition_recordings_content(B1_COLLAPSED_W)
                self._recordings_render()
            else:
                self.close()
        elif self._focus == "b8":
            self._b8_change_value(+1)
        elif self._focus == "b9":
            self._b9_private_toggle()
        elif self._focus == "epg" and self._epg_sel == 0:
            self._epg_change_hour(+1)
        elif self._focus == "epg" and self._epg_sel == 5:
            self._fill_toggle()
        elif self._focus == "other" and 2 <= self._other_sel <= 5:
            self._other_change_offset(+1)

    def _other_change_offset(self, direction):
        import json
        keys = ["rec_start_offset", "rec_end_offset",
                "catchup_start_offset", "catchup_end_offset"]
        key  = keys[self._other_sel - 2]
        cur  = getattr(state, key, 0)
        cur  = max(0, min(5, cur + direction))
        setattr(state, key, cur)
        try:
            data = {}
            try:
                data = json.load(open(DATA + "/cache/app_settings.json"))
            except Exception:
                pass
            data[key] = cur
            json.dump(data, open(DATA + "/cache/app_settings.json", "w"))
        except Exception:
            pass
        self._other_refresh()

    # ------------------------------------------------------------------
    # Up / Down
    # ------------------------------------------------------------------

    def _on_up(self):
        if self._focus == "b1":
            self._b1_up()
        elif self._focus == "b10":
            if self._b10_sel > 0:
                self._b10_sel -= 1
                self._b10_refresh()
        elif self._focus == "recordings":
            if self._rec_sel > 0:
                self._rec_sel -= 1
                if self._rec_sel < self._rec_scroll:
                    self._rec_scroll = self._rec_sel
                self._recordings_render()
        elif self._focus == "other":
            if self._other_sel > 0:
                self._other_sel -= 1
                self._other_refresh()
        elif self._focus == "b6":
            if self._b6_sel > 0:
                self._b6_sel -= 1
                self._b6_refresh()
        elif self._focus == "epg":
            if self._epg_sel > 0:
                self._epg_sel -= 1
                self._epg_refresh()
        elif self._focus == "b7":
            providers = _real_providers(state.providers)
            if self._b7_sel > 0:
                self._b7_sel -= 1
                if self._b7_sel < self._b7_scroll:
                    self._b7_scroll = self._b7_sel
                self._b7_refresh()
        elif self._focus == "b8":
            if self._b8_sel > 0:
                self._b8_sel -= 1
                self._b8_refresh()
        elif self._focus == "b9":
            if self._b9_sel > 0:
                self._b9_sel -= 1
                self._b9_refresh()

    def _on_down(self):
        if self._focus == "b1":
            self._b1_down()
        elif self._focus == "b10":
            if self._b10_sel < len(self._b10_entries) - 1:
                self._b10_sel += 1
                self._b10_refresh()
        elif self._focus == "recordings":
            if self._rec_sel < len(self._rec_entries) - 1:
                self._rec_sel += 1
                if self._rec_sel >= self._rec_scroll + self.REC_VISIBLE:
                    self._rec_scroll = self._rec_sel - self.REC_VISIBLE + 1
                self._recordings_render()
        elif self._focus == "other":
            if self._other_sel < 7:
                self._other_sel += 1
                self._other_refresh()
        elif self._focus == "b6":
            if self._b6_sel < len(self._b6_items) - 1:
                self._b6_sel += 1
                self._b6_refresh()
        elif self._focus == "epg":
            if self._epg_sel < len(self._epg_items) - 1:
                self._epg_sel += 1
                self._epg_refresh()
        elif self._focus == "b7":
            providers = _real_providers(state.providers)
            if self._b7_sel < len(providers) - 1:
                self._b7_sel += 1
                if self._b7_sel >= self._b7_scroll + self._b7_visible:
                    self._b7_scroll = self._b7_sel - self._b7_visible + 1
                self._b7_refresh()
        elif self._focus == "b8":
            if self._b8_sel < len(self._b8_items) - 1:
                self._b8_sel += 1
                self._b8_refresh()
        elif self._focus == "b9":
            cats = self._b9_cats
            # Row list is "Hide All" + "Unhide All" + one row per category
            # (see _b9_refresh/_b9_toggle), so the last valid index is
            # len(cats) + 1, not len(cats) - 1 — the old bound left the
            # final 2 categories permanently unreachable (and, for a
            # provider with only 1-2 categories, blocked all of them).
            if self._b9_sel < len(cats) + 1:
                self._b9_sel += 1
                self._b9_refresh()

    def _open_series(self):
        self._nested_media_label = "Series"
        self.session.openWithCallback(
            self._handle_child_close, MediaBrowser, "series"
        )

    def _open_movies(self):
        self._nested_media_label = "Movies"
        self.session.openWithCallback(
            self._handle_child_close, MediaBrowser, "movies"
        )

    def _b1_before_dispatch(self, action):
        """Hook called by the shared B1Common dispatch just before it
        acts on a newly chosen rail item."""
        active = self._b2_active_local_panel

        if action == "settings":
            if active != "settings":
                # Remember what Settings should hand back on Exit.
                self._b6_return_to = active
            if active == "recordings":
                # Sidebar-only — Recordings stays exactly as it is
                # underneath, nothing to hide.
                return
            return

        # Navigating to anything other than Settings: close Settings
        # first if it's currently stacked on top of something.
        if self._b6_showing:
            self._hide_b6()

        if active and active != action:
            if active == "recordings":
                self._hide_recordings()

    def _handle_child_close(self, result=None):
        """Callback for every rail-screen this screen opens on top of
        itself (currently just Series). Resolves the shared
        "goto:<action>" close protocol — see B1Common._b1_select — by
        showing the right local panel, opening another nested screen,
        or forwarding to whoever opened this screen if it's something
        this screen can't satisfy itself (Live TV, Exit)."""

        if not result or not str(result).startswith("goto:"):
            # Closed normally — return to the rail with the screen we
            # actually opened (Series or Movies) still selected. Don't
            # expose the Live TV mirror that belongs to the Screen1
            # underneath this screen.
            self._focus = "b1"
            self._b1_sel = LABELS.index(self._nested_media_label)
            state.b1_sel = self._b1_sel
            self._b1_refresh()
            self._clear_live_mirror()
            return

        action = result[len("goto:"):]

        if action == "recordings":
            self._show_recordings()
            return

        if action == "settings":
            self._b6_return_to = (
                "movies"
                if self._nested_media_label == "Movies"
                else "series"
            )
            self._show_b6()
            return

        if action == "series":
            self._open_series()
            return

        if action == "movies":
            self._open_movies()
            return

        # "live" / "exit" / anything else this screen can't satisfy
        # itself — this screen isn't a destination on its own, forward
        # to whoever opened it (Screen1).
        self.close("goto:%s" % action)

    # ------------------------------------------------------------------
    # OK — go deeper
    # ------------------------------------------------------------------

    def _on_ok(self):
        _nav_debug("screen2._on_ok: focus=%r" % (self._focus,))
        if self._focus == "b1":
            self._b1_ok()
        elif self._focus == "recordings":
            self._recordings_ok()
        elif self._focus == "b10":
            self._b10_ok()
        elif self._focus == "other":
            if self._other_sel < 2 or self._other_sel in (6, 7):
                self._other_open_browser()  # location items only
        elif self._focus == "b6":
            item = self._b6_items[self._b6_sel]
            if item == "Playlists":
                self._b7_sel    = 0
                self._b7_scroll = 0
                self._show_b7()
            elif item == "EPG":
                self._show_epg()
            elif item == "Other":
                self._show_other()
            elif item == "About":
                self._show_about()
            elif item == "Playback":
                self._playback_menu()
            elif item == "Status & Logs":
                # Logs list + viewer (log_viewer.py) open on top of the
                # Settings panel; Exit from the list lands back here.
                from .log_viewer import open_status_logs
                open_status_logs(self.session)
        elif self._focus == "epg":
            self._epg_ok()
        elif self._focus == "b7":
            providers = _real_providers(state.providers)
            if providers:
                name, provider = providers[self._b7_sel]
                self._b8_provider = provider
                self._b8_sel      = 0
                self._b8_scroll   = 0
                self._show_b8()
        elif self._focus == "b8":
            self._b8_action()
        elif self._focus == "b9":
            self._b9_toggle()

    # ------------------------------------------------------------------
    # Overlay show / hide helpers
    # ------------------------------------------------------------------

    OV_X = 1352   # 8px right of mini TV right edge (784+560=1344+8)
    OV_W = 563    # to screen right edge (1920-1352-5)

    def _show_overlay(self):
        try:
            self["ov_bg"].instance.move(ePoint(self.OV_X, 0))
        except Exception:
            pass

    def _hide_overlay(self):
        try:
            self["ov_bg"].instance.move(ePoint(3500, 0))
        except Exception:
            pass

    def _b1_close_settings(self):
        """Exit from Settings: close the sidebar and hand back exactly
        what was open before it, rather than leaving things at the
        bare rail. See self._b6_return_to, captured in
        _b1_before_dispatch / _handle_child_close when Settings was
        opened."""
        self._hide_b6()

        target = self._b6_return_to
        self._b6_return_to = None

        if target == "series":
            self._open_series()
            return

        if target == "movies":
            self._open_movies()
            return

        if target == "recordings":
            # Recordings was never actually hidden — Settings was just
            # stacked on top of it (see _b1_before_dispatch) — so just
            # hand keyboard focus back rather than reloading it.
            self._focus = "recordings"
            return

        self._focus = "b1"
        self._b1_sel = LABELS.index("Settings")
        state.b1_sel = self._b1_sel
        self._b1_refresh()

    def _show_b6(self):
        self._show_overlay()
        x = self.OV_X + 20
        try:
            self["b6_header"].setText("Settings")
            self["b6_header"].instance.move(ePoint(x, 20))
        except Exception:
            pass
        self._b6_sel = 0
        self._b6_refresh()
        self._focus = "b6"
        self._b6_showing = True

        if self._b2_active_local_panel == "recordings":
            # Stacked on top of Recordings — its own content is still
            # showing underneath, nothing more to do.
            return

        # Settings only covers the right-hand sidebar (OV_X onwards) —
        # it never covers B2/B3. Whatever was cleared by hiding a
        # previous full-width panel (or was simply never populated
        # yet, e.g. straight from a Series hand-off) needs to be
        # showing correctly in that uncovered area, not left blank.
        try:
            self._b2_mirror()
        except Exception:
            pass
        try:
            self._b3_mirror()
        except Exception:
            pass
        try:
            self._b5_mirror()
        except Exception:
            pass

    def _hide_b6(self):
        try:
            self["b6_header"].instance.move(ePoint(3500, 0))
            self["b6_highlight"].instance.move(ePoint(3500, 0))
            for i in range(8):
                self["b6_item%d" % i].instance.move(ePoint(3500, 0))
        except Exception:
            pass
        self._hide_overlay()
        self._b6_showing = False

    # ------------------------------------------------------------------
    # Recordings browser — lists the contents (files and folders) of
    # the recordings folder set in Settings > Other. Reached directly
    # from B1, same level as Settings. Listing only for now — selecting
    # a recording (a file) doesn't do anything yet; play/delete/rename
    # actions are a deliberate later follow-up.
    # ------------------------------------------------------------------

    def _b1_collapse_rail(self):
        # Reuses b1common.py's own proven _b1_collapse/_b1_apply_visual_state
        # directly (already inherited — this screen IS a B1Common
        # subclass) rather than reimplementing label/title/tile
        # handling separately here, which only led to inconsistency
        # with how every other content screen's own collapsed rail
        # actually looks and behaves (Favourites included) — that
        # shared logic already correctly hides b1_tile entirely while
        # collapsed (this screen's own earlier version kept it visible,
        # narrowed — a deliberate but, it turns out, unwanted
        # difference). Only b1_bg needs handling separately here since
        # it doesn't exist on any other B1Common screen (they hardcode
        # their rail background at the collapsed width directly in
        # their own skin, since they're always collapsed and never
        # need it to dynamically resize the way this screen does).
        self._b1_collapse()
        try:
            self["b1_bg"].instance.resize(eSize(B1_COLLAPSED_W, 1080))
        except Exception:
            pass

    def _b1_expand_rail(self):
        self._b1_expand()
        try:
            self["b1_bg"].instance.resize(eSize(B1_W, 1080))
        except Exception:
            pass

    def _reposition_recordings_content(self, rail_w):
        # Shared by _show_recordings (rail_w=B1_COLLAPSED_W, entering)
        # and _on_left_key's "recordings" branch (rail_w=B1_W, keeping
        # Recordings visible while moving focus back to the now-
        # re-expanded rail) — these widgets were all originally built
        # assuming the rail was always B1_W (300), so both entry paths
        # need this, not just the initial collapsed-open: whichever
        # width the rail actually is at needs to be reflected here too,
        # or the two end up out of sync (rail widens back to 300 but
        # content's still sized/positioned for the 100-wide collapsed
        # case, so the rail covers the first ~200px of it — or the
        # reverse, content sized for 300 while the rail's only 100,
        # leaving an ugly gap — either way, whatever moved without the
        # other becoming wrong).
        x = rail_w + 20
        content_w = 1920 - rail_w - 40
        # _recordings_render's own row content (the actual per-row
        # MultiContentEntryText width, not just the containing widget)
        # used to hardcode this same figure assuming a permanently
        # expanded 300-wide rail — remembered here so it can pick up
        # the real, current width too, rather than only the widget
        # itself actually resizing while every row rendered inside it
        # stayed built for the old figure.
        self._rec_content_w = content_w
        try:
            self["rec_bg"].instance.resize(eSize(1920 - rail_w, 1080))
            self["rec_bg"].instance.move(ePoint(rail_w, 0))
            self["rec_header"].instance.resize(eSize(content_w, 60))
            self["rec_header"].instance.move(ePoint(x, 20))
            self["rec_info_title"].instance.resize(eSize(content_w, 36))
            self["rec_info_title"].instance.move(ePoint(x, 112))
            self["rec_info_desc"].instance.resize(eSize(content_w, 28))
            self["rec_info_desc"].instance.move(ePoint(x, 152))
            self["rec_list"].instance.resize(eSize(content_w, 877))
            self["rec_list"].instance.move(ePoint(x, 183))
        except Exception:
            pass

    def _show_recordings(self):
        self._reposition_recordings_content(B1_COLLAPSED_W)
        try:
            self["rec_header"].setText("Recordings")
        except Exception:
            pass
        self._rec_root = getattr(state, 'recording_path', HDD + '/movie/')
        self._recordings_load(self._rec_root)
        self._focus = "recordings"
        self._b2_active_local_panel = "recordings"
        self._recordings_render()
        self._b1_collapse_rail()

        # Keep this rail's own highlight in sync with whatever actually
        # navigated us here — e.g. Recordings chosen from Series' own
        # rail (a different Screen, different _b1_sel), or a fresh
        # app-launch resume — rather than whatever this Screen2
        # instance's rail last happened to show.
        self._b1_sel = state.b1_sel
        self._b1_refresh()

    def _clear_live_mirror(self):
        """Blank Screen2's B2/B3/B5/info panels so the Live TV mirror
        underneath doesn't show through behind whatever else is now in
        focus (the bare rail, Recordings, etc). Single shared spot for
        this — it used to be copy-pasted at each call site and, in one
        of those copies, unreachable in practice (see _hide_recordings)."""
        try:
            self["b2_tile"].setText("")

            for i in range(B2_TOTAL_ROWS):
                name = "b2_row%d" % i
                if name in self:
                    self[name].setText("")

            self["b3_time"].setText("")
            self["b3_list"].setList([])

            self["s1_b5_date"].setText("")
            self["s1_b5_mark1"].setText("")
            self["s1_b5_list"].setList([])

            self["s1_info_title"].setText("")
            self["s1_info_times"].setText("")
            self["s1_info_desc"].setText("")

        except Exception:
            pass

    def _hide_recordings(self):
        try:
            self["rec_bg"].instance.move(ePoint(3500, 0))
            self["rec_header"].instance.move(ePoint(3500, 0))
            self["rec_info_title"].instance.move(ePoint(3500, 0))
            self["rec_info_desc"].instance.move(ePoint(3500, 0))
            self["rec_list"].instance.move(ePoint(3500, 0))
        except Exception:
            pass
        self._b1_expand_rail()
        if self._b2_active_local_panel == "recordings":
            self._b2_active_local_panel = None

        # Returning from Recordings to the B1 rail must not expose the
        # Live TV mirror underneath Screen2. This lives here (rather
        # than in whichever key handler happens to call _hide_recordings)
        # so it always runs, regardless of which of the two exit paths
        # (_on_exit vs _on_left_key) actually fires — the physical < key
        # on this box can trigger either, or both, and the clearing code
        # used to only exist in _on_exit's branch, which _on_left_key
        # firing first (it already changes self._focus away from
        # "recordings") made unreachable in practice.
        self._clear_live_mirror()

    def _parse_recording_filename(self, fname):
        """Best-effort split of a recording with no .json metadata
        sidecar — either a legacy recording made before this plugin
        wrote one, or one made by another app sharing this folder (the
        box's native PVR, E2iPlayer, etc.) — into
        (title, start_ts, channel_name). Falls back to
        (name-without-extension, None, None) if nothing recognisable
        matches."""
        import re, os
        base, _ext = os.path.splitext(fname)

        # This plugin's own convention: "<title>_YYYYMMDD_HHMMSS"
        # ("<title>_CU_YYYYMMDD_HHMMSS" for a catch-up recording).
        m = re.match(r'^(.*?)(?:_CU)?_(\d{8})_(\d{6})$', base)
        if m:
            title, ymd, hms = m.groups()
            try:
                start = int(time.mktime(time.strptime(ymd + hms, "%Y%m%d%H%M%S")))
            except Exception:
                start = None
            return title, start, None

        # The box's native PVR / other recorder plugins convention:
        # "YYYYMMDD HHMM - Service Name - Title".
        m = re.match(r'^(\d{8}) (\d{4}) - (.*?) - (.*)$', base)
        if m:
            ymd, hm, channel, title = m.groups()
            try:
                start = int(time.mktime(time.strptime(ymd + hm, "%Y%m%d%H%M")))
            except Exception:
                start = None
            return title, start, channel

        return base, None, None

    def _read_recording_meta(self, ts_path):
        import json
        try:
            with open(ts_path + ".json", "r") as f:
                return json.load(f)
        except Exception:
            return None

    def _read_native_meta_description(self, ts_path):
        """Best-effort read of the description line from a native
        .meta sidecar (line 3 of: serviceref / name / description /
        time / tags / ...). This exists for the box's own native
        recordings and for anything this plugin records now (see
        _write_native_meta in player.py), so it's a reasonable common
        source for the info line regardless of which recorder actually
        made the file."""
        try:
            with open(ts_path + ".meta", "r") as f:
                lines = f.read().split("\n")
            if len(lines) > 2:
                return lines[2].strip()
        except Exception:
            pass
        return ""

    def _file_fallback_date(self, full):
        """When nothing (our own .json, a native .meta, or the
        filename itself) tells us when a recording was made, fall back
        to the file's own modification time on disk rather than
        showing "Unknown date"."""
        try:
            return int(os.path.getmtime(full))
        except Exception:
            return None

    def _recording_bookmarks(self):
        import json
        try:
            with open(DATA + "/cache/app_settings.json", "r") as f:
                data = json.load(f)
            value = data.get("recording_bookmarks", [])
            return value if isinstance(value, list) else []
        except Exception:
            return []

    def _save_recording_bookmarks(self, bookmarks):
        import json
        path = DATA + "/cache/app_settings.json"
        try:
            data = {}
            try:
                with open(path, "r") as f:
                    data = json.load(f)
            except Exception:
                pass
            data["recording_bookmarks"] = bookmarks
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _recording_add_bookmark(self):
        self._b10_target = "recording_bookmark"
        self._b10_load(
            getattr(self, "_rec_path", None)
            or getattr(state, "recording_path", HDD + "/movie/")
        )
        self._show_b10()

    _tidied_folders = set()

    def _recordings_load(self, path):
        if path not in Screen2._tidied_folders:
            Screen2._tidied_folders.add(path)
            self._tidy_recording_folder(path)
        self._recordings_load_list(path)

    def _recordings_load_list(self, path):
        """List one directory under the recordings root: subfolders
        first, then files, both alphabetical. ".." only appears while
        browsing below the configured root — this never lets you
        browse above the folder set in Settings > Other."""
        import os
        entries = []
        if path.rstrip("/") != "/":
            parent = os.path.dirname(path.rstrip("/")) or "/"
            entries.append({"is_dir": True, "path": parent, "text": ".."})
        # Enigma2's native PVR, other recorder plugins that share this
        # folder (e.g. E2iPlayer writes .stream), and our own wget-based
        # recorder all write a whole family of sidecar files alongside
        # each real recording — .meta, .cuts, .ap, .sc, .eit, plus our
        # own .json metadata sidecar. None of those are recordings in
        # their own right, so only list files whose extension is an
        # actual media container; everything else is a sidecar and
        # stays hidden.
        MEDIA_EXTS = (".ts", ".stream", ".mp4", ".mkv", ".avi", ".mpg",
                      ".mpeg", ".m2ts", ".mov", ".wmv")
        try:
            names = sorted(
                (n for n in os.listdir(path) if not n.startswith(".")),
                key=lambda n: n.lower())
            dirs  = [n for n in names if os.path.isdir(os.path.join(path, n))]
            files = [n for n in names
                     if not os.path.isdir(os.path.join(path, n))
                     and os.path.splitext(n)[1].lower() in MEDIA_EXTS]
            for d in dirs:
                full = os.path.join(path, d)
                # Count what the folder will show: recordings and
                # subfolders - not the side files (.meta, .ap, .jpg, ...)
                try:
                    count = sum(
                        1 for n in os.listdir(full)
                        if not n.startswith(".") and (
                            os.path.splitext(n)[1].lower() in MEDIA_EXTS
                            or os.path.isdir(os.path.join(full, n))))
                except Exception:
                    count = 0
                entries.append({
                    "is_dir": True, "path": full,
                    "text": "%s/  (%d item%s)" % (d, count, "" if count == 1 else "s"),
                })
            for f in files:
                full = os.path.join(path, f)
                description = self._read_native_meta_description(full)
                meta = self._read_recording_meta(full)
                if meta:
                    start = meta.get("start")
                    entries.append({
                        "is_dir": False, "path": full,
                        "title": meta.get("title") or f,
                        "start": start if start is not None else self._file_fallback_date(full),
                        "end": meta.get("end"),
                        "channel_name": meta.get("channel_name"),
                        "stream_id": meta.get("stream_id"),
                        "description": description,
                    })
                else:
                    title, start, channel_name = self._parse_recording_filename(f)
                    if start is None:
                        start = self._file_fallback_date(full)
                    entries.append({
                        "is_dir": False, "path": full,
                        "title": title, "start": start, "end": None,
                        "channel_name": channel_name, "stream_id": None,
                        "description": description,
                    })
        except Exception:
            pass
        if not entries:
            entries.append({"is_dir": False, "path": None, "text": "(no recordings found)"})

        # Recording locations live directly in the Recordings browser.
        # Every row has the fields expected by _recordings_render().
        location_rows = [{
            "is_dir": True,
            "is_location": True,
            "path": path,
            "text": "Current: " + path,
        }]

        for bookmark in self._recording_bookmarks():
            if isinstance(bookmark, dict) and bookmark.get("path"):
                bookmark_path = bookmark["path"]

                if bookmark_path.rstrip("/") != path.rstrip("/"):
                    location_rows.append({
                        "is_dir": True,
                        "is_location": True,
                        "is_bookmark": True,
                        "path": bookmark_path,
                        "text": bookmark.get("name") or bookmark_path,
                    })

        location_rows.append({
            "is_dir": True,
            "is_add_bookmark": True,
            "path": None,
            "text": "+ Add Recording Bookmark",
        })

        # "Scheduled recordings (n)" first — opens the list of what's set
        # to record (recording/schedule_list.py).
        try:
            from .recording.schedule_list import scheduled_items
            n_sched = len(scheduled_items(state))
        except Exception:
            n_sched = 0
        sched_row = [{
            "is_dir": True,
            "is_sched_link": True,
            "path": "__sched__",
            "text": "Scheduled recordings (%d)" % n_sched,
        }]
        # "Deleted recordings (n)" - the bin, so they can be restored.
        try:
            _t, _names = self._trash_items()
        except Exception:
            _names = []
        sched_row.append({
            "is_dir": True,
            "is_trash_link": True,
            "path": "__trash__",
            "text": "Deleted recordings (%d)" % len(_names),
        })

        entries = sched_row + location_rows + entries
        self._rec_mode    = "files"
        self._rec_path    = path
        self._rec_entries = entries
        self._rec_sel     = 0
        self._rec_scroll  = 0

    def _recordings_render(self):
        """Windowed (re)build of the visible list — called on every
        up/down keypress as well as when the folder being browsed
        changes. Only the currently-visible slice of self._rec_entries
        is ever handed to setList(); moveToIndex() then just repositions
        the highlight within that small rendered set. This mirrors the
        only proven-working scrolling pattern in this codebase (see
        screen1.py's _b3_refresh / self["b3_list"].moveToIndex) — a
        single setList() of the full unwindowed list plus a bare
        moveSelectionTo(idx) call does NOT scroll a large list."""
        W = getattr(self, '_rec_content_w', 1920 - B1_COLLAPSED_W - 40)
        H = self.REC_ITEM_H
        BG, SELBG = 0x0a4aa8, 0xd0e8ff  # whiteish highlight — same tile
        # colour as every other list/menu highlight in this plugin (see
        # b6_highlight/b7_highlight/etc.), not the darker blue this used
        # before. Text switches to black on that light background,
        # matching the same convention (_b6_refresh and friends).
        RIGHT_W = 480   # width reserved for "date  start-end (mins)" on line 2
        entries   = self._rec_entries
        vis_start = self._rec_scroll
        vis_end   = min(len(entries), vis_start + self.REC_VISIBLE)
        rows = []
        # Only show the selection highlight while Recordings actually has
        # key-input focus. Backing out to the rail leaves self._rec_sel
        # untouched (so your place is kept), but the highlight itself
        # should disappear until focus comes back — see _on_exit /
        # _on_left_key / _on_right_key, which each re-render after
        # moving focus in or out of "recordings".
        focused = self._focus == "recordings"
        for abs_idx in range(vis_start, vis_end):
            e = entries[abs_idx]
            is_focused = focused and abs_idx == self._rec_sel
            row_bg     = SELBG if is_focused else BG
            fg_main    = 0x000000 if is_focused else 0xffffff
            fg_dim     = 0x000000 if is_focused else 0xdddddd
            fg_accent  = 0x000000 if is_focused else 0xaaddff
            row = [None]
            row.append(MultiContentEntryText(
                pos=(0, 0), size=(W, H),
                font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                text="", color=fg_main, color_sel=fg_main,
                backcolor=row_bg, backcolor_sel=row_bg))
            if e["is_dir"] or e.get("path") is None:
                row.append(MultiContentEntryText(
                    pos=(20, 0), size=(W - 40, H),
                    font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text=e.get("text", ""),
                    color=fg_main, color_sel=fg_main,
                    backcolor=row_bg, backcolor_sel=row_bg))
                rows.append(row)
                continue
            # Line 1: title only (extension already stripped) — no date
            # prefix any more, so it gets the full width and a bigger font.
            fg_title = (e.get("title_colour") if (e.get("title_colour")
                        and not is_focused) else fg_main)
            row.append(MultiContentEntryText(
                pos=(20, 2), size=(W - 40, 36),
                font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                text=e.get("title") or "",
                color=fg_title, color_sel=fg_title,
                backcolor=row_bg, backcolor_sel=row_bg))
            # Line 2, left: channel name (no icon any more).
            left_w = (W - 40) - RIGHT_W - 20
            row.append(MultiContentEntryText(
                pos=(20, 42), size=(left_w, 28),
                font=1, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                text=e.get("channel_name") or "Unknown channel",
                color=fg_dim, color_sel=fg_dim,
                backcolor=row_bg, backcolor_sel=row_bg))
            # Line 2, right: date recorded + start-end + duration, all
            # moved down onto the channel-name line and right-aligned to
            # the end of the row — only shown when known (legacy
            # recordings without an end time just get the date).
            date_str = (time.strftime("%d %b %Y", time.localtime(e["start"]))
                        if e.get("start") else "Unknown date")
            if e.get("right_text"):
                right_text = e["right_text"]
            elif e.get("start") and e.get("end"):
                s    = time.strftime("%H:%M", time.localtime(e["start"]))
                en   = time.strftime("%H:%M", time.localtime(e["end"]))
                mins = max(0, int((e["end"] - e["start"]) / 60))
                right_text = "%s   %s - %s  (%d min)" % (date_str, s, en, mins)
            else:
                right_text = date_str
            row.append(MultiContentEntryText(
                pos=(20 + left_w + 20, 42), size=(RIGHT_W, 28),
                font=1, flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER,
                text=right_text,
                color=fg_accent, color_sel=fg_accent,
                backcolor=row_bg, backcolor_sel=row_bg))
            rows.append(row)
        self["rec_list"].setList(rows)
        focused_in_visible = self._rec_sel - self._rec_scroll
        if 0 <= focused_in_visible < len(rows):
            self["rec_list"].moveToIndex(focused_in_visible)
        self._recordings_update_info()

    def _recordings_update_info(self):
        """The reserved row at the top of the content area always shows
        the programme name (and description, if we have one) for
        whichever entry is currently focused — not tied to scroll
        position, so it stays visible no matter where in the list you
        are."""
        sel = None
        if 0 <= self._rec_sel < len(self._rec_entries):
            sel = self._rec_entries[self._rec_sel]
        if sel and not sel.get("is_dir") and sel.get("path") is not None:
            self["rec_info_title"].setText(sel.get("title") or "")
            self["rec_info_desc"].setText(sel.get("description") or "")
        else:
            self["rec_info_title"].setText(sel.get("text", "") if sel else "")
            self["rec_info_desc"].setText("")

    def _recordings_ok(self):
        if not self._rec_entries:
            return
        entry = self._rec_entries[self._rec_sel]

        if entry.get("is_sched_link"):
            self._sched_show()
            return
        if entry.get("is_sched_back"):
            self._recordings_load(self._rec_path)
            self._recordings_render()
            return
        if entry.get("sched"):
            self._sched_ok(entry)
            return
        if entry.get("is_trash_link"):
            self._trash_show()
            return
        if entry.get("trash"):
            self._trash_ok(entry)
            return
        if entry.get("is_add_bookmark"):
            self._recording_add_bookmark()
            return
        if entry.get("is_bookmark"):
            # A saved recording location: open it, or remove the bookmark
            # (the folder and its recordings are left alone).
            self._rec_bm_entry = entry
            from .side_menu import SideMenu
            self.session.openWithCallback(
                self._recording_bookmark_choice,
                SideMenu,
                entry.get("text") or entry["path"],
                [("Open", "open"), ("Remove bookmark", "remove")],
                "Removing the bookmark doesn't touch the folder or its recordings",
            )
            return
        if entry.get("path") is None:
            return  # the "(no recordings found)" placeholder — nothing to do
        if entry.get("is_dir"):
            self._recordings_load(entry["path"])
            self._recordings_render()
            return
        self._rec_menu_entry = entry
        # Shared right-hand panel (side_menu.py), as the grid's OK menu.
        from .side_menu import SideMenu
        detail = entry.get("channel_name") or ""
        if entry.get("start"):
            when = time.strftime("%d %b  %H:%M", time.localtime(entry["start"]))
            detail = ("%s  \u00b7  %s" % (detail, when)) if detail else when
        self.session.openWithCallback(
            self._recordings_menu_choice,
            SideMenu,
            entry.get("title") or os.path.basename(entry["path"]),
            [("Play", "play"), ("Delete", "delete")],
            detail,
        )

    # ------------------------------------------------------------------
    # Scheduled recordings (recording/schedule_list.py)
    # ------------------------------------------------------------------

    def _sched_show(self, keep_sel=False):
        """Show what's set to record in the Recordings list area:
        recording now (red) first, then soonest first."""
        from .recording.schedule_list import scheduled_items, days_text
        try:
            items = scheduled_items(state)
        except Exception as e:
            print("[E2iPTV] scheduled recordings:", e)
            items = []
        entries = [{"is_dir": True, "is_sched_back": True, "path": "__sched__",
                    "text": "<  Back to recordings"}]
        for it in items:
            chan = it.get("channel") or "Unknown channel"
            if it.get("provider"):
                chan = "%s   \u00b7   %s" % (chan, it["provider"])
            s, e = it.get("start") or 0, it.get("end") or 0
            times = "%s - %s" % (time.strftime("%H:%M", time.localtime(s)),
                                 time.strftime("%H:%M", time.localtime(e)))
            if it["kind"] == "repeat":
                right = "%s   %s   (next %s)" % (
                    days_text(it.get("days")), times,
                    time.strftime("%a %d", time.localtime(s)))
            else:
                right = "%s   %s" % (time.strftime("%a %d %b", time.localtime(s)), times)
            title = it.get("title") or "Recording"
            if it.get("status") == "active":
                title = "REC  " + title
                desc = "Recording now"
            elif it["kind"] == "repeat":
                desc = "Repeat recording: %s, %s" % (days_text(it.get("days")), times)
            else:
                desc = "Scheduled"
            entries.append({
                "is_dir": False, "path": "__sched__", "sched": it,
                "title": title, "channel_name": chan,
                "start": s, "end": e, "right_text": right,
                "description": desc,
                "title_colour": 0xff6666 if it.get("status") == "active" else None,
            })
        if len(entries) == 1:
            entries.append({"is_dir": False, "path": None,
                            "text": "(nothing scheduled)"})
        sel = self._rec_sel if keep_sel else 1
        self._rec_mode = "sched"
        self._rec_entries = entries
        self._rec_sel = max(0, min(sel, len(entries) - 1))
        self._rec_scroll = max(0, self._rec_sel - self.REC_VISIBLE + 1)
        self._recordings_render()

    def _sched_ok(self, entry):
        it = entry["sched"]
        if it.get("status") == "active":
            opts = [("Stop recording now", "stop_now")]
            if it["kind"] == "repeat":
                opts.append(("Stop repeat recording", "stop_repeat"))
        elif it["kind"] == "repeat":
            opts = [("Stop repeat recording", "stop_repeat")]
        else:
            opts = [("Cancel this recording", "cancel")]
        opts.append(("Back", "back"))
        # Shared right-hand panel (side_menu.py) - shows every option.
        from .side_menu import SideMenu
        detail = entry.get("channel_name") or ""
        if entry.get("right_text"):
            detail = "%s  \u00b7  %s" % (detail, entry["right_text"]) if detail else entry["right_text"]
        self._sched_menu_item = it
        self.session.openWithCallback(
            self._sched_choice, SideMenu,
            (entry.get("title") or "Recording").replace("REC  ", ""),
            opts, detail)

    def _sched_choice(self, result):
        it = getattr(self, "_sched_menu_item", None)
        self._sched_menu_item = None
        if not result or not it or result[1] == "back":
            return
        try:
            from .recording.schedule_list import cancel
            cancel(state, it, result[1])
        except Exception as e:
            print("[E2iPTV] scheduled recordings:", e)
        self._sched_show(keep_sel=True)

    def _recording_bookmark_choice(self, result):
        entry = getattr(self, "_rec_bm_entry", None)
        self._rec_bm_entry = None
        if not result or not entry:
            return
        action = result[1] if isinstance(result, (tuple, list)) else result
        if action == "open":
            self._recordings_load(entry["path"])
            self._recordings_render()
        elif action == "remove":
            want = entry["path"].rstrip("/")
            keep = [b for b in self._recording_bookmarks()
                    if not (isinstance(b, dict)
                            and str(b.get("path") or "").rstrip("/") == want)]
            self._save_recording_bookmarks(keep)
            self._recordings_load(self._rec_path)
            self._rec_sel = min(self._rec_sel, max(0, len(self._rec_entries) - 1))
            self._recordings_render()

    def _recordings_menu_choice(self, result):
        entry = getattr(self, "_rec_menu_entry", None)
        self._rec_menu_entry = None
        if not result or not entry:
            return
        _label, action = result
        if action == "play":
            self._recordings_play(entry)
        elif action == "delete":
            self._recordings_confirm_delete(entry)

    def _recordings_play(self, entry):
        """Play via our own RecordingPlayer screen (player.py), opened
        directly on top of this one — confirmed safe here (no freeze on
        opening); the freeze seen when handing off to the box's InfoBar
        was specific to the ServiceApp service types (5001/5002)
        conflicting with Screen1's live "Pig" preview widget, not to
        opening a player screen directly like this.

        RecordingPlayer opens the file as service type 1 (the same DVB
        pipeline live TV and OpenATV's own native Movie Player use) and
        seeks with plain native seek.seekRelative() — no restart/veil
        workaround needed any more; that was only ever required to work
        around type 4097 (ServiceMP3/gstreamer)'s weak seeking.

        The channel name, start/end and description are passed through
        purely so RecordingPlayer's OK-button info panel can show them
        — none of it affects playback."""
        path = entry.get("path")
        if not path or not os.path.exists(path):
            return
        try:
            from .player import RecordingPlayer
            self.session.open(
                RecordingPlayer, path,
                entry.get("title") or os.path.basename(path),
                channel_name=entry.get("channel_name"),
                start=entry.get("start"),
                end=entry.get("end"),
                description=entry.get("description"),
            )
        except Exception as e:
            try:
                open(LOG("e2iptv_play.log"), "a").write("PLAY ERR: %r\n" % (e,))
            except Exception:
                pass
            self.session.open(
                E2iMessageBox,
                "Unable to play this recording.",
                MessageBox.TYPE_ERROR,
                timeout=5,
            )

    @staticmethod
    def _recording_running_key(path):
        """The active-recordings key of the recording still being written
        to this file, or None."""
        try:
            want = os.path.abspath(path)
            for key, rec in (state.active_recordings or {}).items():
                if (isinstance(rec, dict) and rec.get("status") == "active"
                        and rec.get("filename")
                        and os.path.abspath(rec["filename"]) == want):
                    return key
        except Exception:
            pass
        return None

    def _recordings_confirm_delete(self, entry):
        self._rec_delete_entry = entry
        key = self._recording_running_key(entry.get("path") or "")
        if key:
            # Still recording: deleting only moved the file to the bin,
            # where it carried on growing. Stop it first.
            self._rec_delete_key = key
            self.session.openWithCallback(
                self._recordings_stop_then_delete,
                E2iMessageBox,
                "This is still recording.\n\nStop the recording and delete it?\n\n%s"
                % (entry.get("title") or os.path.basename(entry["path"])),
                MessageBox.TYPE_YESNO,
                default=False,
            )
            return
        self.session.openWithCallback(
            self._recordings_delete_confirmed,
            E2iMessageBox,
            "Delete this recording?\n\n%s" % (entry.get("title") or os.path.basename(entry["path"])),
            MessageBox.TYPE_YESNO,
            default=False,
        )

    def _recordings_stop_then_delete(self, confirmed):
        key = getattr(self, "_rec_delete_key", None)
        self._rec_delete_key = None
        if not confirmed or not key:
            self._rec_delete_entry = None
            return
        try:
            from .player import stop_recording
            stop_recording(key)
        except Exception as e:
            print("[E2iPTV] stop before delete:", e)
        # give the last of the stream a moment to reach the file, then
        # move it to the bin as usual
        from twisted.internet import reactor
        reactor.callLater(1.5, self._recordings_delete_confirmed, True)

    def _recordings_delete_confirmed(self, confirmed):
        entry = getattr(self, "_rec_delete_entry", None)
        self._rec_delete_entry = None
        if not confirmed or not entry:
            return
        path = entry.get("path")
        if not path:
            return
        # Delete every file in the same folder that shares this
        # recording's base filename — the media file itself, plus every
        # sidecar any recorder (this plugin, the box's native PVR,
        # E2iPlayer, etc.) wrote alongside it, whatever its naming
        # convention. This replaced a fixed suffix list that assumed
        # every sidecar was named "<full filename>.<suffix>" — true for
        # .json/.meta/.cuts/.ap/.sc, but Enigma2's own .eit sidecar
        # actually *replaces* the media extension (e.g. "recording.eit",
        # not "recording.ts.eit"), so it was never being matched and was
        # left behind every time.
        import glob
        directory, filename = os.path.split(path)
        base = os.path.splitext(filename)[0]
        # Delete = move to the box's recycle bin (.Trash), the same one
        # the box's own recording list uses; the image empties it after
        # the number of days set in its recording settings. Only if no
        # bin can be used is the recording removed outright.
        trash = self._trash_folder(directory)
        files = []
        try:
            files = glob.glob(os.path.join(glob.escape(directory), glob.escape(base) + ".*"))
        except Exception:
            pass
        if os.path.exists(path) and path not in files:
            files.append(path)
        new_base = self._free_base(trash, base, files) if trash else None
        for p in files:
            try:
                if trash and new_base:
                    name = os.path.basename(p)
                    dest = os.path.join(trash, new_base + name[len(base):])
                    try:
                        os.rename(p, dest)
                        self._trash_note(trash, os.path.basename(dest), p)
                        continue
                    except OSError:
                        pass        # e.g. another disk - fall through
                os.remove(p)
            except Exception:
                pass
        # Also tidy sidecars left from the recording's planned start time
        # (or from scheduled recordings that never ran) — see
        # player.remove_orphan_sidecars.
        self._tidy_recording_folder(directory, min_age=0)
        self._recordings_load(self._rec_path)
        self._recordings_render()

    def _trash_folder(self, directory):
        """The recycle bin (.Trash) for recordings in `directory`, created
        if needed: the image's own (OpenATV: <disk>/movie/.Trash), or a
        .Trash folder next to the recording. None if neither can be used."""
        try:
            from Tools.Trashcan import createTrashFolder
            t = createTrashFolder(directory)
            if t and os.path.isdir(t):
                return t
        except Exception:
            pass
        t = os.path.join(directory, ".Trash")
        try:
            if not os.path.isdir(t):
                os.makedirs(t)
            return t
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Deleted recordings (.Trash) - list, restore, delete permanently
    # ------------------------------------------------------------------

    _TRASH_MAP = ".e2iptv_restore.json"     # trash file name -> where it came from
    _MEDIA_EXTS = (".ts", ".stream", ".mp4", ".mkv", ".avi", ".mpg",
                   ".mpeg", ".m2ts", ".mov", ".wmv")

    def _trash_map(self, trash):
        import json
        try:
            with open(os.path.join(trash, self._TRASH_MAP)) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _trash_save_map(self, trash, data):
        import json
        try:
            with open(os.path.join(trash, self._TRASH_MAP), "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _trash_note(self, trash, name_in_trash, original_path):
        data = self._trash_map(trash)
        data[name_in_trash] = original_path
        self._trash_save_map(trash, data)

    def _trash_items(self):
        """(trash folder, [media file names in it]) for the recordings
        root, without creating the bin if there isn't one yet."""
        root = self._rec_root or self._rec_path
        try:
            from Tools.Trashcan import getTrashcan
            trash = getTrashcan(root)
        except Exception:
            trash = os.path.join(root, ".Trash")
        if not trash or not os.path.isdir(trash):
            fallback = os.path.join(root, ".Trash")
            trash = fallback if os.path.isdir(fallback) else None
        if not trash:
            return None, []
        try:
            names = sorted(n for n in os.listdir(trash)
                           if not n.startswith(".")
                           and os.path.splitext(n)[1].lower() in self._MEDIA_EXTS)
        except Exception:
            names = []
        return trash, names

    def _trash_show(self, keep_sel=False):
        trash, names = self._trash_items()
        entries = [{"is_dir": True, "is_sched_back": True, "path": "__trash__",
                    "text": "<  Back to recordings"}]
        rows = []
        for n in names:
            full = os.path.join(trash, n)
            meta = self._read_recording_meta(full) or {}
            if meta:
                title, start, chan = meta.get("title") or n, meta.get("start"), meta.get("channel_name")
            else:
                title, start, chan = self._parse_recording_filename(n)
            try:
                deleted = os.stat(full).st_ctime
            except Exception:
                deleted = 0
            rows.append((deleted, {
                "is_dir": False, "path": full, "trash": True,
                "title": title, "channel_name": chan or "Unknown channel",
                "start": start, "end": meta.get("end"),
                "right_text": "deleted %s" % time.strftime("%d %b %H:%M", time.localtime(deleted)),
                "description": "In the bin - OK to restore or delete permanently",
            }))
        rows.sort(key=lambda r: -r[0])          # most recently deleted first
        entries += [r[1] for r in rows]
        if len(entries) == 1:
            entries.append({"is_dir": False, "path": None, "text": "(nothing deleted)"})
        sel = self._rec_sel if keep_sel else 1
        self._rec_mode = "trash"
        self._rec_trash = trash
        self._rec_entries = entries
        self._rec_sel = max(0, min(sel, len(entries) - 1))
        self._rec_scroll = max(0, self._rec_sel - self.REC_VISIBLE + 1)
        self._recordings_render()

    def _free_base(self, folder, base, files):
        """`base`, or base_1, base_2 ... so that none of the group's files
        (named base + rest) already exists in `folder`. The whole group
        gets the same new name, so side files always match the recording."""
        rests = [os.path.basename(f)[len(base):] for f in files
                 if os.path.basename(f).startswith(base)]
        cand, n = base, 0
        while any(os.path.exists(os.path.join(folder, cand + r)) for r in rests):
            n += 1
            cand = "%s_%d" % (base, n)
        return cand

    def _trash_files_of(self, media_path):
        """The recording and all its side files in the bin (they share its
        base name - a clash in the bin renames the whole group alike)."""
        import glob
        d, n = os.path.split(media_path)
        base = os.path.splitext(n)[0]
        files = glob.glob(os.path.join(glob.escape(d), glob.escape(base) + ".*"))
        if media_path not in files and os.path.exists(media_path):
            files.append(media_path)
        return files

    def _trash_ok(self, entry):
        from .side_menu import SideMenu
        detail = entry.get("channel_name") or ""
        self._trash_menu_entry = entry
        self.session.openWithCallback(
            self._trash_choice, SideMenu, entry.get("title") or "Recording",
            [("Restore", "restore"), ("Delete permanently", "purge")], detail)

    def _trash_choice(self, result):
        entry = getattr(self, "_trash_menu_entry", None)
        self._trash_menu_entry = None
        if not result or not entry:
            return
        if result[1] == "restore":
            self._trash_restore(entry["path"])
            self._trash_show(keep_sel=True)
        elif result[1] == "purge":
            self._trash_purge_entry = entry
            self.session.openWithCallback(
                self._trash_purge_confirmed, E2iMessageBox,
                "Delete permanently? It can't be got back afterwards.\n\n%s"
                % (entry.get("title") or os.path.basename(entry["path"])),
                MessageBox.TYPE_YESNO, default=False)

    def _trash_restore(self, media_path):
        """Move the recording and every side file back to where they
        were deleted from (or the recordings folder if not known)."""
        trash, media_name = os.path.split(media_path)
        data = self._trash_map(trash)
        root = self._rec_root or self._rec_path
        group = self._trash_files_of(media_path)
        trash_base = os.path.splitext(media_name)[0]
        orig_media = data.get(media_name) or os.path.join(root, media_name)
        dest_dir = os.path.dirname(orig_media)
        if not os.path.isdir(dest_dir):
            dest_dir = root
        orig_base = os.path.splitext(os.path.basename(orig_media))[0]
        rests = [os.path.basename(p)[len(trash_base):] for p in group]
        base = orig_base
        if any(os.path.exists(os.path.join(dest_dir, base + r)) for r in rests):
            base = self._free_base(dest_dir, orig_base + "_restored",
                                   [orig_base + "_restored" + r for r in rests])
        for p, r in zip(group, rests):
            try:
                os.rename(p, os.path.join(dest_dir, base + r))
                data.pop(os.path.basename(p), None)
            except Exception as e:
                print("[E2iPTV] restore %s: %s" % (os.path.basename(p), e))
        self._trash_save_map(trash, data)

    def _trash_purge_confirmed(self, confirmed):
        entry = getattr(self, "_trash_purge_entry", None)
        self._trash_purge_entry = None
        if not confirmed or not entry:
            return
        trash = os.path.dirname(entry["path"])
        data = self._trash_map(trash)
        for p in self._trash_files_of(entry["path"]):
            try:
                os.remove(p)
                data.pop(os.path.basename(p), None)
            except Exception:
                pass
        self._trash_save_map(trash, data)
        self._trash_show(keep_sel=True)

    def _tidy_recording_folder(self, folder, min_age=600):
        """Remove orphaned recording sidecars in `folder`, in the
        background (it can be a big folder on a sleeping disk)."""
        from twisted.internet import reactor

        def worker():
            try:
                from .player import remove_orphan_sidecars
                remove_orphan_sidecars(folder, min_age=min_age)
            except Exception:
                pass

        reactor.callInThread(worker)

    # ------------------------------------------------------------------
    # EPG settings panel
    # ------------------------------------------------------------------

    def _epg_load_hour(self):
        import json

        hour = 20

        try:
            with open(
                DATA + "/cache/app_settings.json",
                "r"
            ) as f:
                data = json.load(f)

            hour = int(data.get("epg_fetch_hour", 20))

        except Exception:
            hour = 20

        self._epg_fetch_hour = max(0, min(23, hour))

    def _epg_save_hour(self):
        import json
        import os

        path = DATA + "/cache/app_settings.json"

        try:
            data = {}

            try:
                with open(path, "r") as f:
                    data = json.load(f)
            except Exception:
                pass

            data["epg_fetch_hour"] = int(self._epg_fetch_hour)

            tmp = path + ".tmp"

            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp, path)

            # Immediately apply the new time to the live scheduler.
            try:
                from . import plugin as _plugin

                scheduler = getattr(
                    _plugin,
                    "_epg_scheduler",
                    None
                )

                if scheduler is not None:
                    scheduler._schedule()

            except Exception as ex:
                self._debug(
                    "EPG scheduler reschedule failed: %s"
                    % ex
                )

        except Exception as ex:
            self._debug(
                "EPG settings save failed: %s"
                % ex
            )

    def _epg_change_hour(self, direction):
        self._epg_fetch_hour = (
            self._epg_fetch_hour + direction
        ) % 24

        self._epg_save_hour()
        self._epg_refresh()

    def _show_epg(self):
        self._hide_panel("b6", 8)
        self._show_overlay()

        x = self.OV_X + 20

        self._epg_load_hour()

        try:
            self["epg_header"].setText("EPG")
            self["epg_header"].instance.move(
                ePoint(x, 20)
            )
        except Exception:
            pass

        self._epg_sel = 0
        self._epg_status_cache = None
        self._picon_status_cache = None
        self._epg_refresh()
        self._focus = "epg"
        self._epg_status_bg()

    def _epg_status_bg(self):
        """Work out the EPG status and picon count off the screen's thread,
        then redraw the panel once with the results."""
        from twisted.internet import reactor

        def worker():
            try:
                status = self._epg_status_text()
            except Exception:
                status = "NO RESULT"
            try:
                picons = self._picon_status_text()
            except Exception:
                picons = "unknown"
            reactor.callFromThread(self._epg_status_ready, status, picons)

        reactor.callInThread(worker)

    def _epg_status_ready(self, status, picons):
        self._epg_status_cache = status
        self._picon_status_cache = picons
        if getattr(self, "_focus", None) == "epg":
            self._epg_refresh()

    def _hide_epg(self):
        try:
            self["epg_header"].instance.move(
                ePoint(3500, 0)
            )

            self["epg_highlight"].instance.move(
                ePoint(3500, 0)
            )

            for i in range(8):
                self["epg_item%d" % i].instance.move(
                    ePoint(3500, 0)
                )

        except Exception:
            pass

        self._hide_overlay()

    def _epg_status_text(self):
        import os

        log_path = DATA + "/epg/nightly_refresh.log"

        # Check whether the actual full controller is running.
        running = False

        try:
            import subprocess

            result = subprocess.call(
                "ps | grep '[b]ulk_epg_fetch.py' "
                "> /dev/null 2>&1",
                shell=True
            )

            running = result == 0

        except Exception:
            running = False

        if running:
            return "RUNNING"

        if not os.path.exists(log_path):
            return "NOT RUN"

        try:
            # Only the end of the log matters (the latest job) — it can
            # grow to many MB, so don't read all of it.
            with open(log_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 2 * 1024 * 1024))
                lines = f.read().decode("utf-8", "replace").splitlines()

            # Find the beginning of the most recent EPG job.
            start = -1

            for i in range(len(lines) - 1, -1, -1):
                line = lines[i].strip()

                if (
                    line == "===== EPG NIGHTLY JOB START ====="
                    or line == "===== EPG SCHEDULED REFRESH ====="
                    or line == "===== EPG MANUAL REFRESH ====="
                    or line.startswith("===== EPG MANUAL REFRESH (provider ")
                ):
                    start = i
                    break

            if start >= 0:

                # Only inspect the most recent job.
                for line in lines[start:]:
                    line = line.strip()

                    if line.startswith("Status:"):
                        return line[7:].strip()

                for line in reversed(lines[start:]):
                    line = line.strip()

                    if line.startswith("===== EPG NIGHTLY JOB END"):
                        return "COMPLETED"

                return "INCOMPLETE"

        except Exception:
            pass

        return "NO RESULT"

    def _epg_refresh(self):
        import os

        x = self.OV_X + 20

        # Status and picon count are worked out in the background when the
        # panel opens (see _epg_status_bg). Working them out here, on every
        # Up/Down, meant starting a "ps" process, reading the whole nightly
        # log and counting every picon file on each key press — the lag.
        status = getattr(self, "_epg_status_cache", None) or "checking..."
        picon_status = getattr(self, "_picon_status_cache", None) or "counting..."

        labels = [
            "Refresh Time:  %02d:00"
            % self._epg_fetch_hour,
            "Refresh Now",
            "Status:  %s"
            % status,
            "Clear Picon Cache",
            "Picons:  %s"
            % picon_status
        ] + self._fill_labels()

        for i in range(len(labels)):

            y = self.ITEM_Y0 + i * self.ITEM_H

            try:
                self["epg_item%d" % i].setText(
                    "  " + labels[i]
                )

                col = (
                    gRGB(0x000000)
                    if i == self._epg_sel
                    else gRGB(0xffffff)
                )

                self["epg_item%d" % i].instance.setForegroundColor(
                    col
                )

                self["epg_item%d" % i].instance.move(
                    ePoint(x, y)
                )

            except Exception:
                pass

        try:
            hy = (
                self.ITEM_Y0
                + self._epg_sel * self.ITEM_H
            )

            self["epg_highlight"].instance.move(
                ePoint(x, hy)
            )

        except Exception:
            pass

    # ------------------------------------------------------------------
    # Guide gap filling (epg_fill.py)
    # ------------------------------------------------------------------

    _FILL_UK = "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz"
    _FILL_IE = "https://epgshare01.online/epgshare01/epg_ripper_IE1.xml.gz"
    _FILL_US = "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"

    def _fill_settings(self):
        import json
        try:
            with open(DATA + "/cache/app_settings.json") as f:
                d = json.load(f)
        except Exception:
            d = {}
        src = d.get("epg_fill_sources")
        return bool(d.get("epg_fill", True)), (src if isinstance(src, list) else [])

    def _fill_save(self, **kw):
        import json
        path = DATA + "/cache/app_settings.json"
        try:
            try:
                with open(path) as f:
                    d = json.load(f)
            except Exception:
                d = {}
            d.update(kw)
            with open(path, "w") as f:
                json.dump(d, f)
        except Exception as e:
            print("[E2iPTV] fill setting:", e)

    def _fill_source_text(self, src):
        """Short form for the Settings row (the menu behind it says it in
        full): your other providers always, plus a guide file if chosen."""
        import os
        if not src:
            return "Providers only"
        if src == [self._FILL_UK]:
            return "Providers + UK guide"
        if src == [self._FILL_UK, self._FILL_IE]:
            return "Providers + UK, IE guides"
        if src == [self._FILL_UK, self._FILL_US]:
            return "Providers + UK, US guides"
        if src == [self._FILL_UK, self._FILL_IE, self._FILL_US]:
            return "Providers + UK, IE, US"
        if len(src) == 1 and not src[0].startswith("http"):
            return "Providers + file on box"
        return "Providers + %d guides" % len(src)

    def _fill_running(self):
        p = getattr(self, "_fill_proc", None)
        if p is not None and p.poll() is None:
            return True
        # started elsewhere (overnight, or an earlier visit to Settings)
        try:
            from .epg_fill import is_running
            return is_running()
        except Exception:
            return False

    def _fill_labels(self):
        on, src = self._fill_settings()
        return [
            "Fill Guide Gaps:  < %s >" % ("On" if on else "Off"),
            "Fill Gaps From:  %s" % self._fill_source_text(src),
            "Fill Gaps Now%s" % ("  (running...)" if self._fill_running() else ""),
        ]

    def _fill_toggle(self):
        on, _src = self._fill_settings()
        self._fill_save(epg_fill=not on)
        self._epg_refresh()

    def _fill_choose_source(self):
        import glob
        from .side_menu import SideMenu
        opts = [("Other providers only", "none"),
                ("Other providers + epgshare01 UK  (downloaded daily)", "uk"),
                ("Other providers + epgshare01 UK + Ireland", "ukie"),
                ("Other providers + epgshare01 UK + US  (US file is large)", "ukus"),
                ("Other providers + epgshare01 UK + Ireland + US", "ukieus")]
        for pattern in ("/media/*/*.xml.gz", "/media/*/*.xml", "/media/*/epg/*.xml.gz"):
            for path in sorted(glob.glob(pattern))[:6]:
                opts.append(("Other providers + file:  %s" % path, "file:" + path))
        self.session.openWithCallback(
            self._fill_source_chosen, SideMenu, "Fill Gaps From", opts,
            detail="Your other providers are always tried first", value_only=True)

    def _fill_source_chosen(self, value):
        if not value:
            return
        src = {"uk": [self._FILL_UK], "ukie": [self._FILL_UK, self._FILL_IE],
               "ukus": [self._FILL_UK, self._FILL_US],
               "ukieus": [self._FILL_UK, self._FILL_IE, self._FILL_US],
               "none": []}.get(value)
        if src is None and str(value).startswith("file:"):
            src = [value[5:]]
        if src is not None:
            self._fill_save(epg_fill_sources=src)
        self._epg_refresh()

    def _fill_now(self):
        import subprocess
        if self._fill_running():
            return
        script = "/usr/lib/enigma2/python/Plugins/Extensions/E2iPTV/epg_fill.py"
        try:
            self._fill_proc = subprocess.Popen(
                ["nice", "-n", "15", "python3", script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print("[E2iPTV] fill now:", e)
            return
        self._epg_refresh()
        from twisted.internet import reactor
        reactor.callLater(5, self._fill_watch)

    def _fill_watch(self):
        from twisted.internet import reactor
        if self._fill_running():
            reactor.callLater(5, self._fill_watch)
            return
        if getattr(self, "_focus", None) == "epg":
            self._epg_refresh()
        try:
            import json
            with open(DATA + "/epg/fill/index.json") as f:
                n = len(json.load(f).get("keys") or [])
            from Screens.MessageBox import MessageBox
            self.session.open(MessageBox,
                              "Guide gaps filled: %d channels now have a stand-in guide.\n"
                              "Details: Settings -> Status & Logs -> Guide gap filling" % n,
                              MessageBox.TYPE_INFO, timeout=8)
        except Exception:
            pass

    def _epg_ok(self):
        if self._epg_sel == 0:
            # Refresh Time is changed with LEFT/RIGHT.
            return

        if self._epg_sel == 1:
            self._epg_manual_refresh()
            return

        if self._epg_sel == 3:
            self._picon_rescan()
            return

        if self._epg_sel == 5:
            self._fill_toggle()
            return
        if self._epg_sel == 6:
            self._fill_choose_source()
            return
        if self._epg_sel == 7:
            self._fill_now()
            return

        self._epg_refresh()

    def _epg_manual_refresh(self):
        import os
        import subprocess

        script = (
            "/usr/lib/enigma2/python/Plugins/Extensions/"
            "E2iPTV/bulk_epg_fetch.py"
        )

        log_path = DATA + "/epg/nightly_refresh.log"
        cap(log_path, 5 * 1024 * 1024)  # start afresh past 5 MB (keeps one .old)

        try:

            os.makedirs(
                DATA + "/epg",
                exist_ok=True
            )

            with open(
                log_path,
                "a"
            ) as log:

                log.write(
                    "\n===== EPG MANUAL REFRESH =====\n"
                )

                env = os.environ.copy()
                plugin_parent = (
                    "/usr/lib/enigma2/python/Plugins/Extensions"
                )

                env["PYTHONPATH"] = (
                    plugin_parent
                    + ":"
                    + env.get("PYTHONPATH", "")
                )

                process = subprocess.Popen(
                    [
                        "python3",
                        script,
                        "--playlists",
                        state.playlists_path
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=env
                )

                log.write(
                    "Started bulk EPG fetch PID %d\n"
                    % process.pid
                )

        except Exception as ex:

            try:

                with open(
                    log_path,
                    "a"
                ) as log:

                    log.write(
                        "FAILED TO START EPG FETCH: %s\n"
                        % ex
                    )

            except Exception:
                pass

        # Status changes to RUNNING once the job starts — check again.
        self._epg_status_cache = None
        self._epg_refresh()
        self._epg_status_bg()

    def _picon_status_text(self):
        """How many channel logos have been downloaded so far (see
        backend/picon.py — each provider's own logos, nothing from
        other plugins)."""
        try:
            from .backend.picon import cached_count
            return "%d downloaded" % cached_count()
        except Exception:
            return "unknown"

    def _picon_rescan(self):
        """Clear Picon Cache: delete every downloaded logo so they are
        fetched fresh from the providers as channels are next shown.
        Runs off the GUI thread since it can be thousands of files."""
        from twisted.internet import reactor

        def worker():
            try:
                from .backend.picon import clear_cache
                clear_cache()
            except Exception as ex:
                self._debug("Picon cache clear failed: %s" % ex)
            reactor.callFromThread(self._epg_status_bg)

        reactor.callInThread(worker)

    def _show_b7(self):
        self._hide_panel("b6", 8)
        self._show_overlay()
        x = self.OV_X + 20
        try:
            self["b7_header"].setText("Playlists")
            self["b7_header"].instance.move(ePoint(x, 20))
        except Exception:
            pass
        self._focus = "b7"
        self._b7_refresh()
        # Connect all enabled providers in background to populate status
        from twisted.internet import reactor
        for name, provider in state.providers:
            if getattr(provider, 'enabled', True) and not provider.server_timestamp:
                reactor.callInThread(self._connect_provider, provider)

    def _connect_provider(self, provider):
        from twisted.internet import reactor
        try:
            state.backend.provider_store.connect(provider)
        except Exception:
            pass
        reactor.callFromThread(self._b7_refresh)

    def _prefetch_cats(self, provider):
        try:
            state.backend.get_live_categories(provider)
        except Exception:
            pass

    def _hide_b7(self):
        try:
            self["b7_header"].instance.move(ePoint(3500, 0))
            self["b7_highlight"].instance.move(ePoint(3500, 0))
            for i in range(10):
                self["b7_name%d" % i].instance.move(ePoint(3500, 0))
                self["b7_status%d" % i].instance.move(ePoint(3500, 0))
                self["b7_info%d" % i].instance.move(ePoint(3500, 0))
        except Exception:
            pass

    def _debug(self, text):
        try:
            with open(LOG("e2iptv_screen2.log"), "a") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def _show_b8(self):
        self._debug("SHOW_B8 focus=%s" % self._focus)

        import traceback
        try:
            self._debug("".join(traceback.format_stack()[-6:]))
        except Exception:
            pass

        self._hide_b7()

        self._debug("SHOW_B8")

        self._b8_load_values()
        self._b8_refresh()
        self._focus = "b8"

        print("[E2iPTV] _show_b8()")
        print("[E2iPTV] focus =", self._focus)
        print("[E2iPTV] provider =", getattr(self._b8_provider, "name", None))
        print("[E2iPTV] items =", self._b8_items)

    def _hide_b8(self):
        self._hide_panel("b8", 8)

    # ------------------------------------------------------------------
    # Panel refresh methods
    # ------------------------------------------------------------------

    ITEM_H   = 100   # row height — more breathing room
    ITEM_Y0  = 100   # y of first item

    REC_ITEM_H = 73    # Recordings rows are 2 lines tall (title /
                        # channel name + date + times) — no icon column
    REC_VISIBLE = 877 // REC_ITEM_H   # rows that fit in the rec_list area (12) —
                        # one row's worth of height (877 = 950-73) is reserved
                        # above the list for the rec_info_title/desc gap

    def _hide_panel(self, panel, n_items):
        """Hide all widgets for a panel without touching the overlay."""
        try:
            self["%s_header"    % panel].instance.move(ePoint(3500, 0))
            self["%s_highlight" % panel].instance.move(ePoint(3500, 0))
            for i in range(n_items):
                self["%s_item%d" % (panel, i)].instance.move(ePoint(3500, 0))
        except Exception:
            pass

    def _b6_refresh(self):
        x = self.OV_X + 20
        for i, label in enumerate(self._b6_items):
            y = self.ITEM_Y0 + i * self.ITEM_H
            try:
                self["b6_item%d" % i].setText("  " + label)
                self["b6_item%d" % i].instance.move(ePoint(x, y))
                col = gRGB(0x000000) if i == self._b6_sel else gRGB(0xffffff)
                self["b6_item%d" % i].instance.setForegroundColor(col)
            except Exception:
                pass
        try:
            hy = self.ITEM_Y0 + self._b6_sel * self.ITEM_H
            self["b6_highlight"].instance.move(ePoint(x, hy))
        except Exception:
            pass

    def _b7_refresh(self):

        if self._focus != "b7":
            return

        self._debug("B7_REFRESH focus=%s" % self._focus)


        import time as _t
        x         = self.OV_X + 20
        providers = _real_providers(state.providers)
        vis_start = self._b7_scroll

        for i in range(self._b7_visible):
            idx    = vis_start + i
            y_name = self.ITEM_Y0 + i * self.ITEM_H
            y_info = y_name + 54
            is_sel = idx == self._b7_sel

            try:
                if idx < len(providers):
                    name, prov = providers[idx]
                    enabled    = getattr(prov, 'enabled', True)
                    off_label  = ("Off (#)" if getattr(prov, 'disabled_in_file', False)
                                  else "Disabled")
                    status     = getattr(prov, 'status', 'Unknown') if enabled else off_label
                    expires    = getattr(prov, 'expires', 0)
                    max_conn   = getattr(prov, 'max_connections', 0)
                    cur_conn   = getattr(prov, 'current_connections', 0)

                    # Format expiry
                    exp_str = ""
                    if expires and enabled:
                        try:
                            exp_str = "Expires: " + _t.strftime(
                                "%d/%m/%Y", _t.localtime(int(expires)))
                        except Exception:
                            pass

                    conn_str = ""
                    if max_conn and enabled:
                        conn_str = "Conn: %s/%s" % (cur_conn, max_conn)

                    info_str = "  ".join(filter(None, [exp_str, conn_str]))

                    # Name row (left) + Status (right)
                    self["b7_name%d" % i].setText("  %s" % name)
                    self["b7_name%d" % i].instance.move(ePoint(x, y_name))
                    if is_sel:
                        name_col = gRGB(0x000000)
                    elif not enabled:
                        name_col = gRGB(0x888888)
                    elif status.lower() in ("active",):
                        name_col = gRGB(0xffffff)
                    else:
                        name_col = gRGB(0xff4444)
                    self["b7_name%d" % i].instance.setForegroundColor(name_col)

                    # Status widget — right side of name row
                    status_label = status if enabled else off_label
                    self["b7_status%d" % i].setText(status_label)
                    self["b7_status%d" % i].instance.move(ePoint(x + 398, y_name))
                    if is_sel:
                        st_col = gRGB(0x000000)
                    elif not enabled:
                        st_col = gRGB(0x888888)
                    elif status.lower() == "active":
                        st_col = gRGB(0x44ff44)   # green
                    elif status.lower() == "unknown":
                        st_col = gRGB(0xaaaaaa)   # grey — not yet checked
                    else:
                        st_col = gRGB(0xff4444)   # red for error/expired
                    self["b7_status%d" % i].instance.setForegroundColor(st_col)

                    # Info row
                    self["b7_info%d" % i].setText("  " + info_str if info_str else "")
                    self["b7_info%d" % i].instance.move(ePoint(x, y_info))
                    info_col = gRGB(0x000000) if is_sel else gRGB(0x888888)
                    self["b7_info%d" % i].instance.setForegroundColor(info_col)
                else:
                    self["b7_name%d" % i].setText("")
                    self["b7_info%d" % i].setText("")
                    self["b7_name%d" % i].instance.move(ePoint(x, y_name))
                    self["b7_info%d" % i].instance.move(ePoint(x, y_info))
            except Exception:
                pass

        try:
            hy = self.ITEM_Y0 + (self._b7_sel - self._b7_scroll) * self.ITEM_H
            self["b7_highlight"].instance.move(ePoint(x, hy))
        except Exception:
            pass

    def _b8_load_values(self):
        p = self._b8_provider
        if not p:
            return
        intervals = [3600, 7200, 14400, 28800, 43200, 86400]
        cur       = getattr(p, 'refresh_interval', 86400)
        if cur not in intervals:
            cur = 86400
        self._b8_values = {
            "Refresh Interval": cur,
            "Enabled":          getattr(p, 'enabled', True),
            "EPG Offset":        getattr(p, 'epg_offset', 0),
            "Live TV":           getattr(p, 'show_live', True),
            "Movies":            getattr(p, 'show_movies', True),
            "Series":            getattr(p, 'show_series', True),
        }

    def _b8_interval_label(self, secs):
        hours = secs // 3600
        return "%dh" % hours if hours else "%dm" % (secs // 60)

    def _b8_refresh(self):
        self._debug("B8_REFRESH")
        print("[E2iPTV] _b8_refresh()")
        print("[E2iPTV] focus =", self._focus)
        print("[E2iPTV] provider =", getattr(self._b8_provider, "name", None))
        print("[E2iPTV] items =", self._b8_items)

        x      = self.OV_X + 20

        labels = []
        UNWIRED = {"Sorting", "Re-Order", "Add Playlist"}
        for item in self._b8_items:
            if item == "Refresh Interval":
                v = self._b8_interval_label(
                    self._b8_values.get("Refresh Interval", 86400))
                labels.append(("Refresh Interval:  < %s >" % v, False))
            elif item == "Enabled":
                v = "Yes" if self._b8_values.get("Enabled", True) else "No"
                labels.append(("Enabled:  < %s >" % v, False))
            elif item in ("Live TV", "Movies", "Series"):
                # Left/Right: use this provider for it or not.
                # OK: its categories (hide / unhide).
                v = "Yes" if self._b8_values.get(item, True) else "No"
                labels.append(("%s:  < %s >   OK: categories" % (item, v), False))

            elif item == "EPG Offset":
                v = self._b8_values.get("EPG Offset", 0)

                if v > 0:
                    txt = "+%d" % v
                else:
                    txt = "%d" % v

                labels.append((
                    "EPG Offset:  < %s >" % txt,
                    False
                ))

            elif item == "Update EPG":
                status = getattr(self._b8_provider, 'epg_update_status', None)
                if status == "updating":
                    labels.append(("Update EPG  (Updating...)", False))
                else:
                    try:
                        ts = open(DATA + "/cache/last_epg_update_%s.txt" % self._b8_provider.provider_key).read().strip()
                        labels.append(("Update EPG  (%s)" % ts, False))
                    except Exception:
                        labels.append(("Update EPG", False))
            elif item == "Test Connection":
                if getattr(self._b8_provider, "_testing", False):
                    labels.append(("Test Connection  (testing...)", False))
                else:
                    labels.append(("Test Connection", False))
            elif item == "Update Now":
                status = getattr(self._b8_provider, 'update_status', None)
                if status == "updating":
                    labels.append(("Update Now  (Updating...)", False))
                else:
                    ts = self._load_last_update(self._b8_provider)
                    if ts:
                        labels.append(("Update Now  (%s)" % ts, False))
                    else:
                        labels.append(("Update Now", False))
            else:
                labels.append((item, False))

        # Only 8 rows fit: scroll so the highlighted one is always shown.
        rows = 8
        top = getattr(self, "_b8_scroll", 0)
        if self._b8_sel < top:
            top = self._b8_sel
        elif self._b8_sel >= top + rows:
            top = self._b8_sel - rows + 1
        top = max(0, min(top, max(0, len(labels) - rows)))
        self._b8_scroll = top
        for i in range(rows):
            idx = top + i
            y = self.ITEM_Y0 + i * self.ITEM_H
            try:
                if idx >= len(labels):
                    self["b8_item%d" % i].setText("")
                    continue
                label, greyed = labels[idx]
                if i == 0 and top > 0:
                    label = label + "   \u25b2"
                if i == rows - 1 and idx < len(labels) - 1:
                    label = label + "   \u25bc"
                self["b8_item%d" % i].setText("  " + label)
                self["b8_item%d" % i].instance.move(ePoint(x, y))
                if greyed:
                    col = gRGB(0x888888)
                elif idx == self._b8_sel:
                    col = gRGB(0x000000)
                else:
                    col = gRGB(0xffffff)
                self["b8_item%d" % i].instance.setForegroundColor(col)
            except Exception:
                pass
        try:
            hy = self.ITEM_Y0 + (self._b8_sel - top) * self.ITEM_H
            self["b8_highlight"].instance.move(ePoint(x, hy))
        except Exception:
            pass

    def _b8_change_value(self, direction):

        item = self._b8_items[self._b8_sel]
        try:
            with open(LOG("e2iptv_b8.log"), "a") as f:
                f.write(
                    "sel=%d item=%s dir=%d\n" %
                    (
                        self._b8_sel,
                        item,
                        direction
                    )
                )
        except Exception:
            pass

        if item == "Refresh Interval":

            intervals = [3600, 7200, 14400, 28800, 43200, 86400]

            cur = self._b8_values.get("Refresh Interval", 86400)

            idx = intervals.index(cur) if cur in intervals else len(intervals) - 1
            idx = (idx + direction) % len(intervals)

            self._b8_values["Refresh Interval"] = intervals[idx]

            if self._b8_provider:
                self._b8_provider.refresh_interval = intervals[idx]
                self._save_provider_settings(self._b8_provider)

            self._b8_refresh()

        elif item in ("Live TV", "Movies", "Series"):

            cur = self._b8_values.get(item, True)
            self._b8_values[item] = not cur
            attr = {"Live TV": "show_live", "Movies": "show_movies",
                    "Series": "show_series"}[item]
            if self._b8_provider:
                setattr(self._b8_provider, attr, not cur)
                self._save_provider_settings(self._b8_provider)
            self._b8_refresh()

        elif item == "Enabled":

            cur = self._b8_values.get("Enabled", True)

            self._b8_values["Enabled"] = not cur

            if self._b8_provider:
                self._b8_provider.enabled = not cur
                self._save_provider_settings(self._b8_provider)

            self._b8_refresh()

        elif item == "EPG Offset":

            cur = self._b8_values.get("EPG Offset", 0)

            cur += direction

            # +/-12 hours covers any provider timezone (was +/-4).
            if cur > 12:
                cur = -12
            elif cur < -12:
                cur = 12

            self._b8_values["EPG Offset"] = cur

            if self._b8_provider:
                self._b8_provider.epg_offset = cur
                self._save_provider_settings(self._b8_provider)
                # Applied when the guide is read, so it shows at once.
                try:
                    from .backend.epg_store import set_provider_offset
                    set_provider_offset(
                        getattr(self._b8_provider, "provider_key", ""), cur)
                except Exception:
                    pass

            self._b8_refresh()

    def _save_provider_settings(self, provider):
        import json
        provider_key = getattr(provider, "provider_key", "")
        if not provider_key:
            return
        path = DATA + "/cache/provider_%s.json" % provider_key
        try:
            json.dump({
                "enabled":          getattr(provider, 'enabled', True),
                "refresh_interval": getattr(provider, 'refresh_interval', 86400),
                "epg_offset":       getattr(provider, 'epg_offset', 0),
                "show_live":        getattr(provider, 'show_live', True),
                "show_movies":      getattr(provider, 'show_movies', True),
                "show_series":      getattr(provider, 'show_series', True),
            }, open(path, "w"))
        except Exception:
            pass

    def _b8_action(self):
        item = self._b8_items[self._b8_sel]

        if item in ("Live TV", "Movies", "Series"):
            self._b9_type = {
                "Live TV": "live",
                "Movies": "movies",
                "Series": "series",
            }[item]
            self._b9_sel = 0
            self._b9_scroll = 0
            self._show_b9()

        elif item == "Update Now":
            self._b8_update_now()
        elif item == "Update EPG":
            self._b8_update_epg()
        elif item == "Test Connection":
            self._b8_test_connection()

    def _b8_test_connection(self):
        """Settings -> Playlists -> provider -> Test Connection: check the
        provider step by step (backend/provider_test.py) in the background
        and show where it stops working."""
        from twisted.internet import reactor
        p = self._b8_provider
        if not p or getattr(p, "_testing", False):
            return
        p._testing = True
        self._b8_refresh()

        def work():
            try:
                from .backend.provider_test import test
                ok, lines = test(p)
            except Exception as e:
                ok, lines = False, ["Test failed: %s" % e.__class__.__name__]
            reactor.callFromThread(self._b8_test_done, p, ok, lines)
        reactor.callInThread(work)

    def _b8_test_done(self, p, ok, lines):
        p._testing = False
        if ok:
            p.online = True             # a provider shown red goes white again
        try:
            with open(LOG("e2iptv_xtream.log"), "a") as f:
                f.write("%s %s TEST: %s\n" % (time.strftime("%H:%M:%S"),
                                               getattr(p, "name", "?"),
                                               " | ".join(lines)))
        except Exception:
            pass
        if self._focus == "b8" and self._b8_provider is p:
            self._b8_refresh()
        from Screens.MessageBox import MessageBox
        self.session.open(
            MessageBox,
            "%s - %s\n\n%s" % (getattr(p, "name", ""),
                                "working" if ok else "NOT working",
                                "\n".join(lines)),
            MessageBox.TYPE_INFO if ok else MessageBox.TYPE_ERROR)

    def _b8_update_now(self):
        from twisted.internet import reactor
        p = self._b8_provider
        if not p:
            return
        if getattr(p, 'update_status', None) == "updating":
            return   # already in progress
        p.update_status = "updating"
        self._b8_refresh()
        reactor.callInThread(self._update_thread, p)

    def _b8_update_epg(self):
        from twisted.internet import reactor
        p = self._b8_provider
        if not p or getattr(p, 'epg_update_status', None) == "updating":
            return
        p.epg_update_status = "updating"
        self._b8_refresh()
        reactor.callInThread(self._start_epg_update, p)

    def _start_epg_update(self, provider):
        """Launches bulk_epg_fetch.py for this provider and hands off
        immediately, rather than waiting for it to finish.

        The previous version of this (_update_epg_thread) used
        subprocess.run(), which blocks until the child process exits —
        for a provider with hundreds of channels that can be several
        minutes. That call was already on a background thread
        (reactor.callInThread), so it never froze the GUI while
        running — but a GUI restart does a clean reactor shutdown,
        which joins (waits for) every one of Twisted's threadpool
        workers before the process can actually exit. A worker stuck
        inside subprocess.run() for minutes meant restarting the GUI
        mid-update meant waiting out the ENTIRE fetch first — exactly
        what happened when a restart hung at a spinner during a
        provider EPG update.

        Popen (matching _epg_manual_refresh's already-correct pattern
        elsewhere in this file) returns the moment the child is
        launched, freeing this thread immediately. Completion is
        tracked separately by _poll_epg_update below, driven by the
        reactor's own callLater timer rather than a threadpool worker —
        a clean shutdown simply drops pending timers instead of
        waiting on them, so an in-progress EPG update can no longer
        block a GUI restart."""
        from twisted.internet import reactor as _r
        import glob, os as _os, subprocess as _sp
        provider_key = provider.provider_key
        for f in glob.glob(DATA + "/epg/%s_*.json" % provider_key):
            try: _os.remove(f)
            except Exception: pass
        to_remove = [sid for sid in state.epg_prefetched
                     if sid.startswith("%s_" % provider_key)]
        for sid in to_remove:
            state.epg_prefetched.discard(sid)

        script = ("/usr/lib/enigma2/python/Plugins/Extensions/"
                   "E2iPTV/bulk_epg_fetch.py")
        log_path = DATA + "/epg/nightly_refresh.log"
        cap(log_path, 5 * 1024 * 1024)  # start afresh past 5 MB (keeps one .old)
        process = None
        try:
            _os.makedirs(DATA + "/epg", exist_ok=True)
            env = _os.environ.copy()
            env["PYTHONPATH"] = (
                "/usr/lib/enigma2/python/Plugins/Extensions:"
                "/usr/lib/enigma2/python:"
                + env.get("PYTHONPATH", "")
            )
            # Log file is opened, written to, and closed here — same
            # as _epg_manual_refresh. Closing our copy of the file
            # object right after Popen() launches doesn't stop the
            # child from writing: it inherited its own duplicated file
            # descriptor at spawn time, same as start_new_session=True
            # already detaches it from enigma2's own process group.
            with open(log_path, "a") as log:
                log.write(
                    "\n===== EPG MANUAL REFRESH (provider %s) =====\n" % provider_key
                )
                process = _sp.Popen(
                    ["python3", "-u", script, "--provider", str(provider.name),
                     "--playlists", state.playlists_path],
                    stdin=_sp.DEVNULL,
                    stdout=log,
                    stderr=_sp.STDOUT,
                    start_new_session=True,
                    env=env,
                )
                log.write("Started bulk EPG fetch PID %d\n" % process.pid)
        except Exception:
            pass

        # Hand off to the reactor thread — the first call here bridges
        # from this background thread; _poll_epg_update reschedules
        # itself with callLater from then on.
        _r.callFromThread(self._poll_epg_update, provider, process)

    def _poll_epg_update(self, provider, process):
        """Non-blocking completion check, run on the reactor's own
        thread/timer rather than a threadpool worker — see
        _start_epg_update's docstring for why that distinction is the
        whole point of this change. process.poll() just checks whether
        the child has exited yet without waiting for it."""
        from twisted.internet import reactor
        if process is None or process.poll() is not None:
            provider.epg_update_status = None
            self._epg_update_complete(provider)
            return
        reactor.callLater(2, self._poll_epg_update, provider, process)

    def _epg_update_complete(self, provider):
        import time as _t
        ts = _t.strftime("%d/%m/%y %H:%M", _t.localtime())
        try:
            open(DATA + "/cache/last_epg_update_%s.txt" % provider.provider_key, "w").write(ts)
        except Exception:
            pass
        # Only redraw the provider panel if it's still the one showing —
        # an update finishing after you've moved on (back to Settings,
        # or out entirely) must not paint it over whatever is there now.
        if self._focus == "b8" and self._b8_provider is provider:
            self._b8_refresh()

    def _update_thread(self, provider):
        from twisted.internet import reactor
        import glob, os as _os
        provider_key = provider.provider_key

        # Clear category cache — NOT disabled_cats (user settings)
        for f in glob.glob(DATA + "/cache/cats_%s_*.json" % provider_key):
            try: _os.remove(f)
            except Exception: pass

        # Clear channel cache
        for f in glob.glob(DATA + "/cache/ch_%s_*.json" % provider_key):
            try: _os.remove(f)
            except Exception: pass

        # Reset provider so categories and channels reload fresh
        provider.live_categories_loaded = False
        provider.server_timestamp       = 0

        # Refetch categories now
        try:
            state.backend.get_live_categories(provider)
        except Exception:
            pass

        reactor.callFromThread(self._update_complete, provider)

    def _update_complete(self, provider):
        import time as _t
        ts = _t.strftime("%d/%m/%y %H:%M", _t.localtime())
        provider.update_status = None
        # Save timestamp to disk
        try:
            open(DATA + "/cache/last_update_%s.txt" % provider.provider_key, "w").write(ts)
        except Exception:
            pass
        # Only redraw the provider panel if it's still the one showing —
        # an update finishing after you've moved on (back to Settings,
        # or out entirely) must not paint it over whatever is there now.
        if self._focus == "b8" and self._b8_provider is provider:
            self._b8_refresh()

    def _load_last_update(self, provider):
        try:
            return open(DATA + "/cache/last_update_%s.txt" % provider.provider_key).read().strip()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # B9 — Category management
    # ------------------------------------------------------------------

    def _show_about(self):
        """Settings -> About: plugin version (from version.py, which the
        .ipk build writes, so it always matches the installed package)."""
        import sys
        try:
            from .version import VERSION
        except Exception:
            VERSION = "unknown"
        self._hide_panel("b6", 8)
        self._show_overlay()
        x = self.OV_X + 20
        try:
            self["b6_header"].setText("About")
            self["b6_header"].instance.move(ePoint(x, 20))
        except Exception:
            pass
        lines = [
            "E2iPTV",
            "Version:  %s" % VERSION,
            "Python:  %d.%d.%d" % sys.version_info[:3],
        ]
        for i, label in enumerate(lines):
            try:
                self["b6_item%d" % i].setText("  " + label)
                self["b6_item%d" % i].instance.move(
                    ePoint(x, self.ITEM_Y0 + i * self.ITEM_H))
                self["b6_item%d" % i].instance.setForegroundColor(gRGB(0xffffff))
            except Exception:
                pass
        try:
            self["b6_highlight"].instance.move(ePoint(3500, 0))   # nothing to select
        except Exception:
            pass
        self._focus = "about"

    def _playback_menu(self, current=None):
        """Settings -> Playback: which player Live TV uses (see
        player.make_live_ref; from the next channel change) and whether
        E2iPTV opens by itself when the box starts (plugin.py)."""
        from .side_menu import SideMenu
        from .player import available_live_players, live_player_type
        import json
        try:
            with open(DATA + "/cache/app_settings.json") as f:
                _app = json.load(f)
        except Exception:
            _app = {}
        on_at_start = bool(_app.get("open_at_startup", False))
        watchdog = bool(_app.get("live_watchdog", True))
        cur = live_player_type()
        opts = [(("●  " if t == cur else "    ") + name, t)
                for t, name in available_live_players()]
        opts.append(("Auto-restart frozen channels:  %s"
                     % ("Yes" if watchdog else "No"), "watchdog"))
        opts.append(("Open E2iPTV at start-up:  %s"
                     % ("Yes" if on_at_start else "No"), "autostart"))
        try:
            from .music import music_root
            mroot = music_root()
        except Exception:
            mroot = ""
        opts.append(("Music folder:  %s" % (mroot or "Not set"), "music_root"))
        if isinstance(current, str):
            current = next((i for i, o in enumerate(opts) if o[1] == current), 0)
        if current is None:
            current = next((i for i, o in enumerate(opts) if o[1] == cur), 0)
        self.session.openWithCallback(
            lambda c, n=len(opts): self._playback_chosen(c, n),
            SideMenu, "Playback", opts,
            detail="Live TV player - try another if sound drifts",
            current=current)

    def _playback_chosen(self, choice, n=0):
        if not choice:
            return
        import json
        if choice[1] == "music_root":
            # Folder picker (the same one as the locations in Other).
            import os
            try:
                from .music import music_root
                start = music_root()
            except Exception:
                start = ""
            while start and start != "/" and not os.path.isdir(start):
                start = os.path.dirname(start)
            if not start or not os.path.isdir(start):
                start = "/media"
            self._b10_target = "music"
            self._b10_load(start)
            self._show_b10()
            return
        if choice[1] in ("autostart", "watchdog"):
            key, default = {"autostart": ("open_at_startup", False),
                            "watchdog": ("live_watchdog", True)}[choice[1]]
            path = DATA + "/cache/app_settings.json"
            try:
                try:
                    with open(path) as f:
                        data = json.load(f)
                except Exception:
                    data = {}
                data[key] = not data.get(key, default)
                with open(path, "w") as f:
                    json.dump(data, f)
            except Exception as e:
                print("[E2iPTV] playback setting:", e)
            self._playback_menu(current=choice[1])   # stay on the switch
            return
        try:
            from .player import set_live_player_type
            set_live_player_type(choice[1])
        except Exception as e:
            print("[E2iPTV] live player setting:", e)
        self._playback_menu()           # reopen showing the new choice

    def _data_folder_notice(self):
        from Screens.MessageBox import MessageBox
        path = getattr(self, "_data_folder_pending", "")
        self.session.open(
            MessageBox,
            "Data folder set to %s.\n\nRestart the GUI for it to take effect. "
            "Your existing data (channel order, bouquets, guide, settings) "
            "stays in the old folder; copy it across if you want to keep it."
            % path, MessageBox.TYPE_INFO)

    def _show_other(self):
        self._hide_panel("b6", 8)
        self._show_overlay()
        x = self.OV_X + 20
        try:
            self["b6_header"].setText("Other")
            self["b6_header"].instance.move(ePoint(x, 20))
        except Exception:
            pass
        self._other_sel = 0
        self._other_refresh()
        self._focus = "other"

    def _other_refresh(self):
        import os
        x   = self.OV_X + 20
        rec = getattr(state, 'recording_path', HDD + '/movie/')
        # state.playlists_path is always set (see its own default and
        # comment in state.py) — no fallback needed or wanted here;
        # one was a different, inconsistent hardcoded value (this file
        # had two, disagreeing with each other and with state.py's own
        # default) rather than a genuine safety net.
        pl  = state.playlists_path
        rso = getattr(state, 'rec_start_offset',     0)
        reo = getattr(state, 'rec_end_offset',       0)
        cso = getattr(state, 'catchup_start_offset', 0)
        ceo = getattr(state, 'catchup_end_offset',   0)
        items = [
            "Recording:  %s" % rec,
            "Playlists:  %s" % os.path.dirname(pl),
            "Rec Start Offset:  < %d mins >" % rso,
            "Rec End Offset:  < %d mins >" % reo,
            "Catchup Start Offset:  < %d mins >" % cso,
            "Catchup End Offset:  < %d mins >" % ceo,
            "Picons:  %s" % _picon_root(),
            "Data Folder:  %s" % _data_folder_label(),
        ]
        for i, label in enumerate(items):
            y = self.ITEM_Y0 + i * self.ITEM_H
            try:
                self["b6_item%d" % i].setText("  " + label)
                self["b6_item%d" % i].instance.move(ePoint(x, y))
                col = gRGB(0x000000) if i == self._other_sel else gRGB(0xffffff)
                self["b6_item%d" % i].instance.setForegroundColor(col)
            except Exception:
                pass
        try:
            hy = self.ITEM_Y0 + self._other_sel * self.ITEM_H
            self["b6_highlight"].instance.move(ePoint(x, hy))
        except Exception:
            pass

    def _other_open_browser(self):
        """Open embedded directory browser without leaving screen2."""
        import os
        target = {0: "recording", 6: "picons", 7: "data"}.get(self._other_sel, "playlists")
        self._b10_target = target
        if target == "recording":
            start = getattr(state, 'recording_path', HDD + '/movie/')
        elif target == "data":
            from .paths import HDD
            start = HDD if os.path.isdir(HDD) else "/media"
        elif target == "picons":
            # Start at the current picon folder, or the nearest parent
            # that exists if it hasn't been created yet.
            start = _picon_root().rstrip("/") or "/"
            while start != "/" and not os.path.isdir(start):
                start = os.path.dirname(start) or "/"
        else:
            # Same reasoning as _other_refresh's identical simplification
            # just above — this was a second, yet differently-named
            # hardcoded fallback (a different filename to the other
            # one), never actually needed.
            start = os.path.dirname(state.playlists_path)
        self._b10_load(start)
        self._show_b10()

    def _b10_load(self, path):
        import os
        self._b10_entries = [("✔  Use this folder", path)]
        parent = os.path.dirname(path.rstrip("/"))
        if parent and parent != path:
            self._b10_entries.append(("..", parent))
        try:
            for d in sorted(d for d in os.listdir(path)
                            if os.path.isdir(os.path.join(path, d))):
                self._b10_entries.append((d, os.path.join(path, d)))
        except Exception:
            pass
        self._b10_sel    = 0
        self._b10_scroll = 0

    def _show_b10(self):
        self._hide_panel("b6", 8)
        self._show_overlay()
        x = self.OV_X + 20
        label = {
            "recording": "Recording Location",
            "picons": "Picons Location",
            "data": "Data Folder",
            "music": "Music Folder",
        }.get(self._b10_target, "Playlists Location")
        try:
            self["b10_header"].setText(label)
            self["b10_header"].instance.move(ePoint(x, 20))
        except Exception:
            pass
        self._b10_refresh()
        self._focus = "b10"

    def _hide_b10(self):
        try:
            self["b10_header"].instance.move(ePoint(3500, 0))
            self["b10_highlight"].instance.move(ePoint(3500, 0))
            for i in range(12):
                self["b10_item%d" % i].instance.move(ePoint(3500, 0))
        except Exception:
            pass

    def _b10_refresh(self):
        import os
        x     = self.OV_X + 20
        scroll = min(max(0, self._b10_sel - 5),
                    max(0, len(self._b10_entries) - self._b10_visible))
        self._b10_scroll = scroll
        tile_row = self._b10_sel - scroll

        for i in range(self._b10_visible):
            idx = scroll + i
            y   = self.ITEM_Y0 + i * 66
            try:
                if idx < len(self._b10_entries):
                    name, path = self._b10_entries[idx]
                    self["b10_item%d" % i].setText("  " + name)
                    col = gRGB(0x000000) if i == tile_row else gRGB(0xffffff)
                    self["b10_item%d" % i].instance.setForegroundColor(col)
                else:
                    self["b10_item%d" % i].setText("")
                self["b10_item%d" % i].instance.move(ePoint(x, y))
            except Exception:
                pass
        try:
            hy = self.ITEM_Y0 + tile_row * 66
            self["b10_highlight"].instance.move(ePoint(x, hy))
        except Exception:
            pass

    def _b10_ok(self):
        import json, os
        if not self._b10_entries:
            return
        name, path = self._b10_entries[self._b10_sel]
        if name.startswith("✔"):
            # Confirm selection — save and return to Other
            try:
                data = {}
                try:
                    data = json.load(open(DATA + "/cache/app_settings.json"))
                except Exception:
                    pass
                if self._b10_target == "music":
                    from .music import set_music_root
                    set_music_root(path)
                    self._hide_b10()
                    self._show_b6()
                    self._playback_menu(current=None)
                    return

                if self._b10_target == "recording_bookmark":
                    import os

                    bookmarks = self._recording_bookmarks()

                    if not any(
                        isinstance(item, dict)
                        and item.get("path") == path
                        for item in bookmarks
                    ):
                        bookmarks.append({
                            "name": os.path.basename(path.rstrip("/")) or path,
                            "path": path,
                        })
                        self._save_recording_bookmarks(bookmarks)

                    self._hide_b10()

                    self._rec_root = path
                    self._recordings_load(path)
                    self._focus = "recordings"
                    self._b2_active_local_panel = "recordings"
                    self._recordings_render()
                    self._b1_collapse_rail()
                    self._b1_sel = state.b1_sel
                    self._b1_refresh()
                    return

                if self._b10_target == "recording":
                    state.recording_path = path
                    data["recording_path"] = path
                    # Persist to disk so it survives restart
                    try:
                        import json, os
                        _sp = DATA + '/cache/settings.json'
                        _s = {}
                        if os.path.exists(_sp): _s = json.load(open(_sp))
                        _s['recording_path'] = path
                        json.dump(_s, open(_sp,'w'))
                    except: pass
                elif self._b10_target == "picons":
                    from .backend.picon import set_root
                    set_root(path)
                elif self._b10_target == "data":
                    from .paths import set_data_folder, HDD
                    if path.rstrip("/") != HDD.rstrip("/"):
                        set_data_folder(path)
                        self._data_folder_pending = path
                        from twisted.internet import reactor
                        reactor.callLater(0.3, self._data_folder_notice)
                else:
                    import os
                    full = os.path.join(path, "playlists.txt")
                    state.playlists_path = full
                    data["playlists_path"] = full
                json.dump(data, open(DATA + "/cache/app_settings.json", "w"))
            except Exception:
                pass
            self._hide_b10()
            self._show_other()
        else:
            # Navigate into folder
            self._b10_load(path)
            self._b10_refresh()

    def _show_b9(self):
        self._hide_panel("b8", 8)
        p = self._b8_provider

        if p:
            try:
                if self._b9_type == "movies":
                    state.backend.get_movie_categories(p)
                elif self._b9_type == "series":
                    state.backend.get_series_categories(p)
                else:
                    state.backend.get_live_categories(p)
            except Exception:
                pass

            self._b9_cats = list(state.backend.category_store.all())

            try:
                from .backend.provider_category_settings import load
                self._b9_disabled = load(p, self._b9_type)
            except Exception:
                self._b9_disabled = set()
        else:
            self._b9_cats = []
            self._b9_disabled = set()

        x = self.OV_X + 20

        try:
            name = p.name if p else ""
            label = {
                "live": "Live TV",
                "movies": "Movies",
                "series": "Series",
            }.get(self._b9_type, "Categories")

            self["b9_header"].setText(
                name + " - " + label + " Categories"
            )
            self["b9_header"].instance.move(ePoint(x, 20))
        except Exception:
            pass

        self._b9_refresh()
        self._focus = "b9"

    def _disabled_cats_path(self, provider):
        # LEGACY COMPATIBILITY ONLY.
        # Persistent category identity is now provider-name based.
        return DATA + "/cache/disabled_categories.json"

    def _save_disabled_cats(self, provider):
        from .backend.provider_category_settings import save
        save(provider, self._b9_disabled, self._b9_type)

    def _b9_refresh(self):
        x    = self.OV_X + 20
        cats = self._b9_cats
        p    = self._b8_provider

        disabled = self._b9_disabled if p else set()

        rows = [
            ("Hide All Categories", None),
            ("Unhide All Categories", None),
        ]

        from .backend.private import ids as _private_ids
        private = _private_ids(p, getattr(self, "_b9_type", "live")) if p else set()
        for cat in cats:
            label = cat.category_name
            if str(cat.category_id) in private:
                label += "   [Private]"
            rows.append((label, cat))

        n = len(rows)

        scroll = min(
            max(0, self._b9_sel - 4),
            max(0, n - self._b9_visible)
        )

        tile_row = self._b9_sel - scroll
        self._b9_scroll = scroll

        for i in range(self._b9_visible):
            idx = scroll + i
            y   = self.ITEM_Y0 + i * self.ITEM_H
            try:
                if idx < n:

                    label, cat = rows[idx]

                    if cat is None:
                        enabled = True
                    else:
                        enabled = cat.category_id not in disabled

                    self["b9_item%d" % i].setText("  " + label)

                    self["b9_item%d" % i].instance.move(ePoint(x, y))
                    if i == tile_row:
                        col = gRGB(0x000000)
                    elif enabled:
                        col = gRGB(0xffffff)
                    else:
                        col = gRGB(0x1a98ff)
                    self["b9_item%d" % i].instance.setForegroundColor(col)
                else:
                    self["b9_item%d" % i].setText("")
                    self["b9_item%d" % i].instance.move(ePoint(x, y))
            except Exception:
                pass
        try:
            hy = self.ITEM_Y0 + tile_row * self.ITEM_H
            self["b9_highlight"].instance.move(ePoint(x, hy))
        except Exception:
            pass

    def _b9_private_toggle(self):
        """Left/Right on a category: mark / unmark it Private (left out of
        every list unless shown with 5 pressed twice - backend/private.py)."""
        p = self._b8_provider
        idx = self._b9_sel - 2
        if not p or idx < 0 or idx >= len(self._b9_cats):
            return
        from .backend.private import is_private, mark
        kind = getattr(self, "_b9_type", "live")
        cid = self._b9_cats[idx].category_id
        mark(p, kind, cid, not is_private(p, kind, cid))
        self._b9_refresh()

    def _b9_toggle(self):

        p = self._b8_provider

        if not p:
            return

        if self._b9_sel == 0:

            self._b9_disabled = set(
                str(cat.category_id)
                for cat in self._b9_cats
            )

            self._save_disabled_cats(p)
            self._b9_refresh()
            return

        if self._b9_sel == 1:

            self._b9_disabled.clear()

            self._save_disabled_cats(p)
            self._b9_refresh()
            return

        idx = self._b9_sel - 2

        if idx >= len(self._b9_cats):
            return

        cat = self._b9_cats[idx]

        cat_id = str(cat.category_id)

        if cat_id in self._b9_disabled:
            self._b9_disabled.discard(cat_id)
        else:
            self._b9_disabled.add(cat_id)

        self._save_disabled_cats(p)
        self._b9_refresh()

    def _refresh(self):
        self._debug("REFRESH focus=%s" % self._focus)
        """Populate all panels from shared state."""

        self._b2_mirror()
        self._b3_mirror()
        self._b5_mirror()
        self._info_mirror()

    # ------------------------------------------------------------------
    # B2 mirror — read-only copy of screen1 B2 state
    # ------------------------------------------------------------------

    def _b2_mirror(self):
        # Ask whichever screen opened this one for its own category/
        # provider rows, rather than assuming Live TV and reading
        # global state (which is how Series' own B2 data used to leak
        # into this mirror — see the state.b2_owner workaround this
        # replaces).
        flat, sel = [], 0
        if self._content_screen is not None:
            getter = getattr(self._content_screen, "get_mirror_b2", None)
            if getter:
                try:
                    flat, sel = getter()
                except Exception:
                    flat, sel = [], 0

        tr    = self._b2_tile_row
        off   = self._b2_row_offset
        total = len(flat)

        # Hide the highlight tile entirely when the mirrored list is
        # empty (e.g. Favourites' get_mirror_b2, which genuinely has no
        # B2-shaped content to mirror — see its own comment) rather
        # than just clearing its text. Every existing content screen
        # before this always had real B2 rows, so this case was never
        # actually exercised — leaving the tile's own background
        # visible with nothing on it, floating with no other content
        # around it to explain what it was.
        if total == 0:
            self["b2_tile"].hide()
            self["b2_row_tr_fallback"].hide()
            for i in range(self._b2_total_rows):
                if "b2_row%d" % i in self:
                    self["b2_row%d" % i].setText("")
            return

        self["b2_tile"].show()

        # When B3 has nothing of its own to mirror alongside this (e.g.
        # Favourites — its entries are plain B2-shaped text rows, but
        # long ones: titles plus [provider, type], which B2's normal
        # width (sized for short category labels like "Movies") just
        # truncates) B2 is free to take over that unused space and run
        # almost to the full screen edge instead, matching how much
        # room the actual Favourites screen itself gives the same
        # text. Resize back to normal whenever B3 does have real
        # content to show (e.g. MediaBrowser), since B2 must not cover
        # it there.
        b3_has_content = False
        if self._content_screen is not None:
            b3_getter = getattr(self._content_screen, "get_mirror_b3", None)
            if b3_getter:
                try:
                    b3_channels = b3_getter()[0]
                    b3_has_content = bool(b3_channels)
                except Exception:
                    b3_has_content = False

        row_w = (B2_W - 10) if b3_has_content else (1900 - (B2_X + 5))

        # When widened, these need to render above pig_cover (zPosition
        # 12 — see its own comment: shown by default, only moved aside
        # once genuine Live TV data is confirmed, which Favourites
        # never has) rather than being cut short at its left edge
        # (764) the way the normal, narrower B2 width never reaches.
        # pig_cover is a plain background-coloured panel whenever it's
        # actually shown, not real video, so text rendering over it is
        # safe — the risk this accepts is only if _sync_pig_cover ever
        # incorrectly decided a non-Live-TV mirror like this one had
        # genuine Live TV data to show instead.
        row_z = 3 if b3_has_content else 13

        try:
            if b3_has_content:
                self["b2b3_divider"].show()
            else:
                # Hidden along with widening B2's own rows above — with
                # nothing of B3's to divide from, this thin fixed-
                # position line would otherwise sit part-way across
                # wherever B2's now-much-longer text runs underneath
                # it (e.g. Favourites' full title + [provider, type]
                # strings, easily long enough to reach past it).
                self["b2b3_divider"].hide()
        except Exception:
            pass

        for i in range(self._b2_total_rows):
            if "b2_row%d" % i in self:
                try:
                    self["b2_row%d" % i].instance.resize(eSize(row_w, B2_ITEM_H))
                    self["b2_row%d" % i].instance.setZPosition(row_z)
                except Exception:
                    pass
        try:
            self["b2_tile"].instance.resize(eSize(row_w, B2_ITEM_H))
            self["b2_tile"].instance.setZPosition(row_z + 1)
            self["b2_row_tr_fallback"].instance.resize(eSize(row_w, B2_ITEM_H))
            self["b2_row_tr_fallback"].instance.setZPosition(row_z)
        except Exception:
            pass

        # Clamp the window start so a selection near either end of the
        # list doesn't just show blank rows above/below the tile
        # purely because there isn't enough real content on one side
        # to reach row tr. This must NOT be the default — Live TV,
        # Movies and Series all use B2Common's own shared category
        # rendering, which has always shown the selection at this
        # fixed row regardless of its position in the overall list
        # (never scrolling it to the top) — that's what the real
        # screen itself looks like, so the mirror needs to match it
        # exactly, not "improve" on it. Only a content screen that
        # explicitly opts in (get_mirror_b2_clamped=True — currently
        # just Favourites, whose own list is a plain, naturally-
        # scrolling widget with no fixed-row convention of its own to
        # match) gets this behaviour; everything else keeps the
        # original, always-fixed-at-tr positioning unconditionally.
        clamp = False
        if self._content_screen is not None:
            clamp = bool(getattr(self._content_screen, "get_mirror_b2_clamped", False))

        if clamp:
            max_first = max(0, total - self._b2_total_rows)
            first_idx = max(0, min(sel - tr, max_first))
        else:
            first_idx = sel - tr

        # Row (relative to off) the selection actually lands on now —
        # normally this is exactly tr, but clamping above can shift it
        # when the list's too short on one side to keep it centred.
        sel_row = sel - first_idx + off

        try:
            self["b2_tile"].instance.move(
                ePoint(B2_X + 5, B2_Y + (sel_row - off) * B2_ITEM_H)
            )
        except Exception:
            pass

        self["b2_tile"].setText(flat[sel][0] if 0 <= sel < total else "")

        # b2_tile has moved off its static row (tr) — nothing else was
        # ever built to cover that exact position (see the widget's
        # own comment), so it needs filling here with whatever item
        # actually belongs there now, or it's a bare gap in the list.
        # tr itself is untouched — only ever shows a real row's
        # content when the tile has genuinely moved elsewhere.
        if sel_row != tr + off:
            tr_idx = first_idx + tr
            self["b2_row_tr_fallback"].setText(
                flat[tr_idx][0] if 0 <= tr_idx < total else "")
            self["b2_row_tr_fallback"].show()
        else:
            self["b2_row_tr_fallback"].hide()

        for i in range(self._b2_total_rows):
            if i == sel_row:
                if "b2_row%d" % i in self:
                    self["b2_row%d" % i].setText("")
                continue
            idx = first_idx + (i - off)
            if "b2_row%d" % i in self:
                self["b2_row%d" % i].setText(
                    flat[idx][0] if 0 <= idx < total else "")

    # ------------------------------------------------------------------
    # B3 mirror — passive channel list
    # ------------------------------------------------------------------

    def _b3_mirror(self):
        # Same principle as B2 above — ask the content screen directly
        # rather than assuming Live TV via global state.
        channels, scroll, focused = [], 0, 0
        if self._content_screen is not None:
            getter = getattr(self._content_screen, "get_mirror_b3", None)
            if getter:
                try:
                    channels, scroll, focused = getter()
                except Exception:
                    channels, scroll, focused = [], 0, 0

        self._sync_pig_cover(channels)

        if not channels:
            self["b3_list"].setList([])
            return

        vis_start, vis_end = self._b3_window(
            len(channels), scroll, B3_VISIBLE
        )

        entries = self._b3_build_entries(
            channels, vis_start, vis_end, focused,
            item_w=B3_W, item_h=B3_ITEM_H,
        )

        self["b3_list"].setList(entries)

    def _sync_pig_cover(self, channels):
        """Cover the live video preview unless the content screen
        genuinely has Live TV data. Pure GUI panel, not the Pig/video-
        source widget itself — see the skin comment where pig_cover is
        defined for why that distinction matters here."""
        try:
            if channels:
                self["pig_cover"].instance.move(ePoint(3500, 0))
            else:
                self["pig_cover"].instance.move(ePoint(764, 0))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # B5 mirror — same viewport as screen1, narrower (530px)
    # ------------------------------------------------------------------

    def _b5_mirror(self):
        W    = B5_W_SKIN
        S3_W = 1300
        WIN  = 7200
        IH   = 66
        VIS  = 10

        # Same source as B3 — if the content screen doesn't have a
        # channel-shaped list (e.g. Series), this is honestly empty
        # rather than showing Live TV's grid regardless of context.
        channels = []
        if self._content_screen is not None:
            getter = getattr(self._content_screen, "get_mirror_b3", None)
            if getter:
                try:
                    channels, _, _ = getter()
                except Exception:
                    channels = []

        if not channels:
            self["s1_b5_list"].setList([])
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

        vis_start = state.b5_scroll
        vis_end   = min(len(channels), vis_start + VIS)
        entries   = []

        for idx, ch in enumerate(channels[vis_start:vis_end]):
            abs_idx    = vis_start + idx
            is_focused = abs_idx == state.b5_focused
            events     = state.backend.epg_store.get_for(ch)

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
                backcolor=0x0a4aa8, backcolor_sel=0x0a4aa8))

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
                    bg = 0xd0e8ff; fg = 0x000000
                else:
                    bg = 0x1a98ff; fg = 0xffffff

                entry.append(MultiContentEntryText(
                    pos=(x1 + 1, 1), size=(w - 2, IH - 2),
                    font=1, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text="", color=fg, color_sel=fg,
                    backcolor=bg, backcolor_sel=bg))

                rec_colour = _epg_recording_colour(
                    state, ch.stream_id, ev.start, ev.end, channel=ch
                )

                if rec_colour:
                    bg = rec_colour
                    fg = 0xffffff
                elif is_cursor:
                    cursor_found = True
                    bg = 0xd0e8ff
                    fg = 0x000000
                else:
                    bg = 0x1a98ff
                    fg = 0xffffff

                entry.append(MultiContentEntryText(
                    pos=(x1 + 1, 1), size=(w - 2, IH - 2),
                    font=1, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text="", color=fg, color_sel=fg,
                    backcolor=bg, backcolor_sel=bg))

                if w > 10:
                    entry.append(MultiContentEntryText(
                        pos=(x1 + 24, 2), size=(w - 26, IH - 4),
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

    # ------------------------------------------------------------------
    # Info mirror
    # ------------------------------------------------------------------

    def _info_mirror(self):
        channels = []
        if self._content_screen is not None:
            getter = getattr(self._content_screen, "get_mirror_b3", None)
            if getter:
                try:
                    channels, _, _ = getter()
                except Exception:
                    channels = []

        if not channels:
            self["s1_info_title"].setText("")
            self["s1_info_times"].setText("")
            self["s1_info_desc"].setText("")
            return

        now    = int(time.time())
        cursor = state.b5_cursor_time or now
        idx    = state.b5_focused
        if idx < 0 or idx >= len(channels):
            return

        ch     = channels[idx]
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
                for _rk, _rec in state.active_recordings.items():
                    if (_rec.get("provider_id") == _rec_prov.provider_id
                            and _rec.get("stream_id") == ch.stream_id
                            and _rec.get("event_start") == ev.start):
                        if _rec.get("status") == "active":
                            indicator, colour = "   ●●● REC", 0xff4444
                        elif _rec.get("status") == "scheduled":
                            indicator, colour = "   ●●● SCHEDULED", 0x44cc44
                        break

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
