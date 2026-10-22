# =============================================================================
# E2iPTV — Favourites
# Movies and Series only (no Live channels). A Series is favourited at
# the series level — the whole thing, all seasons/episodes included —
# never per-season or per-episode; a Movie is favourited as itself,
# the only level it has.
#
# Each entry is a provider-scoped *reference* (content type + provider
# ID + item ID + the category ID it lives in), not a saved copy of the
# actual Movie/Series object. The object itself gets resolved fresh
# from the backend at open time — see media_browser.py's
# jump_to_series mechanism (built for Search, reused here so opening a
# favourited series looks exactly like normal browsing, not a separate
# "favourites view"). Storing more than a reference and trusting it
# later is exactly the mistake this whole session was spent finding
# and fixing elsewhere: Xtream IDs are only unique *within* a
# provider, never globally, so a bare stream_id/series_id alone isn't
# a safe key — every entry here carries provider_id specifically to
# avoid that.
# =============================================================================

from . import theme as _T
from .paths import HDD, LOG  # data folder / debug logs
from .paths import DATA  # E2iPTV_Data folder
import json
import os

FAVOURITES_PATH = DATA + "/cache/favourites.json"


DETAIL_FIELDS = ("year", "genre", "rating", "plot", "release_date")


def item_details(item):
    """What the Favourites screen shows for a movie or series: its
    poster link and a few details. "details" marks it as looked up."""
    d = {"cover": getattr(item, "cover", "") or getattr(item, "stream_icon", "") or "",
         "details": True}
    for f in DETAIL_FIELDS:
        v = getattr(item, f, "")
        d[f] = v if isinstance(v, (str, int, float)) else ""
    return d


class FavouritesStore(object):
    """Plain JSON-backed list of favourited movies/series."""

    def __init__(self):
        self._entries = self._load()

    # -------------------------------------------------------------

    def _load(self):

        try:
            with open(FAVOURITES_PATH) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass

        return []

    def _save(self):

        try:
            directory = os.path.dirname(FAVOURITES_PATH)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            with open(FAVOURITES_PATH, "w") as f:
                json.dump(self._entries, f, indent=2)
        except Exception:
            pass

    @staticmethod
    def _key(content_type, provider_key, item_id):
        return (str(content_type), str(provider_key), str(item_id))

    def _entry_key(self, entry):
        return self._key(
            entry.get("content_type"),
            entry.get("provider_key"),
            entry.get("item_id"),
        )

    # -------------------------------------------------------------

    def all(self):
        """Every favourite, newest first."""
        return list(reversed(self._entries))

    def all_of_type(self, content_type):
        return [
            e for e in self.all()
            if e.get("content_type") == content_type
        ]

    def favourite_keys(self, content_type):
        """A set of (provider_key, item_id) string-pairs for the given
        content type."""
        return set(
            (str(e.get("provider_key")), str(e.get("item_id")))
            for e in self._entries
            if e.get("content_type") == content_type
        )

    def is_favourite(self, content_type, provider_key, item_id):

        target = self._key(content_type, provider_key, item_id)

        return any(
            self._entry_key(e) == target
            for e in self._entries
        )

    def add(self, content_type, provider, item, category_id):
        """provider/item are the actual Provider/Movie/Series objects
        — only their IDs and a display name get stored, per this
        module's docstring."""

        if content_type not in ("movie", "series"):
            raise ValueError(
                "Favourites only supports movie/series, got %r" %
                (content_type,)
            )

        provider_key = getattr(provider, "provider_key", None)

        item_id = (
            getattr(item, "stream_id", None) if content_type == "movie"
            else getattr(item, "series_id", None)
        )

        if not provider_key or item_id is None:
            return False

        if self.is_favourite(content_type, provider_key, item_id):
            return False

        entry = {
            "content_type": content_type,
            "provider_key": provider_key,
            "item_id": item_id,
            "category_id": category_id,
            "name": getattr(item, "name", "") or "",
        }
        entry.update(item_details(item))
        self._entries.append(entry)

        self._save()

        return True

    def set_details(self, content_type, provider_key, item_id, details):
        """Save the poster link / year / plot ... shown on the
        Favourites screen, so it needn't be looked up again."""
        target = self._key(content_type, provider_key, item_id)
        for e in self._entries:
            if self._entry_key(e) == target:
                e.update(details)
                self._save()
                return True
        return False

    def remove(self, content_type, provider_key, item_id):

        target = self._key(content_type, provider_key, item_id)

        before = len(self._entries)

        self._entries = [
            e for e in self._entries
            if self._entry_key(e) != target
        ]

        if len(self._entries) != before:
            self._save()
            return True

        return False


