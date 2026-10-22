# =============================================================================
# E2iPTV — Common B1 Icon Rail
#
# One shared B1 implementation for all screens that display the icon rail.
#
# The owning screen supplies:
#   - its own screen skin
#   - its own B2/content layout
#   - _b1_select(index) to handle a selected rail item
#
# This module owns the common B1 rail data, widgets and behaviour.
# =============================================================================

from . import theme as _T
from .paths import HDD  # data folder / debug logs
from .paths import DATA  # E2iPTV_Data folder
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import ePoint, eSize, gRGB
from .state import state


B1_ICON_PATH = (
    "/usr/lib/enigma2/python/Plugins/Extensions/"
    "E2iPTVLab/assets/transPNG/"
)

B1_RAIL_COLOUR = _T.hexs("rail")

B1_COLLAPSED_W = 100
B1_EXPANDED_W  = 300

# All B1 visual properties live here.  Screens do not duplicate them.
B1_LABEL_FONT_SIZE = 28
B1_TITLE_FONT_SIZE = 80
B1_TILE_H = 66
B1_TILE_COLOUR = _T.hexs("hi")
B1_TEXT_COLOUR = "FFFFFF"
B1_SELECTED_TEXT_COLOUR = "000000"

B1_ICONS = [
    "Pi7_magnify-2.png",
    "Pi7_television-3.png",
    "Pi7_television-guide.png",
    "Pi7_movie-open-outline-2.png",
    "Pi7_view-agenda-outline-3.png",
    "Pi7_music.png",
    "Pi7_harddisk-2.png",
    "Pi7_bookmark-outline-3.png",
    "Pi7_cog-outline-2.png",
    "Pi7_exit-close.png",
]

B1_LABELS = [
    "Search",
    "Live TV",
    "EPG Grid",
    "Movies",
    "Series",
    "Music",
    "Recordings",
    "Favourites",
    "Settings",
    "Exit / Close",
]

# Ten items now (Music added after Series): same first and last rows as
# before (156 / 948), 88 apart instead of 99.
B1_Y_POS = [156 + 88 * i for i in range(10)]

# Internal action names, decoupled from the display label text — every
# screen's dispatch works off these, not the label string, so renaming
# a label never has to touch dispatch logic.
B1_ACTIONS = {
    "Search":       "search",
    "Live TV":      "live",
    # Live TV and EPG Grid share the "live" hand-off to Live TV, which
    # then goes full screen (Live TV) or into the grid (EPG Grid) -
    # see state.live_choice, set in _b1_select below.
    "EPG Grid":     "live",
    "Movies":       "movies",
    "Series":       "series",
    "Music":        "music",
    "Recordings":   "recordings",
    "Favourites":   "favourites",
    "Settings":     "settings",
    "Exit / Close": "exit",
}

# Actions that are actually wired up anywhere yet — this is the one
# place that changes as more get built.
B1_WIRED_ACTIONS = ("search", "favourites", "live", "series", "movies", "music", "recordings", "settings", "exit")


# =============================================================================
# B1 "last choice" persistence
#
# Resumable destinations are ones the plugin can open straight into on
# next launch: Live TV (the plugin's default screen), Series and
# Movies (standalone Screens), Recordings and Settings (panels inside
# Screen2, but reachable via the shared dispatch below from any
# rail). Search and Favourites are deliberately excluded — see
# B1_WIRED_ACTIONS above. "Exit / Close" must never be remembered as a
# destination, or choosing it would make the plugin reopen onto its own
# exit option.
# =============================================================================

B1_LAST_CHOICE_PATH = DATA + "/cache/b1_last_choice.json"
B1_RESUMABLE_LABELS = ("Live TV", "EPG Grid", "Series", "Movies", "Recordings", "Settings")
B1_DEFAULT_LABEL = "Live TV"


def save_b1_last_choice(label):
    """Persist `label` as the last real B1 destination, if it's one we
    can actually resume into. Silently does nothing for anything else
    (including Exit / Close) so a stray call can never corrupt this."""

    if label not in B1_RESUMABLE_LABELS:
        return

    import json
    import os

    try:
        os.makedirs(os.path.dirname(B1_LAST_CHOICE_PATH), exist_ok=True)
        with open(B1_LAST_CHOICE_PATH, "w") as f:
            json.dump({"label": label}, f)
    except Exception:
        pass


