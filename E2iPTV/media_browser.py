# =============================================================================
# E2iPTV — Series
# Series category screen using the common B1 icon rail.
#
# Piece 1:
#   - Common B1 rail
#   - Series B2 category display
#   - < from B2 expands B1
#   - > from B1 collapses B1
#   - selecting Series while already in Series collapses B1
#
# No Live TV code is touched here.
# =============================================================================

from .paths import HDD, LOG  # data folder / debug logs
from .paths import DATA  # E2iPTV_Data folder
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.GUIComponent import GUIComponent
from Components.MultiContent import (
    MultiContentEntryText,
    MultiContentEntryPixmapAlphaBlend
)
from Tools.LoadPixmap import LoadPixmap
import threading
import os
import hashlib
import time
try:
    import urllib2
except ImportError:
    import urllib.request as urllib2

from enigma import (
    ePoint,
    eTimer,
    eListbox,
    eListboxPythonMultiContent,
    gFont,
    gRGB,
    RT_HALIGN_LEFT,
    RT_VALIGN_CENTER,
    RT_HALIGN_CENTER,
    BT_SCALE,
    BT_KEEP_ASPECT_RATIO
)

from .state import state
from .favourites import FavouritesStore
from .b1common import B1Common, B1_RAIL_COLOUR
from .b2common import B2Common


_DEBUG_LOG_PATH = LOG("e2iptv_jump_debug.log")


def _debug_log(msg):
    """Writes to a plain file rather than print() — this plugin's own
    output, nothing about the box touched to read it back. Just cat
    the file afterward: `cat /tmp/e2iptv_jump_debug.log`. Same pattern
    as favourites.py's own _debug_log."""
    try:
        with open(_DEBUG_LOG_PATH, "a") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except Exception:
        pass


from .side_menu import SideMenu


class FavouriteChoiceMenu(SideMenu):
    """Watch / Download / Favourites options — the plugin's right-hand
    panel (side_menu.py). Closes with the chosen value, None on Exit."""

    def __init__(self, session, title, already_favourite=False, is_movie=False):
        fav = (("Remove from Favourites", "unfavourite") if already_favourite
               else ("Add to Favourites", "favourite"))
        opts = [("Watch", "watch")]
        if is_movie:
            opts.append(("Download", "download"))
        opts.append(fav)
        SideMenu.__init__(self, session, title, opts, value_only=True)


B1_COLLAPSED_W = 100
B1_EXPANDED_W  = 300

B2_X       = 100
B2_W       = 460
B2_Y       = 85
B2_ITEM_H  = 66
B2_TILE_ROW = 7
B2_ROW_OFFSET = 2
B2_H        = 941
B2_VISIBLE  = (B2_H - (B2_H % B2_ITEM_H)) // B2_ITEM_H - 1
B2_TOTAL_ROWS = B2_VISIBLE + 4

BG_COLOUR   = "0a4aa8"

TILE_COLOUR = "d0e8ff"

# B3 Series browser.
#
# B2's rows don't start at a fixed top position — the current
# selection is always pinned to a fixed screen row (the "tile" row,
# B2_TILE_ROW), with everything else scrolling around it. That fixed
# tile position is Y=547 (B2_Y + B2_TILE_ROW*B2_ITEM_H) — this is
# the real, stable reference point, not B2_Y itself.
#
# B3's list starts 2 rows above the tile position (matches Provider
# sitting 1 row above the tile when a category is selected, and B3's
# heading sitting 1 row above Provider).

B2_TILE_Y   = B2_Y + B2_TILE_ROW * B2_ITEM_H

B3_X        = 585
B3_W        = 620
B3_ITEM_H   = B2_ITEM_H
B3_VISIBLE_ROWS = 10

B3_LIST_TOP = B2_TILE_Y - (2 * B3_ITEM_H)
B3_Y        = B3_LIST_TOP - B3_ITEM_H
B3_H        = (B3_VISIBLE_ROWS + 1) * B3_ITEM_H
B3_LIST_BOTTOM = B3_LIST_TOP + (B3_VISIBLE_ROWS * B3_ITEM_H)

# Permanent poster cache.
SERIES_POSTER_CACHE = DATA + "/cache/series_posters"

# Poster — beside the episode list, top aligned with its first row.
POSTER_W = 360
POSTER_H = 480
POSTER_X = 1920 - POSTER_W - 40
POSTER_Y = B3_LIST_TOP

# Info panel — full width, near the top of the screen. With the new
# anchor there's ample room above the heading again, so this is back
# to its original size and position.
INFO_X = B3_X
INFO_Y = 40
INFO_W = (1920 - 40) - INFO_X
INFO_TITLE_H = 45
INFO_TEXT_Y  = INFO_Y + INFO_TITLE_H + 10
INFO_TEXT_H  = (B3_Y - 20) - INFO_TEXT_Y

# Season bar — shares the heading row, one full row tall like every
# other row in the shared grid.
SEASON_BAR_X = B3_X + 320
SEASON_BAR_Y = B3_Y
SEASON_BAR_W = (1920 - 40) - SEASON_BAR_X
SEASON_BAR_H = B3_ITEM_H
SEASON_SEG_W = 220

# Series-name heading width — stops short of the season bar (now
# opaque) so a long series name can never be painted over.
SERIES_NAME_W = SEASON_BAR_X - B3_X - 20

# Episode list — narrower again, poster sits to its right like it
# originally did beside the Series list. Same start Y and item
# height as B3 above, so both share the identical row grid.
B4_X = B3_X
B4_Y = B3_LIST_TOP
B4_W = POSTER_X - 30 - B4_X
B4_VISIBLE_ROWS = B3_VISIBLE_ROWS
B4_H = B4_VISIBLE_ROWS * B3_ITEM_H



class SeriesList(GUIComponent):

    GUI_WIDGET = eListbox

    def __init__(self, item_h=66):
        GUIComponent.__init__(self)

        self.l = eListboxPythonMultiContent()
        self.l.setFont(0, gFont("E2iRegular", 28))
        self.l.setItemHeight(item_h)
        self._entries = []

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setWrapAround(False)
        instance.setSelectionEnable(False)

    def preWidgetRemove(self, instance):
        pass

    def setList(self, entries):
        self._entries = entries
        self.l.setList(entries)

    def moveToIndex(self, idx):
        if self.instance and self._entries:
            idx = min(
                max(0, idx),
                len(self._entries) - 1
            )
            self.instance.moveSelectionTo(idx)


def _visible_window(sel_index, total_count, visible_count):
    """Returns the start index of a window of visible_count items that
    always contains sel_index, clamped to valid range. Used to render
    only the currently-visible slice of a list/bar ourselves, rather
    than relying on the native listbox to scroll to an arbitrary
    selection — which has not reliably kept the selection in view."""

    if total_count <= visible_count:
        return 0

    return max(0, min(sel_index, total_count - visible_count))