# =============================================================================
# UI — B1's "Favourites" entry: a single browsable list.
#
# Follows the SAME screen-stacking pattern search.py's module docstring
# describes in full (matching how Screen1/Screen2/MediaBrowser already
# handle their own B1-rail navigation): this screen stays open while
# browsing, and when an item is actually opened, it closes ITSELF with
# a payload describing what to do rather than opening the target
# screen directly and then trying to close this one afterwards — the
# earlier version did that, and it corrupted Enigma2's session modal-
# stack tracking in a way that surfaced as a crash on a later,
# unrelated session.open() call, and separately left this screen
# orphaned in the stack whenever it was navigated away from via the B1
# rail instead of Cancel, to resurface later.
# =============================================================================

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MultiContent import MultiContentEntryText
from Components.GUIComponent import GUIComponent
from enigma import (
    eTimer, eListbox, eListboxPythonMultiContent,
    gFont, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER, ePoint, gRGB
)
import threading
import time
from Components.Pixmap import Pixmap
from Tools.LoadPixmap import LoadPixmap

from .state import state
from .player import play_movie
from .b1common import B1Common, B1_RAIL_COLOUR, B1_COLLAPSED_W, B1_EXPANDED_W


_DEBUG_LOG_PATH = LOG("e2iptv_favourites_debug.log")


