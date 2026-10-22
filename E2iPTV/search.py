# =============================================================================
# E2iPTV — Search
# Movies/Series search, by a single provider or across all of them.
# Reference: IPTVBrowser's own Search screens (SearchMenuScreen /
# GlobalMovieSearchScreen / GlobalSeriesSearchScreen) — same overall
# flow (type -> provider scope -> text entry -> ranked results), same
# ranking rule (exact match, then starts-with, then contains), rebuilt
# to fit this plugin's own conventions: real background threads + a
# polling eTimer (the same pattern used throughout media_browser.py
# and player.py) rather than a cooperative 1ms self-rescheduling timer
# per provider, and this plugin's own dark-panel screens throughout
# instead of native ChoiceBox/MessageBox chrome.
#
# Screen-stacking follows the SAME pattern Screen1/Screen2/
# MediaBrowser already use for their own B1-rail navigation (see e.g.
# MediaBrowser._handle_child_close): each step opens the next one with
# openWithCallback and stays open itself while the user browses, so
# Cancel at any point naturally reveals the previous step. When
# something needs to bubble all the way up instead (a series result
# actually chosen — see SearchResultsScreen._activate_selected), the
# CURRENT screen closes ITSELF with a payload describing what to do,
# and its own callback (the screen that opened it) either forwards
# that same payload by closing itself too, or — for the outermost
# level, Screen1's on_close_callback — actually acts on it.
#
# This replaces an earlier version that opened the target screen
# directly and then tried to close several ancestor screens
# afterwards, all in the same call. That looked fine on the very next
# call but corrupted Enigma2's session modal-stack tracking in a way
# that only surfaced as a crash on a later, unrelated session.open()
# ("Modal open are allowed only from a screen which is modal!") — and,
# separately, since navigating away from a half-finished search via
# the B1 rail (rather than Cancel) never closed any of it, left old
# search screens orphaned in the stack to resurface later. Never
# opening a new screen and closing an ancestor in the same call avoids
# the first problem; always forwarding through a real callback chain
# (rather than a manually-tracked "chain" list closed after the fact)
# avoids the second, the same way it already doesn't happen anywhere
# else in this app.
#
# Only VirtualKeyBoard (text entry) stays a native component — that's
# a standard, expected system control across every Enigma2 app, not
# something worth reinventing.
# =============================================================================

from Screens.Screen import Screen
from Screens.VirtualKeyBoard import VirtualKeyBoard
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MultiContent import MultiContentEntryText
from Components.GUIComponent import GUIComponent
from enigma import (
    eTimer, eListbox, eListboxPythonMultiContent,
    gFont, RT_HALIGN_LEFT, RT_VALIGN_CENTER, ePoint, gRGB, eSize
)
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from .state import state
from .player import play_movie
from .favourites import FavouritesStore
from .b1common import B1Common, B1_RAIL_COLOUR, B1_EXPANDED_W, B1_LABELS

# Matches media_browser.py's own B2_X/BG_COLOUR exactly — not imported
# from there (media_browser.py is large; a couple of layout constants
# aren't worth a module-level dependency on it) but these values must
# stay identical to it, since the whole point is matching its layout
# precisely. _CONTENT_W deliberately does NOT match B2's narrow 460px
# though — there's no separate B3-width stage here the way
# MediaBrowser has, so the content area (provider list, results list)
# fills most of the remaining screen instead, the same as the rest of
# this app's *content* areas do, not its narrow category-list column.
_CONTENT_X = 100
_CONTENT_W = 1920 - _CONTENT_X - 40
_BG_COLOUR = "0a4aa8"


# -----------------------------------------------------------------------------
# Shared scrollable text list — same colour convention as the rest of
# the plugin's list rows (dark navy unselected, light-blue selected).
# Same GUIComponent shape as media_browser.py's own list components
# (GUI_WIDGET + postWidgetCreate, not the lower-level GUIcreate/
# GUIdelete) — that's the pattern actually proven to render correctly
# in this environment. Used by both the provider-choice step and the
# results screen below.
# -----------------------------------------------------------------------------

LIST_ITEM_H = 60


