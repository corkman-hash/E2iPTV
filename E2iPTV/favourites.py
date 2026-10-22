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

from .paths import HDD, LOG  # data folder / debug logs
from .paths import DATA  # E2iPTV_Data folder
import json
import os

FAVOURITES_PATH = DATA + "/cache/favourites.json"


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

        self._entries.append({
            "content_type": content_type,
            "provider_key": provider_key,
            "item_id": item_id,
            "category_id": category_id,
            "name": getattr(item, "name", "") or "",
        })

        self._save()

        return True

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
    gFont, RT_HALIGN_LEFT, RT_VALIGN_CENTER, ePoint, gRGB
)
import threading

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
                    backcolor=0x0a4aa8, backcolor_sel=0x0a4aa8)])
                continue
            bg = 0xd0e8ff if i == sel else 0x0a4aa8
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

class FavouritesListScreen(B1Common, Screen):
    """The single Favourites list — movies and series mixed together,
    newest first (see FavouritesStore.all()). OK resolves the entry
    back to a real object: a movie offers Watch/Remove from
    Favourites; a series jumps straight into it exactly like normal
    browsing — MediaBrowser's jump_to_series (see media_browser.py),
    not a special "favourites view". Resolution happens in a
    background thread (this project's usual pattern — see
    media_browser.py's B3 loader for the same shape) since it's a real
    network fetch, not instant.

    B1Common integration matches MediaBrowser's own: this screen is
    always opened directly from Screen2's rail (see open_favourites
    below), the same top-level position MediaBrowser is in when first
    opened — a single flat list, no nested panes to step back through
    first, so Left always opens the real Screen2 straight away, same
    as Exit being disabled here (< is the only way, matching every
    other B1Common screen's own convention)."""

    def __init__(self, session):
        Screen.__init__(self, session)

        self._entries = FavouritesStore().all()
        self._sel = 0
        self._resolving = False
        self._resolve_result = None

        self._b1_init(expanded=False)

        content_x = B1_COLLAPSED_W
        content_w = 1920 - content_x

        self.skin = (
            '<screen name="FavouritesListScreen" position="0,0"'
            ' size="1920,1080" flags="wfNoBorder">'
            '<eLabel position="0,0" size="1920,1080"'
            ' backgroundColor="#0a4aa8" zPosition="0"/>'
            '<eLabel position="0,0" size="%d,1080"'
            ' backgroundColor="#%s" zPosition="1"/>'
            % (B1_COLLAPSED_W, B1_RAIL_COLOUR) +
            self._b1_skin_xml() +
            '<widget name="header" position="%d,30" size="%d,50"'
            ' font="E2iRegular;34" foregroundColor="#1a98ff"'
            ' halign="left" valign="center" transparent="1" zPosition="4"/>'
            % (content_x + 20, content_w - 40) +
            '<widget name="list" position="%d,100" size="%d,900"'
            ' transparent="1" scrollbarMode="showOnDemand" zPosition="4"/>'
            % (content_x, content_w) +
            '</screen>'
        )

        self["header"] = Label(
            "Favourites" if self._entries else
            "No favourites yet — add some from Movies or Series"
        )
        self["list"] = _FavouritesList(self._labels())

        self._b1_register_widgets()

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self._activate_selected,
                "cancel": self._on_cancel,
                "up":     self._up,
                "down":   self._down,
                "left":   self._on_left,
            }, -1)

        self._resolve_timer = eTimer()
        self._resolve_timer.callback.append(self._poll_resolve)

    def _on_cancel(self):
        # Exit is deliberately disabled here — < is the only way to
        # leave/navigate back, same convention as every other
        # B1Common-integrated screen (MediaBrowser, Screen1's
        # ChannelBrowser).
        pass

    def _on_left(self):
        # Always opens the real Screen2 straight away — this screen has
        # no nested panes of its own to step back through first, and is
        # always the top-level screen in its own chain (opened directly
        # from Screen2's rail — see open_favourites), same position
        # MediaBrowser's own _on_left is in once nothing local is left.
        from .screen2 import Screen2
        self.session.openWithCallback(
            self._handle_child_close, Screen2, self
        )

    def _handle_child_close(self, result=None):
        """Callback for the real Screen2 this screen opens via
        _on_left. Resolves the shared "goto:<action>" close protocol
        (see B1Common._b1_select / MediaBrowser._handle_child_close) —
        Recordings/Settings are handled entirely inside that Screen2
        itself (it never closes for those), so the only things that
        ever reach here are leaving to Live TV, Exit, Search, Series/
        Movies, or a normal close with nothing chosen."""

        if not result or not str(result).startswith("goto:"):
            # Closed normally (e.g. pressed < again from the bare
            # rail) — nothing to do, this screen is still exactly as
            # it was underneath.
            return

        # Every "goto:<action>" this screen doesn't itself satisfy
        # (all of them — Favourites has nothing further to offer once
        # you've chosen to leave it) forwards to whoever opened this
        # screen, same reasoning as MediaBrowser's identical method:
        # this screen isn't Live TV/Search/Series/Movies/Exit's to
        # satisfy, and needs to bubble up however many levels exist
        # until it reaches Screen1, the only place that actually acts
        # on these.
        self.close(result)

    def get_mirror_b2(self):
        """Standard content-screen method — see MediaBrowser's
        identical method. Unlike search.py's SearchResultsScreen
        (results are B3-channel-shaped, with nothing sensible to show
        in B2's simple text-row list), Favourites' own entries ARE
        just simple text rows — the same shape B2 already expects
        (MediaBrowser._b2_flat's (label, data, is_cat) tuples, of
        which Screen2's mirror only ever reads [0], the label) — so
        this mirrors them properly rather than leaving Screen2's pane
        looking wiped/blank while it's open, which is what returning
        empty here actually did: Screen2's B2 tile has no other
        content of its own to fall back on showing."""
        return [(label, None, False) for label in self._labels()], self._sel

    # Opts into Screen2._b2_mirror's clamped-window positioning (see its
    # own comment for the full reasoning): unlike Live TV/Movies/Series,
    # whose B2 categories use B2Common's own shared, always-fixed-row
    # rendering on the real screen itself, this list is a plain,
    # naturally-scrolling widget with no such convention to match — so
    # the mirror clamping to avoid blank rows near the top/bottom is
    # actually correct here, not a mismatch with anything.
    get_mirror_b2_clamped = True

    def get_mirror_b3(self):
        """Standard content-screen method — see MediaBrowser's
        identical method and its own comment for the full reasoning."""
        return [], 0, 0

    def _labels(self):
        provider_names = {
            str(getattr(p, "provider_key", None)): getattr(p, "name", "") or ""
            for _name, p in (state.providers or [])
        }
        return [
            "%s   [%s, %s]" % (
                e.get("name", ""),
                provider_names.get(str(e.get("provider_key")), "?"),
                "Movie" if e.get("content_type") == "movie" else "Series"
            )
            for e in self._entries
        ]

    def _up(self):
        if self._sel > 0:
            self._sel -= 1
            self["list"].setSelection(self._sel)

    def _down(self):
        if self._sel < len(self._entries) - 1:
            self._sel += 1
            self["list"].setSelection(self._sel)

    def _activate_selected(self):

        if self._resolving:
            return  # already working on the previous OK press

        if not self._entries or self._sel >= len(self._entries):
            return

        entry = self._entries[self._sel]

        self._resolving = True
        self["header"].setText(
            "Opening '%s'…" % entry.get("name", "")
        )

        def worker():
            try:
                self._resolve_result = _resolve_favourite(entry)
            except Exception as e:
                # Without this, an unexpected failure anywhere inside
                # _resolve_favourite (not just the network calls it
                # already guards internally) would kill this thread
                # silently — self._resolve_result never gets set, and
                # the screen just sits on "Opening '...'" forever with
                # no error at all, which looks exactly like it simply
                # doesn't work rather than having actually failed.
                _debug_log("Favourites resolve crashed: %s" % (e,))
                self._resolve_result = (None, None)

        thread = threading.Thread(
            target=worker,
            name="E2iPTV-Favourites-Resolve"
        )
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

        provider, item = result
        entry = self._entries[self._sel]

        if provider is None or item is None:
            self["header"].setText(
                "'%s' is no longer available on this provider" %
                entry.get("name", "")
            )
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
                # No forwarding needed here — same reasoning as
                # search.py's SearchResultsScreen._activate_selected:
                # E2iPTVPlayer isn't part of the B1/"goto:" system,
                # Exit while watching just reveals this list again.
                play_movie(self.session, provider, item)
            else:
                # Series — bubble this up to whoever opened this
                # screen (ultimately Screen1's on_close_callback)
                # rather than opening MediaBrowser directly here — see
                # this module's docstring for why opening a new screen
                # and closing this one afterwards turned out to be
                # unsafe.
                self.close(("activate", "series", provider, item))

            return

        if choice != "unfavourite":
            return  # Cancelled the menu — stay right where we were.

        try:
            FavouritesStore().remove(
                content_type,
                entry.get("provider_key"),
                entry.get("item_id")
            )
        except Exception as e:
            print("[E2iPTV] Favourites remove failed:", e)
            return

        self._entries = FavouritesStore().all()
        self._sel = min(self._sel, max(0, len(self._entries) - 1))
        self["list"].setEntries(self._labels(), self._sel)
        self["header"].setText(
            "Favourites" if self._entries else
            "No favourites yet — add some from Movies or Series"
        )


def open_favourites(session, on_close_callback):
    """Entry point — call this from B1's "favourites" action.
    on_close_callback is what finally acts on an ("activate", ...)
    result once it bubbles back up — same reasoning as search.py's
    open_search."""
    session.openWithCallback(on_close_callback, FavouritesListScreen)