def load_b1_last_choice():
    """Return the saved B1 destination label, defaulting to Live TV if
    nothing was saved yet or the saved value is no longer valid."""

    import json
    import os

    try:
        if os.path.exists(B1_LAST_CHOICE_PATH):
            data = json.load(open(B1_LAST_CHOICE_PATH))
            label = data.get("label")
            if label in B1_RESUMABLE_LABELS:
                return label
    except Exception:
        pass

    return B1_DEFAULT_LABEL


class B1Common(object):

    # ------------------------------------------------------------------
    # B1 initial state
    # ------------------------------------------------------------------

    def _b1_init(self, expanded=False):
        self._b1_sel = getattr(state, "b1_sel", 0)
        self._b1_expanded = bool(expanded)

        if self._b1_sel < 0:
            self._b1_sel = 0

        if self._b1_sel >= len(B1_LABELS):
            self._b1_sel = len(B1_LABELS) - 1

        state.b1_sel = self._b1_sel

    # ------------------------------------------------------------------
    # B1 skin
    # ------------------------------------------------------------------

    def _b1_skin_xml(self):
        xml = ""

        for i, (icon, y) in enumerate(zip(B1_ICONS, B1_Y_POS)):

            xml += (
                '<widget name="ic%d" position="22,%d" size="56,56"'
                ' pixmap="%s%s" zPosition="4" alphatest="blend"/>'
                % (
                    i,
                    y + 22,
                    B1_ICON_PATH,
                    icon
                )
            )

            xml += (
                '<widget name="b1_lbl%d" position="100,%d" size="190,56"'
                ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
                ' backgroundColor="#%s" valign="center" halign="left"'
                ' transparent="1" zPosition="4"/>'
                % (
                    i,
                    y + 22,
                    B1_RAIL_COLOUR
                )
            )

        xml += (
            '<widget name="b1_title" position="0,10" size="300,80"'
            ' font="E2iRegular;60" foregroundColor="#FFFFFF"'
            ' backgroundColor="#%s" valign="center" halign="center"'
            ' zPosition="4"/>'
            % B1_RAIL_COLOUR
        )

        xml += (
            '<widget name="b1_tile" position="5,3000" cornerRadius="14"'
            ' size="290,66" backgroundColor="E2iHi"'
            ' zPosition="3"/>'
        )

        return xml

    # ------------------------------------------------------------------
    # B1 widgets
    # ------------------------------------------------------------------

    def _b1_register_widgets(self):
        for i in range(len(B1_LABELS)):
            self["ic%d" % i] = Pixmap()
            self["b1_lbl%d" % i] = Label(B1_LABELS[i])

        self["b1_title"] = Label("E2iPTV Lab")
        self["b1_tile"] = Label("")

        # Initial geometry/visibility must be applied after the
        # Enigma2 screen layout has completed.
        self.onLayoutFinish.append(
            self._b1_apply_visual_state
        )

    # ------------------------------------------------------------------
    # B1 visual state
    # ------------------------------------------------------------------

    def _b1_apply_visual_state(self):

        expanded = bool(getattr(self, "_b1_expanded", False))

        # Labels only exist visually while the rail is expanded.
        for i in range(len(B1_LABELS)):
            try:
                if expanded:
                    self["b1_lbl%d" % i].show()
                else:
                    self["b1_lbl%d" % i].hide()
            except Exception:
                pass

        # Title width follows the rail state.
        try:
            self["b1_title"].instance.resize(
                eSize(
                    B1_EXPANDED_W if expanded else B1_COLLAPSED_W,
                    80
                )
            )
        except Exception:
            pass

        # Selection tile is only visible while B1 has focus.
        try:
            if expanded:
                self["b1_tile"].instance.resize(
                    eSize(
                        B1_EXPANDED_W - 10,
                        66
                    )
                )
                self["b1_tile"].show()
            else:
                self["b1_tile"].hide()
        except Exception:
            pass

        self._b1_refresh()

    # ------------------------------------------------------------------
    # B1 selection
    # ------------------------------------------------------------------

    def _b1_refresh(self):

        try:
            y = B1_Y_POS[self._b1_sel] + 22 - 5

            self["b1_tile"].instance.move(
                ePoint(5, y)
            )

        except Exception:
            pass

        for i in range(len(B1_LABELS)):

            try:
                col = (
                    gRGB(0x000000)
                    if i == self._b1_sel
                    else gRGB(0xffffff)
                )

                self["b1_lbl%d" % i].instance.setForegroundColor(
                    col
                )

            except Exception:
                pass

    # Up/Down wrap round: Up on the first item goes to the last (Exit),
    # Down on the last goes back to the first (Search).

    def _b1_up(self):

        self._b1_sel = (self._b1_sel - 1) % len(B1_LABELS)
        state.b1_sel = self._b1_sel
        self._b1_refresh()

    def _b1_down(self):

        self._b1_sel = (self._b1_sel + 1) % len(B1_LABELS)
        state.b1_sel = self._b1_sel
        self._b1_refresh()

    # ------------------------------------------------------------------
    # B1 expand / collapse
    # ------------------------------------------------------------------

    def _b1_expand(self):

        self._b1_expanded = True
        self._b1_apply_visual_state()

    def _b1_collapse(self):

        self._b1_expanded = False
        self._b1_apply_visual_state()

    def _b1_toggle(self):

        if self._b1_expanded:
            self._b1_collapse()
        else:
            self._b1_expand()

    # ------------------------------------------------------------------
    # B1 OK dispatch — the actual shared behaviour
    #
    # A screen does NOT write its own version of "what does each rail
    # item do" any more. It configures itself instead:
    #
    #   self._b1_own_action = "series"
    #       What destination THIS screen itself represents, if any.
    #       Selecting it again just calls _b1_reselect_self() (default:
    #       collapse the rail and stay put). Leave unset/None for a
    #       screen that isn't itself a destination (e.g. Screen2, which
    #       is a rail overlay on top of Live TV, not "Live TV" itself).
    #
    #   self._b1_local_actions = {
    #       "recordings": self._show_recordings,
    #       "settings":   self._show_b6,
    #   }
    #       Destinations THIS screen can satisfy directly, without
    #       closing itself.
    #
    #   def _b1_before_dispatch(self, action):
    #       Optional. Runs immediately before a local-or-forwarded
    #       action is acted on — e.g. Screen2 uses this to hide
    #       whichever of its own local panels is currently showing if
    #       the new action is a different one.
    #
    #   self._b1_nested_actions = {
    #       "series": self._open_series,
    #   }
    #       Destinations that need a whole other screen opened ON TOP
    #       of this one (not closing self) — e.g. Screen2 opening
    #       Series. The screen's own method is responsible for using
    #       self._handle_child_close as that nested screen's callback,
    #       so whatever it later closes with gets resolved the same
    #       way as everything else.
    #
    # Anything not covered by any of those is forwarded to whoever
    # opened this screen, by closing with a standard "goto:<action>"
    # result. Every screen that can open one of these rail-screens
    # implements _handle_child_close to resolve that result — open the
    # right screen, show the right panel, or forward it further up
    # again. This is the one mechanism every screen shares, so a rail
    # item means the same thing everywhere instead of being
    # reimplemented per screen.
    # ------------------------------------------------------------------

    def _b1_select(self, index):
        if index < 0 or index >= len(B1_LABELS):
            return

        label = B1_LABELS[index]
        action = B1_ACTIONS.get(label)

        # Live TV and EPG Grid share the "live" hand-off; this tells Live
        # TV which one was picked (Live TV = straight to full screen).
        state.live_choice = ("full" if label == "Live TV" else
                             "grid" if label == "EPG Grid" else None)

        if action is None or action not in B1_WIRED_ACTIONS:
            return

        save_b1_last_choice(label)

        before = getattr(self, "_b1_before_dispatch", None)
        if before:
            before(action)

        if action == getattr(self, "_b1_own_action", None):
            self._b1_reselect_self()
            return

        local = getattr(self, "_b1_local_actions", None) or {}
        if action in local:
            local[action]()
            return

        nested = getattr(self, "_b1_nested_actions", None) or {}
        if action in nested:
            nested[action]()
            return

        self.close("goto:%s" % action)

    def _b1_reselect_self(self):
        """Selecting the rail item for the screen you're already on.
        Default: just collapse the rail back down. Override for
        anything screen-specific (Series also resets its own layout)."""
        self._b1_collapse()

    def _b1_ok(self):

        if not self._b1_expanded:
            return

        self._b1_select(self._b1_sel)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def _b1_label(self):

        if 0 <= self._b1_sel < len(B1_LABELS):
            return B1_LABELS[self._b1_sel]

        return ""

    def _b1_is(self, label):
        return self._b1_label() == label