class _SearchList(GUIComponent):

    GUI_WIDGET = eListbox

    def __init__(self, entries, row_width=1800):
        GUIComponent.__init__(self)
        self.l = eListboxPythonMultiContent()
        self.l.setItemHeight(LIST_ITEM_H)
        self.l.setFont(0, gFont("E2iRegular", 30))
        self._entries = []
        self._row_width = row_width
        self.setEntries(entries, 0)

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setWrapAround(False)
        # Without this, the native eListbox draws its own selection
        # indicator on top of/instead of the manually-coloured row
        # this class already paints per-row (backcolor_sel below) —
        # same double-highlight bug already solved for the Series B3/
        # B4 lists in this file, missed here on the first pass.
        instance.setSelectionEnable(False)

    def preWidgetRemove(self, instance):
        pass

    def setEntries(self, labels, selected_index=0):
        self._entries = labels

        if not labels:
            self.l.setList([])
            return

        selected_index = min(
            max(0, selected_index),
            len(labels) - 1
        )

        # Rows that actually fit: the list is 900 high and each row 60,
        # so 15 (building 16 put the last one just below the screen).
        visible = 15
        try:
            if self.instance:
                visible = max(1, self.instance.size().height() // LIST_ITEM_H)
        except Exception:
            pass

        start = max(
            0,
            min(
                selected_index - 5,
                len(labels) - visible
            )
        )

        end = min(
            start + visible,
            len(labels)
        )

        rows = []

        for absolute_index in range(start, end):
            text = labels[absolute_index]

            selected = (
                absolute_index == selected_index
            )

            bg = 0xd0e8ff if selected else 0x0a4aa8
            fg = 0x000000 if selected else 0xffffff

            rows.append([
                None,
                MultiContentEntryText(
                    pos=(20, 0),
                    size=(self._row_width, LIST_ITEM_H),
                    font=0,
                    flags=(
                        RT_HALIGN_LEFT |
                        RT_VALIGN_CENTER
                    ),
                    text=text,
                    color=fg,
                    color_sel=fg,
                    backcolor=bg,
                    backcolor_sel=bg
                )
            ])

        self.l.setList(rows)

        if self.instance:
            self.instance.moveSelectionTo(
                selected_index - start
            )

    def setSelection(self, index):
        self.setEntries(
            self._entries,
            index
        )


_LIST_SKIN = (
    '<screen name="%s" position="0,0" size="1920,1080"'
    ' flags="wfNoBorder" backgroundColor="#0a4aa8">'
    '<widget name="header" position="40,30" size="1840,50"'
    ' font="E2iRegular;34" foregroundColor="#1a98ff"'
    ' halign="left" valign="center" transparent="1" zPosition="1"/>'
    '<widget name="list" position="40,100" size="1840,900"'
    ' transparent="1" scrollbarMode="showOnDemand"/>'
    '</screen>'
)


# -----------------------------------------------------------------------------
# Step 1 — Movies or Series
# -----------------------------------------------------------------------------

class SearchTypeMenu(Screen):
    """Same dark-panel + moving-highlight-rect style as screen2.py's
    RecordingActionMenu (Play/Delete) — a self-contained sibling of it
    rather than an import, same reasoning as every other small UI
    helper class duplicated across this project (see e.g. player.py's
    ResumeChoiceMenu): keeps this file's import direction one-way,
    screen1 -> search, never the other way.

    Full 1920x1080 screen with an opaque blue backdrop behind the
    actual panel, rather than just the panel's own small 640x200 —
    same underlying problem as VirtualKeyBoard/native ChoiceBox
    elsewhere (see this project's own history on that), just entirely
    within code this project actually controls: a small opaque panel
    on its own still leaves everything outside its own bounds showing
    whatever's behind it (Live TV), regardless of the panel itself
    being properly opaque. All the panel's own widget coordinates
    below are shifted by the same (640,380) the panel itself used to
    be positioned at, so it still looks exactly as it did — only the
    area around it actually changed."""

    _OPTIONS = [("Search Movies", "movies"), ("Search Series", "series")]
    _SEL_FG    = 0x000000
    _NORMAL_FG = 0xffffff
    _ROW_Y = {0: 440, 1: 510}

    skin = (
        '<screen name="SearchTypeMenu"'
        ' position="0,0" size="1920,1080"'
        ' flags="wfNoBorder">'
        '<eLabel position="0,0" size="1920,1080"'
        ' backgroundColor="#0a4aa8" zPosition="0"/>'
        '<eLabel position="640,380" size="640,200"'
        ' backgroundColor="#0d1117" zPosition="0"/>'
        '<widget name="title" position="660,390" size="600,40"'
        ' font="E2iRegular;28" foregroundColor="#1a98ff"'
        ' halign="left" valign="center" transparent="1" zPosition="2"/>'
        '<eLabel position="640,440" size="640,1"'
        ' backgroundColor="#30ffffff" zPosition="2"/>'
        '<widget name="highlight_bg" position="3500,440" size="640,70"'
        ' backgroundColor="#d0e8ff" zPosition="1" transparent="0"/>'
        '<widget name="row0_text" position="670,440" size="580,70"'
        ' font="E2iRegular;30" foregroundColor="#ffffff"'
        ' halign="left" valign="center" transparent="1" zPosition="2"/>'
        '<widget name="row1_text" position="670,510" size="580,70"'
        ' font="E2iRegular;30" foregroundColor="#ffffff"'
        ' halign="left" valign="center" transparent="1" zPosition="2"/>'
        '</screen>'
    )

    def __init__(self, session):
        Screen.__init__(self, session)
        self._sel = 0
        self["title"]        = Label("Search")
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
        content_type = self._OPTIONS[self._sel][1]
        self.session.openWithCallback(
            self._on_child_close, SearchProviderMenu, content_type
        )

    def _on_child_close(self, result=None):
        if result is not None:
            # SearchProviderMenu (or something further down) is
            # forwarding a result it couldn't handle itself — not
            # Cancel (that's a plain None). Forward it again; this
            # screen isn't the one that acts on it either.
            self.close(result)
        # else: closed normally (Cancel) — this screen is already
        # visible again underneath, nothing further to do.

    def _dismiss(self):
        self.close(None)


# Remembered for the session: Movies or Series, the provider last
# searched (None = All Providers) and the last thing typed.
_last = {"type": "movies", "provider_key": None, "term": ""}


def open_search(session, on_close_callback):
    """Entry point — call this from B1's "search" action.

    on_close_callback is whatever _handle_child_close the caller
    itself uses to resolve the shared "goto:<action>" close protocol
    (see B1Common._b1_select) — screen1.py's, in the only place this
    is actually called from today. It's also what finally acts on an
    ("activate", ...) result once it's bubbled all the way back up
    through every step — see this module's docstring."""
    # One Search screen (type toggle, search row, providers) - the old
    # Movies/Series pop-up (SearchTypeMenu) is no longer used.
    session.openWithCallback(on_close_callback, SearchProviderMenu, _last["type"])


# -----------------------------------------------------------------------------
# Step 2 — All Providers, or a specific one
# -----------------------------------------------------------------------------


class SearchProviderMenu(B1Common, Screen):
    """Search's provider-choice screen.

    Mixes in B1Common for the visual rail treatment MediaBrowser also
    has (_b1_skin_xml/_b1_register_widgets) — but unlike MediaBrowser
    at its true top level, this screen isn't actually the outermost
    one in its flow: SearchTypeMenu opened it with openWithCallback
    and is still alive underneath. So Left just steps back to it
    (self.close(None)), the same way Left in Series steps from
    episodes back to the series list before ever reaching Screen2 —
    it does NOT open Screen2 directly, which was an earlier mistake
    here (see _on_left's own comment for the full story, including why
    that also caused a visual mismatch trying to mirror a plain list
    into Screen2's windowed-row B2 rendering).

    No _b1_own_action is set, matching MediaBrowser (which doesn't set
    one either) — selecting "Search" again while already here just
    forwards through the normal "goto:search" path rather than trying
    to special-case "already on this screen"."""

    def __init__(self, session, content_type):
        Screen.__init__(self, session)

        self._content_type = content_type
        self._b1_init(expanded=False)

        # Disabled providers (a leading # before http in Playlists.txt
        # — see playlist_reader.py) are excluded here, same as the
        # "All Providers" search loop already excludes them — picking
        # one individually from this list should be just as
        # unavailable as it being swept up in an "All Providers" run.
        from .backend.provider import shows as _shows
        providers = [
            p for _name, p in (state.providers or [])
            if (_shows(p, "movies") or _shows(p, "series"))
            and not getattr(p, "is_bouquet", False)
        ]
        self._providers = [("All Providers", None)]
        for p in providers:
            self._providers.append((getattr(p, "name", "") or "Provider", p))
        self._options = []
        self._build_options()
        # Start on the Search row.
        self._sel = 1

        self.skin = (
            '<screen name="SearchProviderMenu" position="0,0"'
            ' size="1920,1080" flags="wfNoBorder">'

            '<eLabel position="0,0" size="1920,1080"'
            ' backgroundColor="#%s" zPosition="0"/>'
            % _BG_COLOUR +

            '<eLabel position="0,0" size="%d,1080"'
            ' backgroundColor="#%s" zPosition="1"/>'
            % (B1_EXPANDED_W, B1_RAIL_COLOUR) +

            '<widget name="content_bg" position="%d,0"'
            ' size="%d,1080" backgroundColor="#%s" zPosition="2"/>'
            % (_CONTENT_X, _CONTENT_W, _BG_COLOUR) +

            self._b1_skin_xml() +

            '<widget name="header" position="%d,30" size="%d,50"'
            ' font="E2iRegular;30" foregroundColor="#1a98ff"'
            ' halign="left" valign="center" transparent="1"'
            ' noWrap="1" zPosition="4"/>'
            % (_CONTENT_X + 20, _CONTENT_W - 40) +

            '<widget name="list" position="%d,100"'
            ' size="%d,900" transparent="1"'
            ' scrollbarMode="showOnDemand" zPosition="4"/>'
            % (_CONTENT_X, _CONTENT_W) +

            '</screen>'
        )

        self["content_bg"] = Label("")
        self["header"] = Label("Search")
        self["list"] = _SearchList([], row_width=_CONTENT_W - 40)
        self.onLayoutFinish.append(self._refresh_rows)

        self._b1_register_widgets()

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self._k_ok,
                "cancel": self._k_cancel,
                "up":     self._k_up,
                "down":   self._k_down,
                "left":   self._on_left,
                "right":  self._k_right,
            }, -1)

    def _up(self):
        if self._options:
            self._sel = (self._sel - 1) % len(self._options)
            self["list"].setSelection(self._sel)

    def _down(self):
        if self._options:
            self._sel = (self._sel + 1) % len(self._options)
            self["list"].setSelection(self._sel)

    # Rows: 0 = Type toggle, 1 = Search, then All Providers / providers.
    _ROW_TYPE, _ROW_SEARCH, _FIRST_PROVIDER = 0, 1, 2

    def _chosen_provider(self):
        key = _last.get("provider_key")
        for _label, p in self._providers:
            if p is not None and str(getattr(p, "provider_key", "")) == str(key):
                return p
        return None                     # All Providers

    def _build_options(self):
        movies = self._content_type == "movies"
        type_row = "Type:      %s      %s" % (
            "[ Movies ]" if movies else "  Movies  ",
            "[ Series ]" if not movies else "  Series  ")
        term = _last.get("term") or ""
        search_row = ('Search:   "%s"' % term) if term else "Search:   (OK to type)"
        chosen = self._chosen_provider()
        rows = [(type_row, "type"), (search_row, "search")]
        for label, p in self._providers:
            mark = "\u25cf  " if p is chosen else "     "
            rows.append((mark + label, p))
        self._options = rows

    def _refresh_rows(self):
        self._build_options()
        self["list"].setEntries([label for (label, _p) in self._options], self._sel)

    def _confirm(self):
        if self._sel == self._ROW_TYPE:
            self._content_type = "series" if self._content_type == "movies" else "movies"
            _last["type"] = self._content_type
            self._refresh_rows()
            return
        if self._sel == self._ROW_SEARCH:
            provider = self._chosen_provider()
        else:
            provider = self._options[self._sel][1]   # None = All Providers
            _last["provider_key"] = (str(getattr(provider, "provider_key", ""))
                                     if provider is not None else None)
            self._refresh_rows()
        self.session.openWithCallback(
            lambda text: self._text_entered(provider, text),
            VirtualKeyBoard,
            title="Search %s%s" % (
                "Movies" if self._content_type == "movies" else "Series",
                " in %s" % provider.name if provider is not None
                else " — All Providers"
            ),
            text=_last.get("term") or ""
        )

    def _text_entered(self, provider, text):
        if not text:
            return  # Cancelled the keyboard — stay open, nothing to do.
        _last["term"] = text
        self._refresh_rows()
        self.session.openWithCallback(
            self._handle_child_close,
            SearchResultsScreen, self._content_type, provider, text
        )

    # ----- Icon rail on this screen (Left) -------------------------------
    # Left expands the rail already drawn down this screen's left edge,
    # with Search highlighted; the Search list stays where it is (slid
    # right by the rail's extra width). OK on another item opens it, as
    # from any rail; OK on Search, Right or Exit closes the rail again.
    _b1_own_action = "search"

    def _on_left(self):
        if getattr(self, "_b1_expanded", False):
            return
        try:
            self._b1_sel = B1_LABELS.index("Search")
            state.b1_sel = self._b1_sel
        except Exception:
            pass
        self._b1_expand()
        self._shift_content(True)

    def _b1_collapse(self):
        B1Common._b1_collapse(self)
        self._shift_content(False)

    def _shift_content(self, rail_open):
        x = B1_EXPANDED_W if rail_open else _CONTENT_X
        w = _CONTENT_W - (B1_EXPANDED_W - _CONTENT_X if rail_open else 0)
        try:
            self["content_bg"].instance.move(ePoint(x, 0))
            self["header"].instance.move(ePoint(x + 20, 30))
            self["list"].instance.move(ePoint(x, 100))
            self["list"].instance.resize(eSize(w, 900))
        except Exception:
            pass

    def _key(self, list_action, rail_action):
        """Send a key to the rail while it's open, otherwise the list."""
        if getattr(self, "_b1_expanded", False):
            if rail_action is not None:
                rail_action()
        else:
            list_action()

    def _k_ok(self):
        self._key(self._confirm, self._b1_ok)

    def _k_up(self):
        self._key(self._up, self._b1_up)

    def _k_down(self):
        self._key(self._down, self._b1_down)

    def _k_right(self):
        self._key(lambda: None, self._b1_collapse)

    def _k_cancel(self):
        self._key(self._dismiss, self._b1_collapse)

    def _handle_child_close(self, result=None):
        # Callback for SearchResultsScreen (opened via text entry) —
        # closes with something to forward (an ("activate", ...)
        # tuple — see this module's docstring) or with None (closed
        # normally / stepped back via its own Left), in which case
        # there's nothing to do and this screen is already visible
        # again underneath.
        if result is not None:
            self.close(result)

    def _dismiss(self):
        self.close(None)