class SeasonBar(GUIComponent):
    """A horizontally-scrolling season selector. Enigma2's eListbox has
    no native horizontal mode, so this renders as a single list row
    containing one MultiContentEntryText per season, laid out left to
    right; the "selected" segment is redrawn with the highlight
    colour on every setSeasons() call rather than using the listbox's
    own native row selection (which only moves vertically)."""

    GUI_WIDGET = eListbox

    def __init__(self, item_h=SEASON_BAR_H):
        GUIComponent.__init__(self)

        self.l = eListboxPythonMultiContent()
        self.l.setFont(0, gFont("E2iRegular", 24))
        self.l.setItemHeight(item_h)

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setWrapAround(False)
        instance.setSelectionEnable(False)

    def preWidgetRemove(self, instance):
        pass

    def setSeasons(self, names, sel_index):

        entries = [None]
        n = len(names)

        if n == 0:
            self.l.setList([entries])
            return

        # If everything fits at the default pill width, stretch pills
        # evenly to fill the bar instead of leaving dead space (and
        # nothing to scroll). Otherwise keep the default width and
        # show only a window of pills that always contains the
        # selected one — positions are always relative to the start
        # of that window (never a raw index minus a running offset),
        # so a wraparound back to the first season always draws
        # flush at x=0 rather than inheriting a stale scroll amount.
        if n * SEASON_SEG_W <= SEASON_BAR_W:
            seg_w = SEASON_BAR_W // n
            start = 0
            visible_count = n
        else:
            seg_w = SEASON_SEG_W
            visible_count = max(1, SEASON_BAR_W // seg_w)
            start = _visible_window(sel_index, n, visible_count)

        for local_i, i in enumerate(
            range(start, min(start + visible_count, n))
        ):

            name = names[i]
            selected = (i == sel_index)

            bg = 0xd0e8ff if selected else 0x0a4aa8
            fg = 0x000000 if selected else 0xffffff

            entries.append(
                MultiContentEntryText(
                    pos=(local_i * seg_w, 4),
                    size=(seg_w - 4, SEASON_BAR_H - 14),
                    font=0,
                    flags=RT_HALIGN_CENTER | RT_VALIGN_CENTER,
                    text=str(name),
                    color=fg,
                    color_sel=fg,
                    backcolor=bg,
                    backcolor_sel=bg
                )
            )

        self.l.setList([entries])


class MediaB3Item(object):
    """Wraps one B3 row's underlying object — a Series or a Movie.
    Kept as the single generic wrapper for both, since Series and
    Movie share the same field names for everything the rest of this
    screen reads (name/plot/genre/cast/rating) except the poster
    image (cover vs stream_icon), which gets aliased onto .cover at
    load time so nothing downstream needs to know which type it is.
    The attribute is still called .series for historical reasons —
    it holds a Movie object just as often as a Series one."""

    def __init__(self, series):
        self.series = series
        self.info = None
        self.seasons = []
        self.season_selected = None


class MediaBrowser(B1Common, B2Common, Screen):

    def __init__(self, session, content_type="series",
                 jump_to_series=None, jump_to_provider=None):
        Screen.__init__(self, session)

        # "series" or "movies" — drives B2's category source (via
        # _b2_content below) and whether OK on a B3 item drills into
        # seasons/episodes (series) or plays directly (movies).
        self._content_type = content_type

        # Set by Search (see search.py) to open straight into a
        # specific series' episodes rather than the normal Provider ->
        # Category -> Series browse — the normal B1/B2/_load() flow
        # still runs underneath exactly as it would without these
        # (harmless — the user is already looking at the episodes
        # view by the time it finishes), this just gets there first.
        self._jump_to_series = jump_to_series
        self._jump_to_provider = jump_to_provider
        # Stays True for this screen's whole lifetime once set — unlike
        # _jump_to_series/_jump_to_provider above, which
        # _do_jump_to_series clears once it's run. Used purely to keep
        # _load() from ever doing its own final render (see _load()'s
        # last line) when a jump was requested, including on a late-
        # firing provider-load retry well after _do_jump_to_series
        # already completed and rendered the actual jump target —
        # checking _jump_to_series there instead would already read
        # None by that point and let the stale render through.
        self._had_jump_target = jump_to_series is not None
        # Set once _do_jump_to_series has resolved which category the
        # target series actually lives in and kicked off the real fetch
        # for it — _finish_b3_series_items (shared with the normal
        # OK-driven B3 load path) checks this once that fetch completes
        # to find and open the right item within the real list, then
        # clears it either way.
        self._pending_jump_series_id = None

        # --------------------------------------------------------------
        # B1
        # --------------------------------------------------------------

        self._b1_init(expanded=False)

        # --------------------------------------------------------------
        # B2
        # --------------------------------------------------------------

        self._b2_content = content_type
        self._b2_cat_indent = "   "

        self._b2_tile_row   = B2_TILE_ROW
        self._b2_row_offset = B2_ROW_OFFSET
        self._b2_visible    = B2_VISIBLE
        self._b2_total_rows = B2_TOTAL_ROWS

        self._b2_flat      = []
        self._b2_open_set  = set()
        self._b2_open_cats = {}
        self._b2_sel       = 0

        # --------------------------------------------------------------
        # Series / Seasons / Episodes
        # --------------------------------------------------------------

        self._series_items = []
        self._series_sel = 0
        self._b3_category_cache = {}

        # B3 loading is deliberately done only after B2 OK.
        # The request itself runs away from the Enigma2 GUI thread so
        # B2 navigation remains responsive while Xtream is answering.
        self._b3_load_id = 0
        self._b3_loading = False
        self._b3_result = None
        self._b3_thread = None
        self._b3_timer = eTimer()
        self._b3_timer.callback.append(self._b3_poll)

        self._series_provider_idx = None
        self._series_provider = None
        self._series_category = None

        self._series_info = None
        self._season_sel = 0

        self._episodes = []
        self._episode_sel = 0

        # "series"   = B3 contains the browsable Series list
        # "episodes" = a Series is selected; season bar + Episode
        #              list (B4) are shown, sharing the row/area
        #              below the series-name heading
        self._content_mode = "series"

        # Focus stack while _content_mode == "episodes":
        # False = season bar has focus (Right cycles seasons,
        #         wrapping; episode list updates live underneath)
        # True  = episode list has focus (Up/Down browse episodes,
        #         OK plays)
        self._episode_focus = False

        # B2/B3 focus.
        # B2 is the normal starting focus.  Once a Series category
        # has been opened, B3 becomes the active navigation area.
        self._b3_focus = False

        self._build_skin()

        self._b1_register_widgets()

        self["b2_bg"] = Label("")
        self["b2_sep"] = Label("")

        for i in range(B2_TOTAL_ROWS):
            self["b2_row%d" % i] = Label("")

        self["title"] = Label("Series")
        self["b2_tile"] = Label("")

        self["b3_list"] = SeriesList(B3_ITEM_H)
        self["b4_list"] = SeriesList(B3_ITEM_H)
        self["season_bar"] = SeasonBar()

        self["series_poster"] = Pixmap()

        self._poster_generation = 0
        self._poster_loading = False
        self._poster_result = None
        self._poster_thread = None
        self._poster_timer = eTimer()
        self._poster_timer.callback.append(self._poster_poll)

        # Lazy fetch for movie plot/cast/genre — Xtream's basic movie
        # list doesn't include it (see MovieInfoReader's docstring for
        # why). Same background-thread + polling-timer pattern as the
        # poster loader just above, fetching detail fields instead of
        # an image. Movies only — Series gets this for free in its own
        # list call already.
        self._movie_info_generation = 0
        self._movie_info_loading = False
        self._movie_info_result = None
        self._movie_info_timer = eTimer()
        self._movie_info_timer.callback.append(self._movie_info_poll)

        # Same background-thread + polling-timer pattern again, this
        # time for the get_series_info fetch _series_ok() needs the
        # first time a given Series is opened this screen session
        # (item.info is None). This used to be a direct, blocking call
        # right inside _series_ok() — fine-looking from a normal
        # keypress (just a brief freeze), but when _series_ok() runs
        # from an already-async context instead (Search/Favourites'
        # jump-to-series path, whenever the category wasn't already
        # cached), blocking the reactor thread inside that callback
        # left the episodes screen showing but not actually painted
        # with its content until some unrelated later event (a
        # keypress) forced Enigma2 to redraw — this is what fixes that.
        self._series_info_result = None
        self._series_info_timer = eTimer()
        self._series_info_timer.callback.append(self._series_info_poll)

        try:
            if not os.path.isdir(SERIES_POSTER_CACHE):
                os.makedirs(SERIES_POSTER_CACHE)
        except Exception:
            pass

        self["b3_title"] = Label("")
        self["b4_title"] = Label("")

        self["info_title"] = Label("")
        self["info_text"] = Label("")

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self._on_ok,
                "cancel": self._on_cancel,
                "up":     self._on_up,
                "down":   self._on_down,
                "left":   self._on_left,
                "right":  self._on_right,
            },
            -1
        )
        # 5 twice quickly: show/hide Private categories (backend/private.py)
        self["private_actions"] = ActionMap(
            ["NumberActions"], {"5": self._private_key}, -1)

        # Both run, in order — _load() to populate B2 (the provider/
        # category rail) same as any normal open, _do_jump_to_series()
        # afterwards to land on the specific series. _load() itself is
        # guarded (see its own final self._refresh_content() call) so
        # it never re-renders over a jump that's already happened —
        # that guard is the actual fix; skipping _load() outright here
        # was the wrong fix and left B2 permanently empty, since
        # _load() is the only thing that ever populates it.
        self.onLayoutFinish.append(self._load)
        self.onExecBegin.append(self._goto_check)
        if self._jump_to_series is not None:
            self.onLayoutFinish.append(self._do_jump_to_series)
        self.onClose.append(self._stop_b3_loader)
        self.onClose.append(self._stop_poster_loader)
        self.onClose.append(self._stop_load_retry_timer)
        self.onClose.append(self._stop_series_info_loader)

    # ==================================================================
    # SKIN
    # ==================================================================

    def _build_skin(self):

        b2_xml = ""

        for i in range(B2_TOTAL_ROWS):

            if i - B2_ROW_OFFSET == B2_TILE_ROW:
                continue

            ry = B2_Y + ((i - B2_ROW_OFFSET) * B2_ITEM_H)

            b2_xml += (
                '<widget name="b2_row%d" position="%d,%d" size="%d,%d"'
                ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
                ' backgroundColor="#%s" valign="center" noWrap="1"'
                ' zPosition="3"/>'
                % (
                    i,
                    B2_X + 5,
                    ry,
                    B2_W - 10,
                    B2_ITEM_H,
                    BG_COLOUR
                )
            )

        tile_y = B2_Y + B2_TILE_ROW * B2_ITEM_H

        self.skin = (
            '<screen name="E2iPTV_Series" position="0,0"'
            ' size="1920,1080" flags="wfNoBorder">'

            # ----------------------------------------------------------
            # Base screen.
            # ----------------------------------------------------------

            '<eLabel position="0,0" size="1920,1080"'
            ' backgroundColor="#0a4aa8" zPosition="0"/>'

            # ----------------------------------------------------------
            # B1 rail.
            # ----------------------------------------------------------

            '<eLabel position="0,0" size="%d,1080"'
            ' backgroundColor="#%s" zPosition="1"/>'
            % (
                B1_EXPANDED_W,
                B1_RAIL_COLOUR
            ) +

            # ----------------------------------------------------------
            # B2.
            # ----------------------------------------------------------

            '<widget name="b2_bg" position="%d,0"'
            ' size="%d,1080" backgroundColor="#%s"'
            ' zPosition="2"/>'
            % (
                B2_X,
                B2_W,
                BG_COLOUR
            ) +

            '<widget name="b2_sep" position="%d,0"'
            ' size="5,1080" backgroundColor="#0a4aa8"'
            ' zPosition="3"/>'
            % (
                B2_X + B2_W
            ) +

            self._b1_skin_xml() +

            b2_xml +

            '<widget name="b2_tile" position="%d,%d"'
            ' size="%d,%d" font="E2iRegular;28"'
            ' foregroundColor="#000000" backgroundColor="#%s"'
            ' valign="center" noWrap="1" zPosition="4"/>'
            % (
                B2_X + 5,
                tile_y,
                B2_W - 10,
                B2_ITEM_H,
                TILE_COLOUR
            ) +

            # ----------------------------------------------------------
            # Information area — from B2's edge to the poster, in the
            # band above the series-name row. Live-updates as the
            # Series list is browsed, same as the poster beside it.
            #
            # IMPORTANT:
            # This is transparent.  No opaque panel is placed behind it.
            # ----------------------------------------------------------

            '<widget name="info_title" position="%d,%d"'
            ' size="%d,%d" font="E2iRegular;30"'
            ' foregroundColor="#FFFFFF" transparent="1"'
            ' valign="center" zPosition="4"/>'
            % (
                INFO_X,
                INFO_Y,
                INFO_W,
                INFO_TITLE_H
            ) +

            '<widget name="info_text" position="%d,%d"'
            ' size="%d,%d" font="E2iRegular;24"'
            ' foregroundColor="#FFFFFF" transparent="1"'
            ' valign="top" zPosition="4"/>'
            % (
                INFO_X,
                INFO_TEXT_Y,
                INFO_W,
                INFO_TEXT_H
            ) +

            # ----------------------------------------------------------
            # B3 — Series list (browsing) / series-name heading
            # (once a series is selected — the season bar shares this
            # same row, to the right of the heading).
            # ----------------------------------------------------------

            '<widget name="b3_title" position="%d,%d"'
            ' size="%d,%d" font="E2iRegular;27"'
            ' foregroundColor="#FFFFFF" transparent="1"'
            ' valign="center" zPosition="4"/>'
            % (
                B3_X,
                B3_Y,
                SERIES_NAME_W,
                B3_ITEM_H
            ) +

            '<widget name="b3_list" position="%d,%d"'
            ' size="%d,%d" scrollbarMode="showNever"'
            ' backgroundColor="#%s" zPosition="4"/>'
            % (
                B3_X,
                B3_LIST_TOP,
                B3_W,
                B3_VISIBLE_ROWS * B3_ITEM_H,
                BG_COLOUR
            ) +

            '<widget name="season_bar" position="%d,%d"'
            ' size="%d,%d" scrollbarMode="showNever"'
            ' backgroundColor="#%s" zPosition="4"/>'
            % (
                SEASON_BAR_X,
                SEASON_BAR_Y,
                SEASON_BAR_W,
                SEASON_BAR_H,
                BG_COLOUR
            ) +

            # ----------------------------------------------------------
            # B4 — Episode list. Full width, below the
            # series-name/season row, once a series is selected.
            # ----------------------------------------------------------

            '<widget name="b4_list" position="%d,%d"'
            ' size="%d,%d" scrollbarMode="showNever"'
            ' backgroundColor="#%s" zPosition="4"/>'
            % (
                B4_X,
                B4_Y,
                B4_W,
                B4_H,
                BG_COLOUR
            ) +

            '<widget name="series_poster" position="%d,%d"'
            ' size="%d,%d" transparent="1" zPosition="4"/>'
            % (
                POSTER_X,
                POSTER_Y,
                POSTER_W,
                POSTER_H
            ) +

            '</screen>'
        )

    # ==================================================================
    # B1
    #
    # Series no longer has its own rail or dispatch — B1Common is only
    # used here for the permanently-collapsed, static icon strip (same
    # icons, same positions as Screen1's). Leaving the collapsed area
    # opens the real Screen2 (see _on_left below), exactly like Screen1
    # does, instead of expanding a rail of its own.
    # ==================================================================

    def _show_b3_panel(self):
        for name in (
            "b3_title",
            "b3_list",
            "season_bar",
            "b4_list",
            "series_poster",
            "info_title",
            "info_text",
        ):
            try:
                self[name].show()
            except Exception:
                pass

    def _hide_b3_panel(self):
        for name in (
            "b3_title",
            "b3_list",
            "season_bar",
            "b4_list",
            "series_poster",
            "info_title",
            "info_text",
        ):
            try:
                self[name].hide()
            except Exception:
                pass

    def _on_left(self):

        if self._b3_focus:

            if self._content_mode == "episodes":

                if self._episode_focus:
                    # Episode list -> straight back to the series list.
                    # Left/Right now belong entirely to the season bar
                    # (cycling seasons, both directions) — OK is the only
                    # way to move focus down into the episode list, so
                    # there's no longer a reason for Left to stop at the
                    # season bar on the way out; that intermediate step
                    # was the "confusing drop" this whole redesign was
                    # about.
                    self._content_mode = "series"
                    self._series_info = None
                    self._season_sel = 0
                    self._episodes = []
                    self._episode_sel = 0
                    self._episode_focus = False
                    self._refresh_content()
                    return

                # Season bar -> cycle to the PREVIOUS season, wrapping
                # from the first back to the last. Mirrors _cycle_season
                # (Right) exactly, just decrementing — season bar no
                # longer exits to Series on Left at all; OK is the only
                # way out of it now (down into episodes), matching Exit
                # below for the direct "back to Series" shortcut.
                self._cycle_season_back()
                return

            # content_mode == "series": Left returns focus to B2.
            # Stop/invalidate any background category load first so
            # late Stalker pages cannot repopulate B3 after we leave it.
            self._stop_b3_loader()
            self._b3_focus = False
            self._hide_b3_panel()
            return

        # At the top of B2 with nothing left to back out of locally —
        # same as Screen1, this opens the real Screen2 (the rail,
        # Recordings, Settings) on top of this screen, passing self as
        # the content screen so its mirror shows Series' own B2/B3
        # instead of Live TV's. See get_mirror_b2/get_mirror_b3 and
        # _handle_child_close below.
        from .screen2 import Screen2
        self.session.openWithCallback(
            self._handle_child_close, Screen2, self
        )

    def _on_right(self):

        # From B2, Right enters B3 when Series are available.
        if (
            not self._b3_focus and
            self._content_mode == "series" and
            self._series_items
        ):
            self._b3_focus = True
            self._show_b3_panel()
            self._refresh_content()
            return

        # Season bar has focus — Right cycles to the next season,
        # wrapping from the last back to the first. Episode focus
        # doesn't consume Right; only the season bar does.
        if (
            self._b3_focus and
            self._content_mode == "episodes" and
            not self._episode_focus
        ):
            self._cycle_season()
            return

    def _handle_child_close(self, result=None):
        """Callback for the real Screen2 this screen opens via
        _on_left. Resolves the shared "goto:<action>" close protocol
        (see B1Common._b1_select / Screen1._handle_child_close):
        Recordings/Settings are handled entirely inside that Screen2
        itself (it never closes for those), so the only things that
        ever reach here are leaving to Live TV, Exit, or a normal
        close with nothing chosen."""

        if not result or not str(result).startswith("goto:"):
            # Closed normally (e.g. pressed < again from the bare
            # rail) — nothing to do, this screen is still exactly as
            # it was underneath.
            return

        action = result[len("goto:"):]

        if action == "exit":
            # Not this screen's to satisfy — forward to whoever opened
            # it, same as Screen2 does for anything it can't handle
            # itself.
            self.close("goto:exit")
            return

        if action == "live":
            # Propagate all the way up, same as "exit" above — don't
            # assume whatever's directly underneath this screen IS
            # Live TV. Series is usually opened nested on an
            # intermediate Screen2 (from "Series" chosen on its rail),
            # which is just a rail overlay, not Live TV itself, and
            # wouldn't run Screen1's proper refresh-on-return logic if
            # we stopped here. Forwarding lets it bubble through
            # however many levels exist until it reaches Screen1,
            # which is the only place that actually shows Live TV.
            self.close("goto:live")
            return

        if action == "search":
            # Same forwarding as "exit"/"live" above — this screen
            # isn't Search's to satisfy either, and needs to bubble up
            # to Screen1 the same way. Previously fell through the
            # bottom of this function doing nothing at all when this
            # case wasn't handled, which is why selecting Search from
            # within Series/Movies appeared to just silently reopen
            # Series — nothing had actually happened; Series had been
            # sitting there unchanged underneath the whole time.
            self.close("goto:search")
            return

        if action == "favourites":
            # Same forwarding, same reasoning — added ahead of time
            # this round rather than waiting to find it the same way
            # "search" was found.
            self.close("goto:favourites")
            return

        if action == "music":
            self.close("goto:music")
            return

        # "series"/"recordings"/"settings" shouldn't reach here —
        # Recordings/Settings are handled locally by that Screen2, and
        # selecting "Series" again there opens a fresh Series screen
        # rather than closing anything.

    def get_mirror_b2(self):
        """Standard content-screen method — the same interface
        Screen1 implements. Lets a Screen2 opened on top of this
        screen mirror Series' own category list instead of assuming
        Live TV."""
        return self._b2_flat, self._b2_sel

    def get_mirror_b3(self):
        """Standard content-screen method. Series' B3 (series list, or
        seasons/episodes depending on _content_mode) isn't shaped like
        a channel list — no picons, no stream_id — so there's nothing
        meaningful to hand Screen2's channel-row mirror yet. Returning
        empty means that area is honestly blank rather than showing
        something wrong."""
        return [], 0, 0


    def _b1_refresh(self):
        B1Common._b1_refresh(self)

    def _apply_media_category_visibility(self, provider, cats):
        """
        Apply the provider's saved category visibility to the
        Movies and Series category lists.

        Live TV is not touched here.
        """
        if self._content_type not in ("movies", "series"):
            return cats

        try:
            from .backend.provider_category_settings import load

            hidden = load(
                provider,
                self._content_type
            )

            if not hidden:
                return cats

            result = []

            for item in cats:
                try:
                    data = item[1]
                    category = data.get("category")
                    category_id = str(
                        getattr(category, "category_id", "")
                    )

                    if category_id in hidden:
                        continue

                except Exception:
                    pass

                result.append(item)

            return result

        except Exception:
            return cats

    # ==================================================================
    # LOAD
    # ==================================================================

    def _goto_check(self):
        """Became the active screen again (e.g. a film/episode player
        above closed): if the player's Menu asked for another section,
        hand it on to Live TV (goto.py). Asking for this same section
        just stays here."""
        from .goto import peek, take
        action = peek()
        if action is None:
            return
        take()
        from .goto import nav_log
        nav_log("%s active again, Menu asked for %r" % (self._content_type, action))
        if action == self._content_type:
            return
        from twisted.internet import reactor as _r
        _r.callLater(0, self.close, "goto:%s" % action)

    def _load(self):

        # If this screen has already been closed (see
        # _stop_load_retry_timer), a queued retry callback that fired
        # in the gap before the timer's own .stop() took effect must
        # not touch any widgets — they may already be destroyed.
        if getattr(self, "_load_retry_pending", None) is False:
            return

        providers = getattr(state, "providers", [])

        if not providers:
            # state.providers is populated by Screen1's background
            # loading thread (kicked off from its own onLayoutFinish),
            # which runs fully independently of this screen's own
            # onLayoutFinish. If this screen was opened very early —
            # e.g. via the startup "resume into Series" path — it can
            # race ahead of that thread and land here before any
            # providers exist yet. Retrying shortly instead of giving
            # up here means a slow/late provider load still resolves
            # correctly instead of leaving Series permanently blank.
            self._b2_flat = []
            self._b2_sel = 0
            self._b2_refresh()

            if getattr(self, "_load_retry_count", 0) < 20:
                self._load_retry_count = getattr(self, "_load_retry_count", 0) + 1
                self._load_retry_pending = True
                self._load_retry_timer = eTimer()
                self._load_retry_timer.callback.append(self._load)
                self._load_retry_timer.start(250, True)

            return

        self._load_retry_count = 0

        # From here on is entirely "restore whatever provider/category
        # was last used for browsing" — which must never run at all
        # when a Search jump-to is pending (see _had_jump_target in
        # __init__), not just have its final render suppressed as the
        # previous version of this guard did. That narrower guard only
        # covered self._refresh_content() further down; this
        # restoration logic itself still ran unconditionally before
        # it, still calling self._b2_refresh() with the *old* saved
        # provider/category — which is exactly what was overwriting
        # B2 back to the last-browsed provider after
        # _do_jump_to_series() had already correctly set it to the
        # actual jump target. _do_jump_to_series() sets up B2 for the
        # jump target itself; _load() has nothing useful left to do
        # once providers are confirmed ready.
        if self._had_jump_target:
            return

        state_key_prefix = (
            "movies" if self._content_type == "movies" else "series"
        )

        saved_provider_idx = getattr(
            state,
            state_key_prefix + "_provider_idx",
            None
        )

        saved_category_id = getattr(
            state,
            state_key_prefix + "_category_id",
            None
        )

        # Restore the previously used Series provider.
        #
        # A new Series screen starts with empty _b2_open_cats, so the
        # category list must be loaded before B2 can be rebuilt with
        # that provider expanded.
        if (
            saved_provider_idx is not None
            and 0 <= saved_provider_idx < len(providers)
        ):
            provider = providers[saved_provider_idx][1]

            try:
                cats_raw = self._get_b2_categories(provider)
                cats = self._cats_to_list(cats_raw)
                cats = self._apply_media_category_visibility(
                    provider,
                    cats
                )

                self._b2_open_set.add(saved_provider_idx)
                self._b2_open_cats[saved_provider_idx] = cats

            except Exception as e:
                print(
                    "[E2iPTV] Series: failed restoring provider:",
                    e
                )

        self._b2_rebuild_flat()

        # Restore the previously used Series category.
        restored_idx = None

        if (
            saved_provider_idx is not None
            and saved_category_id is not None
        ):
            for i, (label, data, is_cat) in enumerate(
                self._b2_flat
            ):
                if not is_cat:
                    continue

                if data.get("provider_idx") != saved_provider_idx:
                    continue

                category = data.get("category")

                if category is None:
                    continue

                if str(
                    getattr(category, "category_id", "")
                ) == str(saved_category_id):
                    restored_idx = i
                    break

        if restored_idx is not None:
            self._b2_sel = restored_idx

        elif self._b2_flat:
            self._b2_sel = min(
                self._b2_sel,
                len(self._b2_flat) - 1
            )

        else:
            self._b2_sel = 0

        self._b2_refresh()

        # Guarded: if this screen was opened via a Search jump-to, this
        # must never render — _do_jump_to_series()/_series_ok() is what
        # renders the actual jump target, and this can otherwise fire
        # well after that already happened (a late provider-load retry
        # — see this method's own retry branch above), overwriting the
        # correct episodes view with a stale Series-list render. See
        # __init__'s _had_jump_target comment for why this checks that
        # rather than _jump_to_series, which is already cleared by now
        # whenever this situation actually arises.
        if not self._had_jump_target:
            self._refresh_content()

    # ==================================================================
    # B2 NAVIGATION
    # ==================================================================

    def _on_up(self):

        # B3 has focus.
        if self._b3_focus:
            self._content_up()
            return

        # B2 has focus.
        if not self._b2_flat:
            return

        if self._b2_sel <= 0:
            return

        self._b2_sel -= 1
        self._b2_refresh()


    def _on_down(self):

        # B3 has focus.
        if self._b3_focus:
            self._content_down()
            return

        # B2 has focus.
        if not self._b2_flat:
            return

        if self._b2_sel >= len(self._b2_flat) - 1:
            return

        self._b2_sel += 1
        self._b2_refresh()


    def _on_ok(self):

        if self._b3_focus:

            if self._content_mode == "series":
                self._b3_ok_with_menu()
                return

            # content_mode == "episodes"
            if not self._episode_focus:
                # Season bar -> move focus down into the episode list.
                self._episode_focus = True
                self._refresh_content()
                return

            # Episode list has focus -> play the highlighted episode.
            self._episode_ok()
            return

        if not self._b2_flat:
            return

        if (
            self._b2_sel < 0 or
            self._b2_sel >= len(self._b2_flat)
        ):
            return

        label, data, is_cat = self._b2_flat[self._b2_sel]

        # ----------------------------------------------------------
        # B2 category:
        # The category row now carries its own provider.
        # Do NOT try to rediscover the provider by walking B2.
        # ----------------------------------------------------------

        if is_cat:

            category = data.get("category")
            provider = data.get("provider")
            prov_idx = data.get("provider_idx")

            if provider is None or category is None:
                print(
                    "[E2iPTV] Series: invalid B2 category row:",
                    label
                )
                return

            self._series_provider_idx = prov_idx
            self._series_provider = provider
            self._series_category = category

            # Remember the provider/category so a new screen instance
            # of this same content type (Series or Movies — they're
            # tracked separately) can reopen at the same place.
            state_key_prefix = (
                "movies" if self._content_type == "movies" else "series"
            )

            setattr(state, state_key_prefix + "_provider_idx", prov_idx)
            setattr(
                state,
                state_key_prefix + "_category_id",
                getattr(category, "category_id", None)
            )

            print(
                "[E2iPTV] Series category selected:",
                getattr(
                    category,
                    "category_name",
                    getattr(
                        category,
                        "name",
                        str(category)
                    )
                ),
                "provider=",
                getattr(provider, "name", ""),
                "id=",
                getattr(category, "category_id", "")
            )

            self._b3_focus = True

            self._request_b3_series(
                provider,
                category
            )
            return

        # ----------------------------------------------------------
        # B2 provider:
        # Opening/changing provider clears ALL old Series state.
        # ----------------------------------------------------------

        provider = data.get("provider")

        if provider is None:
            return

        prov_idx = data.get("provider_idx")

        if prov_idx is None:
            prov_idx = next(
                (
                    i
                    for i, (name, p) in enumerate(state.providers)
                    if p is provider
                ),
                None
            )

        if prov_idx is None:
            return

        # Always discard the old B3/B4 state when the provider row
        # is acted upon.  This prevents the previous provider's
        # series from remaining visible.
        self._b3_load_id += 1
        self._b3_loading = False
        self._b3_result = None

        self._series_items = []
        self._series_sel = 0
        self._series_provider_idx = None
        self._series_provider = None
        self._series_category = None
        self._series_info = None
        self._season_sel = 0
        self._episodes = []
        self._episode_sel = 0
        self._content_mode = "series"
        self._episode_focus = False

        try:
            self["b3_list"].setList([])
            self["b4_list"].setList([])
            self["season_bar"].setSeasons([], 0)
            self["b3_title"].setText("")
            self["b4_title"].setText("")
            self["info_title"].setText("")
            self["info_text"].setText("")
        except Exception:
            pass

        if prov_idx in self._b2_open_set:

            self._b2_open_set.discard(prov_idx)
            self._b2_open_cats.pop(
                prov_idx,
                None
            )

        else:

            try:
                cats_raw = self._get_b2_categories(
                    provider
                )
            except Exception as e:
                print(
                    "[E2iPTV] Series: category fetch failed:",
                    e
                )
                cats_raw = []

            cats = self._cats_to_list(
                cats_raw
            )
            cats = self._apply_media_category_visibility(
                provider,
                cats
            )

            self._b2_open_set.add(prov_idx)
            self._b2_open_cats[
                prov_idx
            ] = cats

        self._b2_rebuild_flat()

        new_idx = next(
            (
                i
                for i, (
                    name,
                    pdata,
                    is_category
                ) in enumerate(self._b2_flat)
                if (
                    not is_category and
                    pdata.get("provider") is provider
                )
            ),
            None
        )

        if new_idx is not None:
            self._b2_sel = new_idx

        self._b2_refresh()

    # ==================================================================
    # B3 SERIES
    # ==================================================================

    def _finish_b3_series_items(self, items):
        """Finish a B3 category load using the same selection behaviour
        for Movies and Series."""

        old_sel = self._series_sel

        incremental_load = (
            self._content_type in ("movies", "series")
            and self._b3_loading
            and bool(self._series_items)
        )

        self._series_items = items
        self._series_sel = old_sel if incremental_load else 0

        if self._series_items:
            self._series_sel = min(
                self._series_sel,
                len(self._series_items) - 1
            )
        else:
            self._series_sel = 0

        pending_id = self._pending_jump_series_id
        self._pending_jump_series_id = None

        if pending_id is not None:
            idx = next(
                (
                    i for i, item in enumerate(items)
                    if str(getattr(item.series, "series_id", ""))
                    == str(pending_id)
                ),
                None
            )

            if idx is not None:
                self._series_sel = idx
                self._b3_focus = True
                self._series_ok()
                return

        self._b3_focus = bool(items)

        if items:
            self._show_b3_panel()

        self._refresh_b3_only()


    def _request_b3_series(self, provider, category):
        """Fetch one B2 category only after OK, without blocking the GUI."""

        # content_type is part of the key deliberately: Xtream numbers
        # movie categories and series categories independently, so a
        # movie category and a series category can easily share the
        # same category_id. Without content_type here, switching
        # between Movies and Series on the same provider could return
        # the other content type's cached (or empty) list instead of
        # actually fetching this one — the category-cache equivalent
        # of the backend's own category_store bug fixed earlier.
        cache_key = (
            getattr(provider, "provider_key", None),
            getattr(category, "category_id", None),
            self._content_type
        )

        # A result already obtained for this exact provider/category can
        # be displayed immediately.  No API call is made.
        cached = self._b3_category_cache.get(cache_key)
        if cached is not None:
            self._finish_b3_series_items(cached)
            return

        self._b3_load_id += 1
        load_id = self._b3_load_id
        self._b3_loading = True
        self._b3_result = None

        self._series_items = []
        self._series_sel = 0
        self._content_mode = "series"

        self["b3_title"].setText(
            "Movies" if self._content_type == "movies" else "Series"
        )
        self["b3_list"].setList([])

        def worker():
            result = []
            error = None

            try:
                if self._content_type == "movies":
                    source_type = getattr(provider, "source_type", "")

                    if source_type.upper() == "STALKER":
                        from .backend.stalker_client import StalkerClient
                        from .backend.movie_reader import MovieReader

                        client = StalkerClient(provider)
                        reader = MovieReader()

                        raw_items, total = client.get_vod_page(
                            category.category_id,
                            1
                        )

                        result = reader._read_stalker_items(
                            provider,
                            raw_items
                        )

                        self._b3_result = (
                            load_id,
                            cache_key,
                            result,
                            None,
                            False
                        )

                        page_size = len(raw_items)

                        if page_size and total > page_size:
                            for page_no in range(
                                2,
                                (total + page_size - 1) // page_size + 1
                            ):
                                try:
                                    raw_items, unused_total = (
                                        client.get_vod_page(
                                            category.category_id,
                                            page_no
                                        )
                                    )

                                    more = reader._read_stalker_items(
                                        provider,
                                        raw_items
                                    )

                                    if more:
                                        result.extend(more)

                                        self._b3_result = (
                                            load_id,
                                            cache_key,
                                            result,
                                            None,
                                            False
                                        )

                                except Exception as e:
                                    print(
                                        "[E2iPTV] Stalker movie page "
                                        "load failed:",
                                        page_no,
                                        e
                                    )
                                    self._b3_result = (
                                        load_id,
                                        cache_key,
                                        result,
                                        e,
                                        True
                                    )
                                    return

                        self._b3_result = (
                            load_id,
                            cache_key,
                            result,
                            None,
                            True
                        )

                        return

                    result = state.backend.get_movies(
                        provider,
                        category
                    )
                else:
                    source_type = getattr(provider, "source_type", "")

                    if source_type.upper() == "STALKER":
                        from .backend.stalker_client import StalkerClient
                        from .backend.series_reader import SeriesReader

                        client = StalkerClient(provider)
                        reader = SeriesReader()

                        raw_items, total = client.get_series_page(
                            category.category_id,
                            1
                        )

                        result = reader._read_stalker_items(
                            provider,
                            raw_items
                        )

                        self._b3_result = (
                            load_id,
                            cache_key,
                            result,
                            None,
                            False
                        )

                        page_size = len(raw_items)

                        if page_size and total > page_size:
                            for page_no in range(
                                2,
                                (total + page_size - 1) // page_size + 1
                            ):
                                try:
                                    raw_items, unused_total = (
                                        client.get_series_page(
                                            category.category_id,
                                            page_no
                                        )
                                    )

                                    more = reader._read_stalker_items(
                                        provider,
                                        raw_items
                                    )

                                    if more:
                                        result.extend(more)

                                        self._b3_result = (
                                            load_id,
                                            cache_key,
                                            result,
                                            None,
                                            False
                                        )

                                except Exception as e:
                                    print(
                                        "[E2iPTV] Stalker Series page "
                                        "load failed:",
                                        page_no,
                                        e
                                    )
                                    self._b3_result = (
                                        load_id,
                                        cache_key,
                                        result,
                                        e,
                                        True
                                    )
                                    return

                        self._b3_result = (
                            load_id,
                            cache_key,
                            result,
                            None,
                            True
                        )

                        return

                    result = state.backend.get_series(
                        provider,
                        category
                    )
            except Exception as e:
                error = e

            print(
                "[E2iPTV] B3 WORKER FINISHED:",
                "type=", self._content_type,
                "provider=", getattr(provider, "name", ""),
                "category=", getattr(category, "category_id", ""),
                "items=", len(result or []),
                "error=", error
            )

            self._b3_result = (
                load_id,
                cache_key,
                result,
                error,
                True
            )

        self._b3_thread = threading.Thread(
            target=worker,
            name="E2iPTV-Series-B3"
        )
        self._b3_thread.daemon = True
        self._b3_thread.start()

        try:
            self._b3_timer.start(100, False)
        except Exception as e:
            print("[E2iPTV] B3 timer start failed:", e)
            self._b3_loading = False

    def _b3_poll(self):
        result = self._b3_result

        if result is None:
            if self._b3_loading:
                try:
                    self._b3_timer.start(100, True)
                except Exception:
                    pass
            return

        self._b3_result = None

        load_id, cache_key, series, error, done = result

        # Ignore a late response from an older OK if another category was
        # selected in the meantime.
        if load_id != self._b3_load_id:
            if self._b3_loading:
                try:
                    self._b3_timer.start(100, True)
                except Exception:
                    pass
            return

        if error is not None:
            print("[E2iPTV] B3 Series load failed:", error)
            series = []

        items = []

        for item in (series or []):

            if not getattr(item, "name", ""):
                continue

            if self._content_type == "movies":
                # Movie uses stream_icon where Series uses cover —
                # alias it so the poster-loading code (built for
                # Series) works unchanged for movies too.
                item.cover = getattr(item, "stream_icon", "")

            wrapped = MediaB3Item(item)

            if self._content_type == "movies":
                # A Movie already carries everything create_rule/the
                # info panel need — no separate info-fetch step like
                # Series' get_series_info.
                wrapped.info = item

            items.append(wrapped)

        self._b3_category_cache[cache_key] = items
        self._finish_b3_series_items(items)

        self._b3_loading = not done

        print(
            "[E2iPTV] B3 category loaded:",
            len(items),
            "done=",
            done
        )

        if self._b3_loading:
            try:
                self._b3_timer.start(100, True)
            except Exception:
                pass

    def _stop_b3_loader(self):
        self._b3_load_id += 1
        self._b3_loading = False

        try:
            self._b3_timer.stop()
        except Exception:
            pass

    def _stop_load_retry_timer(self):
        # This screen may close while a provider-load retry is still
        # pending (e.g. quick rail navigation before providers ever
        # finished loading) — without this, the timer fires afterward
        # and calls _load() on an already-destroyed screen, touching
        # widgets that no longer exist natively.
        self._load_retry_pending = False

        timer = getattr(self, "_load_retry_timer", None)

        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass

    def _clear_b3(self):

        self._series_items = []
        self._series_sel = 0

        try:
            self["b3_title"].setText("")
        except Exception:
            pass

        try:
            self["b3_list"].setList([])
        except Exception:
            pass


    def _favourite_keys(self):
        """Set of (provider_id, item_id) pairs already favourited for
        this screen's content type — loaded fresh each call (a small
        JSON read) rather than cached, since the favourites list can
        change mid-session via the Watch/Add to Favourites menu and a
        stale cached set would then mismark rows until the screen
        happened to fully reload."""
        content_type = "movie" if self._content_type == "movies" else "series"
        try:
            return FavouritesStore().favourite_keys(content_type)
        except Exception:
            return set()

    def _b3_row_label(self, series, favourite_keys, provider):
        """The B3 row label for one Movie/Series — name, prefixed with
        a star if it's already favourited. provider is whichever
        provider this item actually belongs to (matters for All-
        Providers-style views if this is ever reused there; for normal
        browsing it's always self._series_provider)."""
        name = getattr(series, "name", "") or ""
        item_id = (
            getattr(series, "stream_id", None)
            if self._content_type == "movies"
            else getattr(series, "series_id", None)
        )
        key = (
            str(getattr(provider, "provider_key", None)),
            str(item_id)
        )
        if key in favourite_keys:
            return "\u2605 " + name
        return name

    def _refresh_b3_only(self):

        entries = []

        start = _visible_window(
            self._series_sel,
            len(self._series_items),
            B3_VISIBLE_ROWS
        )

        end = min(
            start + B3_VISIBLE_ROWS,
            len(self._series_items)
        )

        favourite_keys = self._favourite_keys()

        for i in range(start, end):

            item = self._series_items[i]

            entries.append(
                self._entry(
                    self._b3_row_label(
                        item.series, favourite_keys, self._series_provider
                    ),
                    i == self._series_sel
                )
            )

        try:
            self["b3_title"].setText(
                "Movies" if self._content_type == "movies" else "Series"
            )
            incremental_movie_load = (
                self._content_type in ("movies", "series")
                and self._b3_loading
                and bool(getattr(self["b3_list"], "_entries", []))
            )

            if incremental_movie_load:
                # More Stalker pages are being appended. Rebuild the
                # native list but immediately restore the currently
                # highlighted visible row.
                old_visible = self._series_sel - start
                self["b3_list"].setList(entries)
                self["b3_list"].moveToIndex(old_visible)
            else:
                self["b3_list"].setList(entries)

            self["b3_list"].show()

            if self._series_items:

                highlighted = self._series_items[
                    self._series_sel
                ].series

                self["info_title"].setText(
                    getattr(highlighted, "name", "")
                )

                self["info_text"].setText(
                    getattr(highlighted, "plot", "") or ""
                )

            else:
                self["info_title"].setText("")
                self["info_text"].setText("")

            self["season_bar"].setSeasons([], 0)
            self["season_bar"].hide()

            self["b4_list"].setList([])
            self["b4_list"].hide()
        except Exception:
            pass

        # The poster always follows the currently selected Series.
        self._refresh_series_poster()
        self._refresh_movie_info()


    # ==================================================================
    # SERIES POSTER
    # ==================================================================

    def _poster_cache_path(self, url):

        if not url:
            return None

        try:
            key = hashlib.md5(
                url.encode("utf-8")
            ).hexdigest()

            return os.path.join(
                SERIES_POSTER_CACHE,
                key + ".jpg"
            )

        except Exception:
            return None

    def _load_poster_pixmap(self, path):
        """LoadPixmap displays at the source image's native resolution
        — if that's bigger than the widget, it gets clipped/cropped
        rather than scaled down to fit. Loading with an explicit
        target size scales it properly instead. Older Enigma2 builds
        may not support these kwargs, so this falls back to a plain
        load (still functional, just not scaled) if so."""

        try:
            return LoadPixmap(
                path,
                cached=False,
                desired_width=POSTER_W,
                desired_height=POSTER_H
            )
        except TypeError:
            return LoadPixmap(
                path,
                cached=False
            )

    def _refresh_series_poster(self):

        self._poster_generation += 1
        generation = self._poster_generation

        # Clear the previous poster immediately when the selection changes.
        try:
            self["series_poster"].instance.setPixmap(None)
        except Exception:
            pass

        if not self._series_items:
            self._poster_result = None
            try:
                self["series_poster"].instance.setPixmap(None)
            except Exception:
                pass
            return

        if (
            self._series_sel < 0 or
            self._series_sel >= len(self._series_items)
        ):
            return

        item = self._series_items[self._series_sel]
        series = item.series

        url = getattr(series, "cover", "") or ""

        if not url:
            try:
                self["series_poster"].instance.setPixmap(None)
            except Exception:
                pass
            return

        cache_path = self._poster_cache_path(url)

        if not cache_path:
            return

        # Cached poster — no network access.
        if os.path.exists(cache_path):
            try:
                pixmap = self._load_poster_pixmap(cache_path)

                if pixmap:
                    try:
                        self["series_poster"].instance.setScale(1)
                    except Exception:
                        pass

                    self["series_poster"].instance.setPixmap(
                        pixmap
                    )
            except Exception as e:
                print(
                    "[E2iPTV] Poster cache display failed:",
                    e
                )

            return

        # A new selection is allowed to supersede an older poster
        # download.  _poster_generation ensures that an old result
        # can never be displayed over the current selection.
        self._poster_loading = True
        self._poster_result = None

        def worker():

            result = None
            error = None

            try:

                if not os.path.isdir(
                    SERIES_POSTER_CACHE
                ):
                    os.makedirs(
                        SERIES_POSTER_CACHE
                    )

                tmp_path = cache_path + ".download"

                data = urllib2.urlopen(
                    url,
                    timeout=5
                ).read()

                with open(
                    tmp_path,
                    "wb"
                ) as f:
                    f.write(data)

                os.rename(
                    tmp_path,
                    cache_path
                )

                result = cache_path

            except Exception as e:

                error = e

                try:
                    if os.path.exists(
                        cache_path + ".download"
                    ):
                        os.remove(
                            cache_path + ".download"
                        )
                except Exception:
                    pass

            self._poster_result = (
                generation,
                result,
                error
            )

        self._poster_thread = threading.Thread(
            target=worker,
            name="E2iPTV-Series-Poster"
        )

        self._poster_thread.daemon = True
        self._poster_thread.start()

        try:
            self._poster_timer.start(
                100,
                False
            )
        except Exception:
            pass


    def _poster_poll(self):

        result = self._poster_result

        if result is None:

            if self._poster_loading:
                try:
                    self._poster_timer.start(
                        100,
                        True
                    )
                except Exception:
                    pass

            return

        self._poster_result = None
        self._poster_loading = False

        generation, path, error = result

        # Selection changed while downloading.
        if generation != self._poster_generation:
            return

        if error is not None:
            print(
                "[E2iPTV] Series poster load failed:",
                error
            )
            return

        if not path or not os.path.exists(path):
            return

        try:

            pixmap = self._load_poster_pixmap(path)

            if pixmap:
                try:
                    self["series_poster"].instance.setScale(1)
                except Exception:
                    pass

                self["series_poster"].instance.setPixmap(
                    pixmap
                )

        except Exception as e:

            print(
                "[E2iPTV] Series poster display failed:",
                e
            )


    def _stop_poster_loader(self):

        self._poster_generation += 1
        self._poster_loading = False
        self._poster_result = None

        try:
            self._poster_timer.stop()
        except Exception:
            pass

    def _stop_series_info_loader(self):

        self._series_info_result = None
        self._series_loading = None
        self._series_req_id = getattr(self, "_series_req_id", 0) + 1
        try:
            before = getattr(self, "_series_title_before", None)
            self._series_title_before = None
            if before is not None:
                self["b3_title"].setText(before)
        except Exception:
            pass

        try:
            self._series_info_timer.stop()
        except Exception:
            pass

    # ==================================================================
    # MOVIE INFO (lazy plot/cast/genre fetch — movies only)
    # ==================================================================

    def _refresh_movie_info(self):
        """Same background-thread + polling-timer pattern as
        _refresh_series_poster just above — fetching detail fields via
        Backend.get_movie_info() instead of an image. Movies only:
        Series already has plot/cast/genre for free from its own list
        call (see SeriesReader/MovieInfoReader docstrings), so this is
        a no-op whenever _content_type isn't "movies"."""

        if self._content_type != "movies":
            return

        self._movie_info_generation += 1
        generation = self._movie_info_generation

        if not self._series_items:
            self._movie_info_result = None
            return

        if (
            self._series_sel < 0 or
            self._series_sel >= len(self._series_items)
        ):
            return

        movie = self._series_items[self._series_sel].series

        if getattr(movie, "info_loaded", False):
            # Already fetched (e.g. an earlier visit to this movie
            # this session) — just make sure it's actually on screen.
            try:
                self["info_text"].setText(
                    getattr(movie, "plot", "") or ""
                )
            except Exception:
                pass
            return

        if self._series_provider is None:
            return

        provider = self._series_provider

        self._movie_info_loading = True
        self._movie_info_result = None

        def worker():

            error = None

            try:
                ok = state.backend.get_movie_info(provider, movie)
            except Exception as e:
                ok = False
                error = e

            self._movie_info_result = (generation, ok, error)

        thread = threading.Thread(
            target=worker,
            name="E2iPTV-Movie-Info"
        )

        thread.daemon = True
        thread.start()

        try:
            self._movie_info_timer.start(100, False)
        except Exception:
            pass

    def _movie_info_poll(self):

        result = self._movie_info_result

        if result is None:

            if self._movie_info_loading:
                try:
                    self._movie_info_timer.start(100, True)
                except Exception:
                    pass

            return

        self._movie_info_result = None
        self._movie_info_loading = False

        generation, ok, error = result

        # Selection changed while fetching — discard.
        if generation != self._movie_info_generation:
            return

        if not ok:
            if error:
                print("[E2iPTV] Movie info load failed:", error)
            return

        if (
            not self._series_items or
            self._series_sel < 0 or
            self._series_sel >= len(self._series_items)
        ):
            return

        movie = self._series_items[self._series_sel].series

        try:
            self["info_text"].setText(
                getattr(movie, "plot", "") or ""
            )
        except Exception:
            pass

    def _stop_movie_info_loader(self):

        self._movie_info_generation += 1
        self._movie_info_loading = False
        self._movie_info_result = None

        try:
            self._movie_info_timer.stop()
        except Exception:
            pass


    def _do_jump_to_series(self):
        """Opens straight into a specific series' episodes — set by
        Search (see search.py). Properly sets up B2 (expands the
        actual provider, selects the actual category the series
        belongs to) and fetches that category's real series list via
        the normal _request_b3_series flow, rather than faking a
        single-item list standing in for it — so backing out of
        episodes lands on a correctly populated B2/B3, not an empty
        rail with nothing in it. _finish_b3_series_items (shared with
        the normal OK-driven path) is what actually resolves
        _pending_jump_series_id against that real list and opens the
        matching item once it's loaded."""

        series = self._jump_to_series
        provider = self._jump_to_provider

        self._jump_to_series = None
        self._jump_to_provider = None

        if series is None or provider is None:
            _debug_log(
                "_do_jump_to_series: series or provider is None "
                "(series=%r provider=%r) — nothing to do" %
                (series, provider)
            )
            return

        category_id = getattr(series, "category_id", None)

        _debug_log(
            "_do_jump_to_series: series=%r series_id=%r category_id=%r "
            "provider=%r" % (
                getattr(series, "name", ""),
                getattr(series, "series_id", ""),
                category_id,
                getattr(provider, "name", "")
            )
        )

        prov_idx = next(
            (
                i for i, (_name, p) in enumerate(state.providers or [])
                if p is provider
            ),
            None
        )

        category = None

        if category_id and prov_idx is not None:

            try:
                cats_raw = self._get_b2_categories(provider)
                cats = self._cats_to_list(cats_raw)
                cats = self._apply_media_category_visibility(
                    provider,
                    cats
                )
            except Exception as e:
                print("[E2iPTV] Search jump: category fetch failed:", e)
                _debug_log(
                    "_do_jump_to_series: category fetch raised: %s" % (e,)
                )
                cats = []

            self._b2_open_set.add(prov_idx)
            self._b2_open_cats[prov_idx] = cats
            self._b2_rebuild_flat()

            category = next(
                (
                    cdata.get("category")
                    for (_cname, cdata) in cats
                    if str(getattr(cdata.get("category"), "category_id", ""))
                    == str(category_id)
                ),
                None
            )

            _debug_log(
                "_do_jump_to_series: fetched %d categories for provider, "
                "target category_id=%r %s" % (
                    len(cats), category_id,
                    "FOUND: %r" % getattr(category, "category_name", "")
                    if category is not None else "NOT FOUND (have: %r)" % (
                        [
                            getattr(cdata.get("category"), "category_id", None)
                            for (_cname, cdata) in cats
                        ][:20]
                    )
                )
            )

            if category is not None:

                b2_idx = next(
                    (
                        i for i, (_label, data, is_cat)
                        in enumerate(self._b2_flat)
                        if is_cat
                        and data.get("provider_idx") == prov_idx
                        and getattr(
                            data.get("category"), "category_id", None
                        ) == category.category_id
                    ),
                    None
                )

                if b2_idx is not None:
                    self._b2_sel = b2_idx

            self._b2_refresh()

        self._series_provider = provider
        self._series_provider_idx = prov_idx
        self._series_category = category

        if category is None:
            # Couldn't resolve where this series actually lives (no
            # category_id on the result, category fetch failed, or the
            # category's gone since search indexed it) — fall back to
            # at least opening the series itself rather than showing
            # nothing, same as before this fix, just as a last resort
            # instead of the normal path.
            print(
                "[E2iPTV] Search jump: category not resolved for",
                getattr(series, "name", ""),
                "— opening series directly, B2/B3 won't reflect it"
            )
            _debug_log(
                "_do_jump_to_series: category is None after resolution "
                "attempt (category_id=%r, prov_idx=%r) — falling back to "
                "direct open, B2/B3 will NOT be correctly populated" %
                (category_id, prov_idx)
            )
            self._series_items = [MediaB3Item(series)]
            self._series_sel = 0
            self._b3_focus = True
            self._series_ok()
            return

        self._pending_jump_series_id = getattr(series, "series_id", None)

        _debug_log(
            "_do_jump_to_series: category resolved OK, requesting B3 "
            "series list for category_id=%r, pending_jump_series_id=%r" %
            (category_id, self._pending_jump_series_id)
        )

        self._request_b3_series(provider, category)

    def _b3_ok_with_menu(self):
        """OK on a B3 item (a Movie, or a Series before drilling into
        its episodes) — shows Watch/Add to Favourites first, rather
        than going straight to _series_ok() the way this used to work
        and the way Search's jump-to-series still deliberately does
        (see _do_jump_to_series/_finish_b3_series_items, which call
        _series_ok() directly): choosing a specific search result
        already *is* the "watch this" action, so that path skips this
        menu on purpose rather than asking again."""

        if (
            not self._series_items or
            self._series_sel < 0 or
            self._series_sel >= len(self._series_items)
        ):
            return

        item = self._series_items[self._series_sel]
        obj = item.series
        provider = self._series_provider

        content_type = "movie" if self._content_type == "movies" else "series"

        item_id = (
            getattr(obj, "stream_id", None) if content_type == "movie"
            else getattr(obj, "series_id", None)
        )

        already = False
        if provider is not None and item_id is not None:
            try:
                already = FavouritesStore().is_favourite(
                    content_type,
                    getattr(provider, "provider_key", None),
                    item_id
                )
            except Exception:
                already = False

        self.session.openWithCallback(
            lambda choice: self._b3_menu_choice(choice, obj, provider),
            FavouriteChoiceMenu,
            getattr(obj, "name", ""),
            already,
            content_type == "movie"
        )

    def _b3_menu_choice(self, choice, obj, provider):

        if choice == "watch":
            self._series_ok()
            return

        if choice == "download":
            if self._content_type != "movies":
                return

            from . import player as _player

            _player.download_movie(
                self.session,
                provider,
                obj
            )
            return

        if choice not in ("favourite", "unfavourite"):
            return  # Cancelled (None) — stay on the list, do nothing.

        content_type = "movie" if self._content_type == "movies" else "series"

        try:
            store = FavouritesStore()
            if choice == "favourite":
                store.add(
                    content_type,
                    provider,
                    obj,
                    getattr(obj, "category_id", None)
                )
            else:
                store.remove(
                    content_type,
                    getattr(provider, "provider_key", None),
                    getattr(obj, "stream_id", None)
                    if content_type == "movie"
                    else getattr(obj, "series_id", None)
                )
        except Exception as e:
            print("[E2iPTV] Favourites update failed:", e)
            return

        self._refresh_b3_only()

    def _series_ok(self):

        if not self._series_items:
            return

        if self._content_type == "movies":
            self._movie_ok()
            return

        item = self._series_items[self._series_sel]
        series = item.series

        print(
            "[E2iPTV] Series selected:",
            getattr(series, "name", ""),
            "id=",
            getattr(series, "series_id", "")
        )

        # Reuse already-loaded info on a repeat visit to this Series
        # within the same screen session — no need to re-fetch, and
        # it's what lets the remembered season below actually mean
        # something (same item object, same cached seasons).
        if item.info is not None:
            _debug_log(
                "_series_ok: item.info already cached for %r, "
                "continuing immediately" % getattr(series, "name", "")
            )
            self._series_ok_continue(item, series)
            return

        # Already loading this same series: ignore the repeat press.
        # Each press used to start another identical request to the
        # provider, which only made the wait longer — and a late answer
        # to an earlier press could be picked up afterwards.
        loading = getattr(self, "_series_loading", None)
        if loading is not None and loading[1] is item:
            _debug_log(
                "_series_ok: %r already loading — repeat press ignored"
                % getattr(series, "name", "")
            )
            return

        self._series_req_id = getattr(self, "_series_req_id", 0) + 1
        req_id = self._series_req_id
        self._series_loading = (req_id, item, time.time())

        # Show that something is happening while the provider answers.
        try:
            if getattr(self, "_series_title_before", None) is None:
                self._series_title_before = self["b3_title"].getText()
            self["b3_title"].setText(
                "Loading %s…" % getattr(series, "name", "")
            )
        except Exception:
            pass

        _debug_log(
            "_series_ok: fetching get_series_info for %r (series_id=%r) "
            "in background thread [request %d]" % (
                getattr(series, "name", ""),
                getattr(series, "series_id", ""),
                req_id
            )
        )

        # First visit to this Series this session — get_series_info is
        # a real network call, so (like every other fetch in this
        # file) it needs to happen off the GUI thread rather than
        # blocking it directly inline, which is how this used to work.
        # That was mostly harmless from a normal keypress — just a
        # brief freeze — but this same method also runs from an
        # already-async context (Search/Favourites' jump-to-series
        # path, whenever the category it resolved to wasn't already
        # cached — see _finish_b3_series_items), and blocking the
        # reactor thread inside THAT callback left the episodes screen
        # showing but not actually painted with its content until some
        # unrelated later event — a keypress — forced Enigma2 to
        # redraw. Backgrounding this fetch unconditionally fixes both.

        def worker():
            try:
                info = state.backend.get_series_info(
                    self._series_provider,
                    series
                )
                _debug_log(
                    "_series_ok worker: get_series_info returned %s for "
                    "%r" % (
                        "a result" if info is not None else "None",
                        getattr(series, "name", "")
                    )
                )
            except Exception as e:
                print("[E2iPTV] Series info load failed:", e)
                _debug_log(
                    "_series_ok worker: get_series_info RAISED for %r: "
                    "%s" % (getattr(series, "name", ""), e)
                )
                info = None
            # Only the newest request's answer is wanted; an older one
            # (user moved on to another series) is simply dropped.
            if req_id == getattr(self, "_series_req_id", None):
                self._series_info_result = (req_id, item, series, info)

        thread = threading.Thread(
            target=worker,
            name="E2iPTV-Series-Info"
        )
        thread.daemon = True
        thread.start()

        try:
            self._series_info_timer.start(100, False)
        except Exception as e:
            _debug_log(
                "_series_ok: starting _series_info_timer RAISED: %s" %
                (e,)
            )

    def _series_info_poll(self):

        result = self._series_info_result

        if result is None:
            return

        self._series_info_result = None

        req_id, item, series, info = result
        if req_id != getattr(self, "_series_req_id", None):
            return          # stale answer — keep waiting for the current one

        self._series_info_timer.stop()
        loading = getattr(self, "_series_loading", None)
        self._series_loading = None
        took = time.time() - loading[2] if loading else 0
        _debug_log(
            "_series_info_poll: %r answered in %.1fs"
            % (getattr(series, "name", ""), took)
        )

        try:
            before = getattr(self, "_series_title_before", None)
            self._series_title_before = None
            if info is None:
                self["b3_title"].setText(
                    "Couldn't load %s — try again" % getattr(series, "name", "")
                )
            elif before is not None:
                self["b3_title"].setText(before)
        except Exception:
            pass

        if info is None:
            # Already logged in the worker above — nothing further to
            # do, same as the old inline version's behaviour on error.
            _debug_log(
                "_series_info_poll: info is None for %r — stopping "
                "here, nothing will open" % getattr(series, "name", "")
            )
            return

        _debug_log(
            "_series_info_poll: info received for %r, proceeding to "
            "process seasons/episodes" % getattr(series, "name", "")
        )

        item.info = info

        all_seasons = list(
            getattr(info, "seasons", []) or []
        )

        # Only keep seasons that actually have episode content.
        # Some Xtream sources list an empty "Specials" (season 0)
        # placeholder even when there are no special episodes —
        # this keeps a genuine Specials season (if it has real
        # episodes) but drops empty/phantom entries either way.
        episodes = getattr(info, "episodes", []) or []

        seasons_with_episodes = set(
            getattr(ep, "season_number", None)
            for ep in episodes
        )

        item.seasons = [
            s for s in all_seasons
            if getattr(s, "season_number", None)
            in seasons_with_episodes
        ]

        # Some providers omit the "seasons" array entirely for a
        # given series (get_series_info returns an empty list)
        # even though "episodes" genuinely has content grouped by
        # season number — in which case the filter above has
        # nothing to keep, no matter how many episodes exist, and
        # the series would show its title and nothing else. Any
        # season number seen in the episodes but missing from
        # all_seasons gets a minimal synthesized Season here so
        # it still shows up and is selectable.
        covered = set(
            getattr(s, "season_number", None)
            for s in item.seasons
        )

        missing = sorted(
            sn for sn in seasons_with_episodes
            if sn is not None and sn not in covered
        )

        for sn in missing:

            from .backend.season import Season

            synthesized = Season()
            synthesized.source_id = getattr(series, "source_id", 0)
            synthesized.series_id = series.series_id
            synthesized.season_id = sn
            synthesized.season_number = sn
            synthesized.name = (
                "Specials" if sn == 0 else "Season %d" % sn
            )
            synthesized.episode_count = sum(
                1 for ep in episodes
                if getattr(ep, "season_number", None) == sn
            )

            item.seasons.append(synthesized)

        item.seasons.sort(
            key=lambda s: getattr(s, "season_number", 0)
        )

        self._series_ok_continue(item, series)

    def _series_ok_continue(self, item, series):
        """Shared tail of _series_ok() — runs once item.info/
        item.seasons are known good, whether that was immediate
        (already cached from an earlier visit) or just finished an
        async fetch (see _series_info_poll above)."""

        _debug_log(
            "_series_ok_continue: %r has %d seasons after processing — "
            "proceeding to show episodes" % (
                getattr(series, "name", ""), len(item.seasons)
            )
        )

        self._series_info = item.info

        if (
            item.season_selected is not None and
            item.season_selected < len(item.seasons)
        ):
            # A season was already picked on a previous visit to this
            # Series this session — reopen on that one.
            self._season_sel = item.season_selected

        else:
            # First visit — default to "Season 1" rather than
            # whatever happens to sort first (often Specials/Season 0
            # on some sources).
            self._season_sel = next(
                (
                    i for i, s in enumerate(item.seasons)
                    if getattr(s, "season_number", 0) == 1
                ),
                0
            )

        item.season_selected = self._season_sel

        self._content_mode = "episodes"
        self._episode_focus = False

        self._load_episodes_for_season(item, self._season_sel)

        self._refresh_content()

    # ==================================================================
    # MOVIES — no seasons/episodes, OK plays directly.
    # ==================================================================

    def _movie_ok(self):

        if not self._series_items:
            return

        item = self._series_items[self._series_sel]
        movie = item.series

        print(
            "[E2iPTV] Movie selected:",
            getattr(movie, "name", ""),
            "id=",
            getattr(movie, "stream_id", "")
        )

        if self._series_provider is None:
            return

        from . import player as _player

        _player.play_movie(
            self.session,
            self._series_provider,
            movie
        )

    # ==================================================================
    # SEASONS
    # ==================================================================

    def _load_episodes_for_season(self, item, season_index):
        """Filters the already-loaded episode list down to one season.
        No network call — series_info.episodes carries every episode
        for every season already, fetched once in _series_ok(). Used
        both for the initial season and every subsequent season-bar
        move, so switching seasons is instant."""

        if (
            not item.seasons or
            season_index < 0 or
            season_index >= len(item.seasons)
        ):
            self._episodes = []
            self._episode_sel = 0
            return

        season = item.seasons[season_index]

        season_number = getattr(
            season,
            "season_number",
            0
        )

        self._episodes = [
            ep for ep in (
                getattr(item.info, "episodes", []) or []
            )
            if getattr(ep, "season_number", 0) == season_number
        ]

        self._episode_sel = 0

    def _cycle_season(self):
        """Right on the season bar — moves to the next season,
        wrapping from the last back to the first. Episode list below
        updates immediately, no OK needed."""

        if not self._series_items:
            return

        item = self._series_items[self._series_sel]

        if not item.seasons:
            return

        self._season_sel = (
            (self._season_sel + 1) % len(item.seasons)
        )

        item.season_selected = self._season_sel

        self._load_episodes_for_season(item, self._season_sel)

        self._refresh_content()

    def _cycle_season_back(self):
        """Left on the season bar — moves to the PREVIOUS season,
        wrapping from the first back to the last. Mirrors
        _cycle_season (Right) exactly, just decrementing."""

        if not self._series_items:
            return

        item = self._series_items[self._series_sel]

        if not item.seasons:
            return

        self._season_sel = (
            (self._season_sel - 1) % len(item.seasons)
        )

        item.season_selected = self._season_sel

        self._load_episodes_for_season(item, self._season_sel)

        self._refresh_content()

    # ==================================================================
    # B4 EPISODES
    # ==================================================================

    def _episode_ok(self):

        if not self._episodes:
            return

        if (
            self._episode_sel < 0 or
            self._episode_sel >= len(self._episodes)
        ):
            return

        episode = self._episodes[self._episode_sel]

        print(
            "[E2iPTV] Episode selected:",
            getattr(episode, "title", ""),
            "id=",
            getattr(episode, "episode_id", "")
        )

        if self._series_provider is None:
            return

        from . import player as _player

        series_title = getattr(self._series_info, "name", "") or ""

        _player.play_episode(
            self.session,
            self._series_provider,
            episode,
            series_title,
            on_close_callback=lambda *a: self._refresh_content()
        )

    # ==================================================================
    # CONTENT NAVIGATION
    # ==================================================================

    def _content_up(self):

        if self._content_mode == "series":

            if self._series_sel > 0:
                self._series_sel -= 1

        elif self._content_mode == "episodes" and self._episode_focus:

            if self._episode_sel > 0:
                self._episode_sel -= 1

        self._refresh_content()

    def _content_down(self):

        if self._content_mode == "series":

            if self._series_sel < len(self._series_items) - 1:
                self._series_sel += 1

        elif self._content_mode == "episodes" and self._episode_focus:

            if self._episode_sel < len(self._episodes) - 1:
                self._episode_sel += 1

        self._refresh_content()

    # ==================================================================
    # CONTENT DISPLAY
    # ==================================================================

    def _entry(self, text, selected=False, width=B3_W):

        bg = (
            0xd0e8ff
            if selected
            else 0x0a4aa8
        )

        fg = (
            0x000000
            if selected
            else 0xffffff
        )

        return [
            None,
            MultiContentEntryText(
                pos=(0, 0),
                size=(width, B3_ITEM_H),
                font=0,
                flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                text=str(text),
                color=fg,
                color_sel=fg,
                backcolor=bg,
                backcolor_sel=bg
            )
        ]

    def _refresh_content(self):

        # --------------------------------------------------------------
        # Clear everything content-related up front.
        # --------------------------------------------------------------

        self["b3_list"].setList([])
        self["b4_list"].setList([])
        self["season_bar"].setSeasons([], 0)

        self["info_title"].setText("")
        self["info_text"].setText("")

        # --------------------------------------------------------------
        # Series mode — browsing the Series list. Info panel + poster
        # update live as the highlight moves, same data source (the
        # lightweight Series object already carries plot/genre/cast —
        # no extra API call needed).
        # --------------------------------------------------------------

        if self._content_mode == "series":

            # Explicitly hidden, not just emptied — these belong to
            # the episodes view and must never be left on screen
            # (with stale or empty content) while browsing the Series
            # list, whether that's on first open or backing out of an
            # episodes view without leaving B3 entirely.
            try:
                self["season_bar"].hide()
                self["b4_list"].hide()
            except Exception:
                pass

            try:
                self["b3_list"].show()
            except Exception:
                pass

            self._refresh_series_poster()
            self._refresh_movie_info()

            self["b3_title"].setText(
                "Movies" if self._content_type == "movies" else "Series"
            )

            if self._series_items:

                highlighted = self._series_items[
                    self._series_sel
                ].series

                self["info_title"].setText(
                    getattr(highlighted, "name", "")
                )

                self["info_text"].setText(
                    getattr(highlighted, "plot", "") or ""
                )

            entries = []

            start = _visible_window(
                self._series_sel,
                len(self._series_items),
                B3_VISIBLE_ROWS
            )

            end = min(
                start + B3_VISIBLE_ROWS,
                len(self._series_items)
            )

            favourite_keys = self._favourite_keys()

            for i in range(start, end):

                item = self._series_items[i]

                entries.append(
                    self._entry(
                        self._b3_row_label(
                            item.series, favourite_keys,
                            self._series_provider
                        ),
                        i == self._series_sel
                    )
                )

            self["b3_list"].setList(entries)

            try:
                self["b3_list"].moveToIndex(
                    self._series_sel - start
                )
            except Exception:
                pass

            return

        # --------------------------------------------------------------
        # Episodes mode — a Series is selected. Series-name heading +
        # season bar share one row; episode list fills the full width
        # below it. Info panel/poster stay on the selected Series.
        # --------------------------------------------------------------

        if not self._series_items:
            return

        try:
            self["b3_list"].hide()
            self["season_bar"].show()
            self["b4_list"].show()
        except Exception:
            pass

        item = self._series_items[self._series_sel]
        series = item.series

        self._refresh_series_poster()

        provider_name = getattr(self._series_provider, "name", "") or ""

        self["b3_title"].setText(
            "%s   [%s]" % (getattr(series, "name", ""), provider_name)
            if provider_name else getattr(series, "name", "")
        )

        plot = ""

        if item.info is not None:
            plot = getattr(item.info, "plot", "") or ""

        if not plot:
            plot = getattr(series, "plot", "") or ""

        self["info_title"].setText(
            getattr(series, "name", "")
        )

        self["info_text"].setText(str(plot))

        season_names = []

        for season in item.seasons:

            name = getattr(season, "name", "")

            number = getattr(season, "season_number", 0)

            if not name:
                name = "Season %d" % number

            season_names.append(name)

        self["season_bar"].setSeasons(
            season_names,
            self._season_sel
        )

        episode_entries = []

        ep_start = _visible_window(
            self._episode_sel,
            len(self._episodes),
            B4_VISIBLE_ROWS
        )

        ep_end = min(
            ep_start + B4_VISIBLE_ROWS,
            len(self._episodes)
        )

        from . import player as _player

        provider = self._series_provider

        for i in range(ep_start, ep_end):

            episode = self._episodes[i]

            num = getattr(
                episode,
                "episode_number",
                0
            )

            title = getattr(
                episode,
                "title",
                ""
            )

            text = "%02d  %s" % (
                num,
                title
            )

            try:
                provider_name = getattr(provider, "name", "") or ""

                # New watched entries use the stable episode identity.
                stable_watched_key = "episode_%s_%s_%s" % (
                    provider.source_type,
                    provider_name,
                    episode.episode_id
                )

                watched = _player.is_watched(stable_watched_key)

                # Preserve older Xtream watched entries, which were
                # stored using the stream URL.
                if not watched and provider.source_type != "STALKER":
                    url_watched_key = _player.build_series_url(
                        provider, episode
                    )
                    watched = _player.is_watched(url_watched_key)

                if watched:
                    text = "\u2713 " + text
            except Exception:
                pass

            episode_entries.append(
                self._entry(
                    text,
                    i == self._episode_sel,
                    width=B4_W
                )
            )

        self["b4_list"].setList(
            episode_entries
        )


    def _on_cancel(self):
        # Exit is deliberately disabled everywhere else on this screen —
        # < is the only way to leave/navigate back. One deliberate
        # exception: from the season bar or episode list, Exit is a
        # direct shortcut straight back to the series list, same
        # destination as Left from the episode list now goes to
        # (see _on_left) — since Left on the season bar itself is now
        # taken for cycling seasons rather than backing out, this gives
        # a fast, single-press way out of the whole seasons/episodes
        # view from either state within it.
        if self._b3_focus and self._content_mode == "episodes":
            self._content_mode = "series"
            self._series_info = None
            self._season_sel = 0
            self._episodes = []
            self._episode_sel = 0
            self._episode_focus = False
            self._refresh_content()
        return