def _debug_log(msg):
    """Writes to a plain file rather than print() — this plugin's own
    output, nothing about the box touched to read it back. Just cat
    the file afterward: `cat /tmp/e2iptv_favourites_debug.log`."""
    try:
        with open(_DEBUG_LOG_PATH, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _resolve_favourite(entry):
    """A favourite entry is just a provider-scoped reference (see this
    module's docstring) — this re-fetches the actual, current
    Provider/Movie/Series objects it points to, the same category-walk
    approach media_browser.py's _do_jump_to_series already proved out
    for Search. Returns (provider, item), either of which may be None
    if the provider's gone, disabled, or the item's no longer in the
    category it was favourited from (removed/moved upstream since)."""

    name = entry.get("name", "")

    provider = next(
        (
            p for _name, p in (state.providers or [])
            if str(getattr(p, "provider_key", None))
            == str(entry.get("provider_key"))
        ),
        None
    )

    if provider is None:
        _debug_log(
            "Favourites resolve '%s': provider_key %r not found "
            "in state.providers" % (name, entry.get("provider_key"))
        )
        return None, None

    content_type = entry.get("content_type")
    category_id = entry.get("category_id")
    item_id = entry.get("item_id")

    try:
        categories = (
            state.backend.get_movie_categories(provider)
            if content_type == "movie"
            else state.backend.get_series_categories(provider)
        )
    except Exception as e:
        _debug_log(
            "Favourites resolve '%s': category fetch failed: %s" %
            (name, e)
        )
        categories = []

    category = next(
        (
            c for c in categories
            if str(getattr(c, "category_id", "")) == str(category_id)
        ),
        None
    )

    if category is None:
        _debug_log(
            "Favourites resolve '%s': category_id %r not found "
            "among %d categories fetched for provider %r (have: %r)" % (
                name, category_id, len(categories),
                getattr(provider, "name", ""),
                [getattr(c, "category_id", None) for c in categories][:20]
            )
        )
        return provider, None

    try:
        items = (
            state.backend.get_movies(provider, category)
            if content_type == "movie"
            else state.backend.get_series(provider, category)
        )
    except Exception as e:
        _debug_log(
            "Favourites resolve '%s': item fetch failed: %s" %
            (name, e)
        )
        items = []

    id_attr = "stream_id" if content_type == "movie" else "series_id"

    item = next(
        (
            i for i in items
            if str(getattr(i, id_attr, "")) == str(item_id)
        ),
        None
    )

    if item is None:
        _debug_log(
            "Favourites resolve '%s': item_id %r not found "
            "among %d items fetched for category %r (have ids: %r)" % (
                name, item_id, len(items),
                getattr(category, "category_name", category_id),
                [getattr(i, id_attr, None) for i in items][:20]
            )
        )

    return provider, item


# Same GUIComponent shape as search.py's _SearchList (GUI_WIDGET +
# postWidgetCreate, the pattern proven to render correctly here, not
# the lower-level GUIcreate/GUIdelete) — duplicated rather than
# imported, same reasoning as every other small UI helper class
# throughout this project: keeps this file's own import direction
# simple rather than reaching into search.py for it.
_LIST_ITEM_H = 60


class _FavouritesList(GUIComponent):

    GUI_WIDGET = eListbox

    def __init__(self, entries):
        GUIComponent.__init__(self)
        self.l = eListboxPythonMultiContent()
        self.l.setItemHeight(_LIST_ITEM_H)
        self.l.setFont(0, gFont("E2iRegular", 30))
        self._entries = []
        self.setEntries(entries, 0)

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setWrapAround(False)
        instance.setSelectionEnable(False)

    def preWidgetRemove(self, instance):
        pass

    def setEntries(self, labels, selected_index=0):
        """Only the rows that fit are given to the box's list, a window
        that follows the highlight - the list widget doesn't scroll by
        itself with its selection bar switched off (same as Search and
        Music), so with more than a screenful the rest couldn't be
        reached."""
        self._entries = labels
        if not labels:
            self.l.setList([])
            return
        sel = max(0, min(selected_index, len(labels) - 1))
        visible = 15                       # 900 high / 60 a row
        try:
            if self.instance:
                visible = max(1, self.instance.size().height() // _LIST_ITEM_H)
        except Exception:
            pass
        # The highlight stays on the middle row and the list moves past
        # it (as on Live TV / Movies / Series); rows before the first or
        # after the last entry are left blank.
        mid = visible // 2
        rows = []
        for r in range(visible):
            i = sel - mid + r
            if i < 0 or i >= len(labels):
                rows.append([None, MultiContentEntryText(
                    pos=(0, 0), size=(1800, _LIST_ITEM_H), font=0,
                    flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text="",
                    backcolor=_T.PANEL, backcolor_sel=_T.PANEL)])
                continue
            bg = _T.HI if i == sel else _T.PANEL
            fg = 0x000000 if i == sel else 0xffffff
            rows.append([
                None,
                MultiContentEntryText(
                    pos=(20, 0), size=(1800, _LIST_ITEM_H),
                    font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text=labels[i], color=fg, color_sel=fg,
                    backcolor=bg, backcolor_sel=bg
                )
            ])
        self.l.setList(rows)
        if self.instance:
            self.instance.moveSelectionTo(0)

    def setSelection(self, index):
        self.setEntries(self._entries, index)


from .side_menu import SideMenu


class _FavouriteChoiceMenu(SideMenu):
    """Watch / Remove from Favourites — the plugin's right-hand panel."""

    def __init__(self, session, title):
        SideMenu.__init__(self, session, title,
                          [("Watch", "watch"),
                           ("Remove from Favourites", "unfavourite")],
                          value_only=True)

class _PanelList(GUIComponent):
    """A plain list the screen draws itself (no selection bar of the
    box's own) - used for both panels."""

    GUI_WIDGET = eListbox

    def __init__(self, item_h):
        GUIComponent.__init__(self)
        self.l = eListboxPythonMultiContent()
        self.l.setItemHeight(item_h)
        self.l.setFont(0, gFont("E2iRegular", 30))
        self.l.setFont(1, gFont("E2iRegular", 26))

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setWrapAround(False)
        instance.setSelectionEnable(False)

    def preWidgetRemove(self, instance):
        pass

    def setList(self, rows):
        self.l.setList(rows)


# Layout (1920x1080) - the same as the Lab Recordings and Music screens
_C_BG, _C_PANEL, _C_TEXT, _C_DIM, _C_ACCENT = (
    "E2iBg", "E2iPanel", "#ffffff", "E2iDim", "E2iHi")
_P_PANEL, _P_SEL, _P_TEXT, _P_DIM, _P_ACCENT = (
    _T.PANEL, _T.HI, 0xffffff, _T.DIM, _T.HI)
_L_X, _L_W = 40, 600
_R_X, _R_W = 670, 1210
_P_Y, _P_H = 100, 940
_PL_ROW_H, _PL_ROWS = 62, 3
_PL_Y = _P_Y + 16
_F_ROW_H, _F_ROWS = 66, 13
_F_Y = _P_Y + 16
_INFO_Y = _PL_Y + _PL_ROWS * _PL_ROW_H + 30
_POST_W, _POST_H = 210, 280
POSTER_CACHE = DATA + "/cache/series_posters"   # shared with Movies/Series


def _row(w, h, cells, sel):
    """One list row: background, rounded highlight when selected, then
    the text cells [(x, width, font, text, colour, align)]."""
    bg = _P_SEL if sel else _P_PANEL
    out = [None, MultiContentEntryText(
        pos=(0, 0), size=(w, h), font=0, text="",
        flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
        backcolor=_P_PANEL, backcolor_sel=_P_PANEL)]
    if sel:
        out.append(MultiContentEntryText(
            pos=(4, 3), size=(w - 8, h - 6), font=0, text="",
            flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
            backcolor=bg, backcolor_sel=bg, cornerRadius=14))
    for x, cw, font, text, col, align in cells:
        col = 0x000000 if sel else col
        out.append(MultiContentEntryText(
            pos=(x, 0), size=(cw, h), font=font, text=text,
            flags=align | RT_VALIGN_CENTER,
            color=col, color_sel=col, backcolor=bg, backcolor_sel=bg))
    return out


def _poster_path(url):
    import hashlib
    try:
        return os.path.join(POSTER_CACHE,
                            hashlib.md5(url.encode("utf-8")).hexdigest() + ".jpg")
    except Exception:
        return None


class FavouritesListScreen(Screen):
    """Lab: two panels, as the Recordings and Music screens.

    Left: All / Movies / Series, and under them the poster and details
    of the highlighted favourite. Right: the favourites, newest first.

    OK on a favourite does what it always did: a movie offers Watch /
    Remove from Favourites, a series opens it as normal browsing. The
    details are looked up in the background (from the lists the plugin
    already keeps on disk) and remembered, so it's quick next time.
    Left/Right swap panels; Left on the left panel, or Exit, opens the
    rail."""

    def __init__(self, session):
        Screen.__init__(self, session)

        self._all = FavouritesStore().all()
        self._filter = "all"
        self._entries = list(self._all)
        self._sel = 0
        self._scroll = 0
        self._focus = "files"
        self._pl_sel = 0
        self._resolving = False
        self._resolve_result = None
        self._fill_results = []        # [(entry key, details)] from the background
        self._fill_running = False
        self._poster_want = 0          # url wanted on screen (0 = nothing yet)
        self._poster_done = None       # (url, path) from the background

        self.skin = """
<screen name="E2iLabFavourites" position="0,0" size="1920,1080" flags="wfNoBorder" backgroundColor="{bg}">
  <eLabel position="0,0" size="1920,1080" backgroundColor="{bg}" zPosition="0"/>
  <widget name="header" position="60,30" size="1400,50" font="E2iRegular;34" foregroundColor="{text}" backgroundColor="{bg}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="clock"  position="1560,30" size="320,50" font="E2iRegular;34" foregroundColor="{dim}" backgroundColor="{bg}" halign="right" valign="center" transparent="1" zPosition="2"/>
  <widget name="l_bg" position="{lx},{py}" size="{lw},{ph}" backgroundColor="{panel}" cornerRadius="18" zPosition="1"/>
  <widget name="r_bg" position="{rx},{py}" size="{rw},{ph}" backgroundColor="{panel}" cornerRadius="18" zPosition="1"/>
  <widget name="places" position="{lx2},{ply}" size="{lw2},{plh}" backgroundColor="{panel}" scrollbarMode="showNever" transparent="1" zPosition="2"/>
  <eLabel position="{lx3},{sepy}" size="{lw3},2" backgroundColor="E2iSep" zPosition="2"/>
  <eLabel position="{lx3},{iy}" size="{pw},{ph2}" backgroundColor="E2iPanel2" zPosition="2"/>
  <widget name="noposter" position="{lx3},{iy}" size="{pw},{ph2}" font="E2iRegular;30" foregroundColor="E2iMuted" backgroundColor="E2iPanel2" halign="center" valign="center" transparent="1" zPosition="3"/>
  <widget name="poster" position="{lx3},{iy}" size="{pw},{ph2}" scale="1" transparent="1" zPosition="4"/>
  <widget name="i_title" position="{tx},{iy}" size="{tw},126" font="E2iRegular;32" foregroundColor="{text}" backgroundColor="{panel}" halign="left" valign="top" transparent="1" zPosition="2"/>
  <widget name="i_year"  position="{tx},{iy1}" size="{tw},36" font="E2iRegular;26" foregroundColor="{accent}" backgroundColor="{panel}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="i_genre" position="{tx},{iy2}" size="{tw},70" font="E2iRegular;24" foregroundColor="{dim}" backgroundColor="{panel}" halign="left" valign="top" transparent="1" zPosition="2"/>
  <widget name="i_where" position="{tx},{iy3}" size="{tw},36" font="E2iRegular;24" foregroundColor="{dim}" backgroundColor="{panel}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="i_plot"  position="{lx3},{iy4}" size="{lw3},{plot_h}" font="E2iRegular;26" foregroundColor="{text}" backgroundColor="{panel}" halign="left" valign="top" transparent="1" zPosition="2"/>
  <widget name="list" position="{rx2},{fy}" size="{rw2},{fh}" backgroundColor="{panel}" scrollbarMode="showNever" transparent="1" zPosition="2"/>
  <widget name="status" position="{rx3},{sy}" size="{sw},34" font="E2iRegular;26" foregroundColor="#f5c542" backgroundColor="{panel}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="count"  position="{cx},{sy}" size="260,34" font="E2iRegular;24" foregroundColor="{dim}" backgroundColor="{panel}" halign="right" valign="center" transparent="1" zPosition="2"/>
</screen>""".format(
            bg=_C_BG, text=_C_TEXT, dim=_C_DIM, panel=_C_PANEL, accent=_C_ACCENT,
            lx=_L_X, lw=_L_W, rx=_R_X, rw=_R_W, py=_P_Y, ph=_P_H,
            lx2=_L_X + 10, lw2=_L_W - 20, ply=_PL_Y, plh=_PL_ROWS * _PL_ROW_H,
            lx3=_L_X + 24, lw3=_L_W - 48, sepy=_INFO_Y - 14,
            pw=_POST_W, ph2=_POST_H, iy=_INFO_Y,
            tx=_L_X + 24 + _POST_W + 20, tw=_L_W - 48 - _POST_W - 20,
            iy1=_INFO_Y + 132, iy2=_INFO_Y + 172, iy3=_INFO_Y + 244,
            iy4=_INFO_Y + _POST_H + 20,
            plot_h=_P_Y + _P_H - (_INFO_Y + _POST_H + 20) - 20,
            rx2=_R_X + 10, rw2=_R_W - 20, fy=_F_Y, fh=_F_ROWS * _F_ROW_H,
            rx3=_R_X + 34, sw=_R_W - 340, cx=_R_X + _R_W - 294,
            sy=_P_Y + _P_H - 48)

        self["header"] = Label("Favourites")
        self["clock"] = Label("")
        self["l_bg"] = Label("")
        self["r_bg"] = Label("")
        self["places"] = _PanelList(_PL_ROW_H)
        self["list"] = _PanelList(_F_ROW_H)
        self["status"] = Label("")
        self["count"] = Label("")
        self["poster"] = Pixmap()
        self["noposter"] = Label("")
        for n in ("i_title", "i_year", "i_genre", "i_where", "i_plot"):
            self[n] = Label("")

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self._activate_selected,
                "cancel": self._on_cancel,
                "up":     lambda: self._move(-1),
                "down":   lambda: self._move(1),
                "left":   self._on_left,
                "right":  self._on_right,
            }, -1)
        self["pages"] = ActionMap(["ChannelSelectBaseActions"], {
            "nextBouquet": lambda: self._move(-self._page()),
            "prevBouquet": lambda: self._move(self._page()),
        }, -1)

        self._resolve_timer = eTimer()
        self._resolve_timer.callback.append(self._poll_resolve)
        self._tick_timer = eTimer()
        self._tick_timer.callback.append(self._tick)
        self.onLayoutFinish.append(self._first_show)
        self.onClose.append(self._tick_timer.stop)

    # -- start ------------------------------------------------------------------

    def _first_show(self):
        try:
            self["poster"].hide()
        except Exception:
            pass
        self._clock_text()
        self._paint()
        self._tick_timer.start(200, False)
        self._fill_start()

    def _clock_text(self):
        try:
            self["clock"].setText(time.strftime("%a %d %b  %H:%M"))
        except Exception:
            pass

    def _tick(self):
        if int(time.time()) % 15 == 0:
            self._clock_text()
        # Details found in the background: save them (here, never in the
        # background, so a removal can't be overwritten) and show them.
        if self._fill_results:
            results = self._fill_results[:]
            del self._fill_results[:len(results)]
            store = FavouritesStore()
            for key, details in results:
                store.set_details(key[0], key[1], key[2], details)
                for e in self._all:
                    if self._key(e) == key:
                        e.update(details)
            self._show_info()
        done, self._poster_done = self._poster_done, None
        if done and done[0] == self._poster_want:
            self._poster_show(done[1])

    @staticmethod
    def _key(e):
        return (e.get("content_type"), e.get("provider_key"), e.get("item_id"))

    # -- looking up details the first time ------------------------------------

    def _fill_start(self):
        todo = [dict(e) for e in self._all if not e.get("details")]
        if not todo or self._fill_running:
            return
        self._fill_running = True

        def worker():
            for e in todo:
                try:
                    provider, item = _resolve_favourite(e)
                    if item is None:
                        continue
                    if e.get("content_type") == "movie":
                        try:
                            state.backend.get_movie_info(provider, item)
                        except Exception:
                            pass
                    self._fill_results.append((self._key(e), item_details(item)))
                except Exception as ex:
                    _debug_log("Favourites details '%s': %s" % (e.get("name", ""), ex))
            self._fill_running = False
        t = threading.Thread(target=worker, name="E2iPTV-Favourites-Details")
        t.daemon = True
        t.start()

    # -- drawing ----------------------------------------------------------------

    def _places(self):
        n_m = sum(1 for e in self._all if e.get("content_type") == "movie")
        n_s = sum(1 for e in self._all if e.get("content_type") == "series")
        return [("all", "All (%d)" % len(self._all)),
                ("movie", "Movies (%d)" % n_m),
                ("series", "Series (%d)" % n_s)]

    def _provider_names(self):
        return {str(getattr(p, "provider_key", None)): getattr(p, "name", "") or ""
                for _name, p in (state.providers or [])}

    def _paint(self):
        # left
        w = _L_W - 20
        rows = []
        for i, (key, text) in enumerate(self._places()):
            sel = self._focus == "places" and i == self._pl_sel
            col = _P_ACCENT if key == self._filter else _P_TEXT
            rows.append(_row(w, _PL_ROW_H,
                             [(24, w - 48, 0, text, col, RT_HALIGN_LEFT)], sel))
        self["places"].setList(rows)
        # right
        w = _R_W - 20
        n = len(self._entries)
        self._sel = max(0, min(self._sel, n - 1)) if n else 0
        if self._sel < self._scroll:
            self._scroll = self._sel
        elif self._sel >= self._scroll + _F_ROWS:
            self._scroll = self._sel - _F_ROWS + 1
        self._scroll = max(0, min(self._scroll, max(0, n - _F_ROWS)))
        names = self._provider_names()
        rows = []
        for i in range(self._scroll, min(n, self._scroll + _F_ROWS)):
            e = self._entries[i]
            sel = self._focus == "files" and i == self._sel
            kind = "Movie" if e.get("content_type") == "movie" else "Series"
            right = "%s   ·   %s" % (kind, names.get(str(e.get("provider_key")), "?"))
            rows.append(_row(w, _F_ROW_H, [
                (24, w - 48 - 400, 0, e.get("name", ""), _P_TEXT, RT_HALIGN_LEFT),
                (w - 24 - 380, 380, 1, right, _P_DIM, RT_HALIGN_RIGHT)], sel))
        self["list"].setList(rows)
        self["count"].setText("%d of %d" % (self._sel + 1, n) if n > _F_ROWS else "")
        if not self._all:
            self["status"].setText("No favourites yet - add some from Movies or Series")
        elif not n:
            self["status"].setText("None here")
        elif not self._resolving:
            self["status"].setText("")
        self._show_info()

    def _show_info(self):
        e = None
        if self._focus == "files" and self._entries:
            e = self._entries[self._sel]
        if e is None:
            key, text = self._places()[self._pl_sel]
            self._info(text.split(" (")[0], "", "", "",
                       "OK to show these favourites." if self._all else "", None)
            return
        bits = [str(e.get(k) or "") for k in ("year",) if e.get(k)]
        if not bits and e.get("release_date"):
            bits.append(str(e["release_date"])[:4])
        rating = str(e.get("rating") or "").strip()
        if rating and rating not in ("0", "0.0"):
            bits.append("★ " + rating)
        where = "%s   ·   %s" % (
            "Movie" if e.get("content_type") == "movie" else "Series",
            self._provider_names().get(str(e.get("provider_key")), "?"))
        plot = e.get("plot") or ("" if e.get("details") else "Looking up the details...")
        self._info(e.get("name", ""), "   ·   ".join(bits),
                   e.get("genre") or "", where, plot, e.get("cover") or None)

    def _info(self, title, year, genre, where, plot, cover):
        for n, v in (("i_title", title), ("i_year", year), ("i_genre", genre),
                     ("i_where", where), ("i_plot", plot)):
            self[n].setText(v or "")
        if cover == self._poster_want:
            return
        self._poster_want = cover
        self["poster"].hide()
        self["noposter"].setText("" if cover else "No poster")
        if not cover:
            return
        path = _poster_path(cover)
        if path and os.path.exists(path):
            self._poster_show(path)
            return

        def worker(url=cover, path=path):
            try:
                import urllib.request
                if not os.path.isdir(POSTER_CACHE):
                    os.makedirs(POSTER_CACHE)
                data = urllib.request.urlopen(url, timeout=5).read()
                with open(path + ".download", "wb") as f:
                    f.write(data)
                os.rename(path + ".download", path)
                self._poster_done = (url, path)
            except Exception:
                try:
                    os.remove(path + ".download")
                except Exception:
                    pass
        t = threading.Thread(target=worker, name="E2iPTV-Favourites-Poster")
        t.daemon = True
        t.start()

    def _poster_show(self, path):
        try:
            try:
                pm = LoadPixmap(path, cached=False,
                                desired_width=_POST_W, desired_height=_POST_H)
            except TypeError:
                pm = LoadPixmap(path, cached=False)
            if pm:
                # Stretch it to the frame, whatever size the picture is
                # (as the Series poster does).
                try:
                    self["poster"].instance.setScale(1)
                except Exception:
                    pass
                self["poster"].instance.setPixmap(pm)
                self["poster"].show()
                self["noposter"].setText("")
        except Exception:
            pass

    # -- keys ---------------------------------------------------------------

    def _page(self):
        return _PL_ROWS if self._focus == "places" else _F_ROWS

    def _move(self, d):
        if self._focus == "places":
            self._pl_sel = max(0, min(_PL_ROWS - 1, self._pl_sel + d))
        elif self._entries:
            self._sel = max(0, min(len(self._entries) - 1, self._sel + d))
        self._paint()

    def _on_left(self):
        if self._focus == "files":
            self._focus = "places"
            self._paint()
            return
        self._open_rail()

    def _on_right(self):
        if self._focus == "places":
            self._focus = "files"
            self._paint()

    def _on_cancel(self):
        self._open_rail()

    def _open_rail(self):
        from .lab_rail import open_rail
        open_rail(self, "Favourites")

    def _set_filter(self, key):
        self._filter = key
        self._entries = [e for e in self._all
                         if key == "all" or e.get("content_type") == key]
        self._sel = 0
        self._scroll = 0

    def _handle_child_close(self, result=None):
        """Back from the rail: a "goto:<action>" goes on up to Screen1,
        which acts on it; a plain close leaves this screen as it was."""
        if not result or not str(result).startswith("goto:"):
            return
        if result == "goto:favourites":
            return
        self.close(result)

    def get_mirror_b2(self):
        return [(e.get("name", ""), None, False) for e in self._entries], self._sel

    get_mirror_b2_clamped = True

    def get_mirror_b3(self):
        return [], 0, 0

    # -- OK: the same as before -------------------------------------------------

    def _activate_selected(self):
        if self._focus == "places":
            self._set_filter(self._places()[self._pl_sel][0])
            self._focus = "files"
            self._paint()
            return

        if self._resolving:
            return  # already working on the previous OK press

        if not self._entries or self._sel >= len(self._entries):
            return

        entry = self._entries[self._sel]

        self._resolving = True
        self["status"].setText("Opening '%s'..." % entry.get("name", ""))

        def worker():
            try:
                self._resolve_result = _resolve_favourite(entry)
            except Exception as e:
                _debug_log("Favourites resolve crashed: %s" % (e,))
                self._resolve_result = (None, None)

        thread = threading.Thread(target=worker, name="E2iPTV-Favourites-Resolve")
        thread.daemon = True
        thread.start()

        self._resolve_timer.start(150, False)

    def _poll_resolve(self):

        if self._resolve_result is None:
            return

        self._resolve_timer.stop()
        result = self._resolve_result
        self._resolve_result = None
        self._resolving = False
        self["status"].setText("")

        provider, item = result
        entry = self._entries[self._sel]

        if provider is None or item is None:
            self["status"].setText("'%s' is no longer available on this provider"
                                   % entry.get("name", ""))
            # Offer to take it off the list rather than leave it there.
            self.session.openWithCallback(
                lambda choice: self._menu_choice(choice, entry, None, None),
                SideMenu, entry.get("name", ""),
                [("Remove from Favourites", "unfavourite")],
                detail="No longer available on this provider",
                value_only=True)
            return

        self.session.openWithCallback(
            lambda choice: self._menu_choice(choice, entry, provider, item),
            _FavouriteChoiceMenu,
            entry.get("name", "")
        )

    def _menu_choice(self, choice, entry, provider, item):

        content_type = entry.get("content_type")

        if choice == "watch":
            if content_type == "movie":
                play_movie(self.session, provider, item)
            else:
                # Series - handed up to Screen1, which opens it (see
                # this module's notes on why it isn't opened from here).
                self.close(("activate", "series", provider, item))
            return

        if choice != "unfavourite":
            return  # Cancelled the menu - stay right where we were.

        try:
            FavouritesStore().remove(
                content_type,
                entry.get("provider_key"),
                entry.get("item_id")
            )
        except Exception as e:
            print("[E2iPTV] Favourites remove failed:", e)
            return

        self._all = FavouritesStore().all()
        sel = self._sel
        self._set_filter(self._filter)
        self._sel = min(sel, max(0, len(self._entries) - 1))
        self["status"].setText("")
        self._paint()


def open_favourites(session, on_close_callback):
    """Entry point — call this from B1's "favourites" action.
    on_close_callback is what finally acts on an ("activate", ...)
    result once it bubbles back up — same reasoning as search.py's
    open_search."""
    session.openWithCallback(on_close_callback, FavouritesListScreen)