# -----------------------------------------------------------------------------
# Step 3 — results
# -----------------------------------------------------------------------------

RESULT_VISIBLE_ROWS = 14


from .side_menu import SideMenu


class _SearchFavouriteChoiceMenu(SideMenu):
    """Watch / Add-or-Remove Favourites — the plugin's right-hand panel."""

    def __init__(self, session, title, already_favourite=False):
        SideMenu.__init__(self, session, title,
                          [("Watch", "watch"),
                           ("Remove from Favourites", "unfavourite")
                           if already_favourite else
                           ("Add to Favourites", "favourite")],
                          value_only=True)

class SearchResultsScreen(B1Common, Screen):
    """Runs the actual search in a background thread (one provider at
    a time when scoped to "All Providers", matching the background-
    thread + polling-eTimer + generation-counter pattern used
    throughout media_browser.py) and shows ranked results in a
    scrollable list.

    Same B1Common integration as SearchProviderMenu, for the same
    visual-rail reasoning — and the same corrected Left behaviour:
    steps back to SearchProviderMenu (still alive underneath, opened
    with openWithCallback), rather than opening Screen2 directly,
    which used to be the only way in *or* out of this screen (Cancel/
    Exit was the sole option; Left duplicated it now too, so both
    behave the same way — see _on_left/_dismiss).

    _activate_selected() is the one thing OK does — kept as its own
    method rather than inlined so that swapping "always play
    immediately" for a Play/Download/Add to Favourites side menu later
    (mentioned as likely upcoming) only means changing what this one
    method does, not restructuring the screen."""

    def __init__(self, session, content_type, provider, term):
        Screen.__init__(self, session)

        self._content_type = content_type
        self._provider = provider          # None = all providers
        self._term = term.strip()
        self._term_lower = self._term.lower()

        self._results = []                 # list of (label, obj, provider)
        self._sel = 0

        self._b1_init(expanded=False)

        self.skin = (
            '<screen name="SearchResultsScreen" position="0,0"'
            ' size="1920,1080" flags="wfNoBorder">'

            '<eLabel position="0,0" size="1920,1080"'
            ' backgroundColor="#%s" zPosition="0"/>'
            % _BG_COLOUR +

            '<eLabel position="0,0" size="%d,1080"'
            ' backgroundColor="#%s" zPosition="1"/>'
            % (B1_EXPANDED_W, B1_RAIL_COLOUR) +

            '<widget name="content_bg" position="%d,0"'
            ' size="%d,1080" backgroundColor="#%s" zPosition="2"/>'
            % (_CONTENT_X, _CONTENT_W, _BG_COLOUR) +

            self._b1_skin_xml() +

            '<widget name="header" position="%d,30" size="%d,50"'
            ' font="E2iRegular;30" foregroundColor="#1a98ff"'
            ' halign="left" valign="center" transparent="1"'
            ' noWrap="1" zPosition="4"/>'
            % (_CONTENT_X + 20, _CONTENT_W - 40) +

            '<widget name="list" position="%d,100"'
            ' size="%d,900" transparent="1"'
            ' scrollbarMode="showOnDemand" zPosition="4"/>'
            % (_CONTENT_X, _CONTENT_W) +

            '</screen>'
        )

        self["content_bg"] = Label("")
        self["header"] = Label("Searching…")
        self["list"] = _SearchList([], row_width=_CONTENT_W - 40)

        self._b1_register_widgets()

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self._activate_selected,
                "cancel": self._dismiss,
                "up":     self._up,
                "down":   self._down,
                "left":   self._on_left,
            }, -1)

        self._search_thread = None
        self._search_state = None   # ("progress", i, total, name) or ("done", results)
        self._search_timer = eTimer()
        self._search_timer.callback.append(self._poll)

        self.onLayoutFinish.append(self._start_search)

    def _on_left(self):
        # Steps back to SearchProviderMenu, which opened this screen
        # and is still alive underneath — not "open Screen2 directly",
        # which was the actual mistake (see SearchProviderMenu's
        # identical method for the full reasoning). This also sidesteps
        # the row-position mismatch from the first screenshot entirely:
        # Screen2's B2 mirror assumes a long, scrollable list with the
        # selection at a fixed row, which never matched this screen's
        # plain list rendering — not opening Screen2 at all removes the
        # need to reconcile the two.
        self.close(None)

    def _dismiss(self):
        self._on_left()  # Cancel/Exit behaves the same as Left here.

    # -------------------------------------------------------------

    def _start_search(self):

        from concurrent.futures import (
            ThreadPoolExecutor,
            as_completed
        )

        content_type = self._content_type
        from .backend.provider import shows as _shows
        _kind = "movies" if str(content_type).startswith("movie") else "series"
        providers = (
            [self._provider] if self._provider is not None
            else [
                p for _name, p in (state.providers or [])
                if _shows(p, _kind)
                and not getattr(p, "is_bouquet", False)
            ]
        )
        term_lower = self._term_lower
        CATEGORY_TIMEOUT = 20

        def rank_of(name_lower):
            if name_lower == term_lower:
                return 0
            if name_lower.startswith(term_lower):
                return 1
            return 2

        def worker():

            found = []
            total = len(providers)

            for i, provider in enumerate(providers):

                self._search_state = (
                    "progress",
                    i,
                    total,
                    getattr(provider, "name", "") or "provider"
                )

                pool = None

                try:
                    source_type = getattr(
                        provider,
                        "source_type",
                        ""
                    ).upper()

                    if source_type == "STALKER":
                        from .backend.stalker_client import StalkerClient

                        client = StalkerClient(provider)

                        raw_items = client.search_media(
                            content_type,
                            self._term,
                            limit=500
                        )

                        if content_type == "movies":
                            from .backend.movie_reader import MovieReader

                            items = MovieReader()._read_stalker_items(
                                provider,
                                raw_items
                            )
                        else:
                            from .backend.series_reader import SeriesReader

                            items = SeriesReader()._read_stalker_items(
                                provider,
                                raw_items
                            )

                        from .backend.private import hidden as _private_hidden
                        _priv = _private_hidden(provider, content_type)
                        for item in items:
                            name = getattr(item, "name", "") or ""
                            name_lower = name.lower()

                            if term_lower not in name_lower:
                                continue
                            if str(getattr(item, "category_id", "")) in _priv:
                                continue

                            found.append((
                                rank_of(name_lower),
                                name,
                                item,
                                provider
                            ))

                        continue

                    if content_type == "movies":
                        categories = (
                            state.backend.get_movie_categories(
                                provider
                            )
                        )
                        fetch = state.backend.get_movies
                    else:
                        categories = (
                            state.backend.get_series_categories(
                                provider
                            )
                        )
                        fetch = state.backend.get_series

                    disabled = set(
                        getattr(
                            provider,
                            "disabled_cats",
                            set()
                        ) or set()
                    )
                    # Private categories too, unless shown
                    from .backend.private import hidden as _private_hidden
                    disabled |= _private_hidden(provider, content_type)

                    disabled = set(str(x) for x in disabled)
                    categories = [
                        category
                        for category in (categories or [])
                        if str(getattr(
                            category,
                            "category_id",
                            None
                        )) not in disabled
                    ]

                    pool = ThreadPoolExecutor(
                        max_workers=8
                    )

                    futures = {}

                    for category in categories:
                        futures[
                            pool.submit(
                                fetch,
                                provider,
                                category
                            )
                        ] = category

                    try:
                        for future in as_completed(
                            futures,
                            timeout=CATEGORY_TIMEOUT
                        ):

                            category = futures[future]

                            try:
                                items = future.result()
                            except Exception as e:
                                print(
                                    "[E2iPTV] Search: category failed "
                                    "%s / %s: %s"
                                    % (
                                        getattr(
                                            provider,
                                            "name",
                                            "provider"
                                        ),
                                        getattr(
                                            category,
                                            "name",
                                            getattr(
                                                category,
                                                "category_id",
                                                "category"
                                            )
                                        ),
                                        e
                                    )
                                )
                                continue

                            for item in (items or []):

                                name = (
                                    getattr(
                                        item,
                                        "name",
                                        ""
                                    ) or ""
                                )

                                name_lower = name.lower()

                                if term_lower not in name_lower:
                                    continue

                                found.append((
                                    rank_of(name_lower),
                                    name,
                                    item,
                                    provider
                                ))

                    except TimeoutError:
                        print(
                            "[E2iPTV] Search: category batch timed "
                            "out after %ds for %s"
                            % (
                                CATEGORY_TIMEOUT,
                                getattr(
                                    provider,
                                    "name",
                                    "provider"
                                )
                            )
                        )

                        for future in futures:
                            if not future.done():
                                future.cancel()

                except Exception as e:
                    print(
                        "[E2iPTV] Search: provider failed %s: %s"
                        % (
                            getattr(
                                provider,
                                "name",
                                "provider"
                            ),
                            e
                        )
                    )

                finally:
                    if pool is not None:
                        pool.shutdown(
                            wait=False
                        )

            found.sort(
                key=lambda t: (
                    t[0],
                    t[1].lower()
                )
            )

            self._search_state = (
                "done",
                found
            )

        self._search_thread = threading.Thread(
            target=worker,
            name="E2iPTV-Search"
        )

        self._search_thread.daemon = True
        self._search_thread.start()

        self._search_timer.start(
            150,
            False
        )

    def _poll(self):

        state_ = self._search_state

        if state_ is None:
            return

        kind = state_[0]

        if kind == "progress":

            _kind, i, total, name = state_

            if total > 1:
                self["header"].setText(
                    "Searching %s… (%d/%d)" % (name, i + 1, total)
                )
            else:
                self["header"].setText("Searching %s…" % name)

            return

        # kind == "done"

        self._search_timer.stop()
        self._search_state = None

        _kind, result = state_

        self._results = [
            (
                "%s   [%s]" % (name, getattr(provider, "name", ""))
                if self._provider is None else name,
                item,
                provider
            )
            for (_rank, name, item, provider) in result
        ]

        if not self._results:
            self["header"].setText(
                "No matches for '%s'" % self._term
            )
        else:
            self["header"].setText(
                "%d result%s for '%s'" % (
                    len(self._results),
                    "" if len(self._results) == 1 else "s",
                    self._term
                )
            )

        self._sel = 0
        self["list"].setEntries(
            [label for (label, _item, _provider) in self._results],
            self._sel
        )

    # -------------------------------------------------------------

    # Up/Down wrap round (there can be many results).
    def _up(self):
        if self._results:
            self._sel = (self._sel - 1) % len(self._results)
            self["list"].setSelection(self._sel)

    def _down(self):
        if self._results:
            self._sel = (self._sel + 1) % len(self._results)
            self["list"].setSelection(self._sel)

    def _activate_selected(self):
        """Opens the Watch/Add to Favourites menu for the selected
        result — matches Movies/Series browsing and the Favourites
        list, which both already work this way. _menu_choice below is
        where OK's actual effect lives."""

        if not self._results or self._sel >= len(self._results):
            return

        _label, item, provider = self._results[self._sel]

        content_type = "movie" if self._content_type == "movies" else "series"

        item_id = (
            getattr(item, "stream_id", None) if content_type == "movie"
            else getattr(item, "series_id", None)
        )

        already = False
        if item_id is not None:
            try:
                already = FavouritesStore().is_favourite(
                    content_type,
                    getattr(provider, "provider_key", None),
                    item_id
                )
            except Exception:
                already = False

        self.session.openWithCallback(
            lambda choice: self._menu_choice(choice, item, provider),
            _SearchFavouriteChoiceMenu,
            getattr(item, "name", ""),
            already
        )

    def _menu_choice(self, choice, item, provider):

        if choice == "watch":

            if self._content_type == "movies":
                # No forwarding needed — E2iPTVPlayer isn't part of
                # the B1/"goto:" system at all; Exit while watching
                # just stops playback and reveals this list again,
                # exactly like playing a movie from the normal Movies
                # browser does.
                play_movie(self.session, provider, item)
            else:
                # Series — bubble this up to whoever opened the chain
                # (ultimately Screen1's on_close_callback) rather than
                # opening MediaBrowser directly here — see this
                # module's docstring for why opening a new screen and
                # closing ancestor screens in the same call turned out
                # to be unsafe. Screen1._handle_child_close is what
                # actually opens MediaBrowser once this reaches it.
                self.close(("activate", "series", provider, item))

            return

        if choice != "favourite" and choice != "unfavourite":
            return  # Cancelled the menu — stay on the results list.

        content_type = "movie" if self._content_type == "movies" else "series"

        try:
            store = FavouritesStore()
            if choice == "favourite":
                store.add(
                    content_type,
                    provider,
                    item,
                    getattr(item, "category_id", None)
                )
            else:
                store.remove(
                    content_type,
                    getattr(provider, "provider_key", None),
                    getattr(item, "stream_id", None)
                    if content_type == "movie"
                    else getattr(item, "series_id", None)
                )
        except Exception as e:
            print("[E2iPTV] Favourites update failed:", e)
