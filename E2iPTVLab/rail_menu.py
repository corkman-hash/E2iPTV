# =============================================================================
# E2iPTV — Menu button: the icon rail, slid in over the left edge
#
# The plugin's own screen (not the image's ChoiceBox), drawn exactly like
# the expanded icon rail — same icons, labels, colours, sizes and row
# positions, all taken from b1common.py — and covering only the rail's
# own strip, so whatever is playing stays visible to its right.
#
# Up/Down move (wrapping round at the ends, like the rail), OK closes with
# (label, action), Exit or Right closes with None.
# =============================================================================

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import ePoint, gRGB

from .b1common import (B1_ICONS, B1_LABELS, B1_ACTIONS, B1_Y_POS,
                       B1_ICON_PATH, B1_RAIL_COLOUR, B1_EXPANDED_W,
                       B1_TILE_COLOUR)

ITEMS = [(label, B1_ACTIONS[label]) for label in B1_LABELS]


def _skin():
    xml = (
        '<screen name="E2iPTVRailMenu" position="0,0" size="%d,1080"'
        ' flags="wfNoBorder" backgroundColor="#%s">'
        '<eLabel position="0,0" size="%d,1080" backgroundColor="#%s" zPosition="1"/>'
        '<widget name="title" position="0,10" size="%d,80"'
        ' font="E2iRegular;60" foregroundColor="#FFFFFF"'
        ' backgroundColor="#%s" valign="center" halign="center" zPosition="4"/>'
        '<widget name="tile" position="5,3000" size="%d,66" cornerRadius="14"'
        ' backgroundColor="#%s" zPosition="3"/>'
        % (B1_EXPANDED_W, B1_RAIL_COLOUR, B1_EXPANDED_W, B1_RAIL_COLOUR,
           B1_EXPANDED_W, B1_RAIL_COLOUR, B1_EXPANDED_W - 10, B1_TILE_COLOUR))
    for i, (icon, y) in enumerate(zip(B1_ICONS, B1_Y_POS)):
        xml += (
            '<widget name="ic%d" position="22,%d" size="56,56"'
            ' pixmap="%s%s" zPosition="4" alphatest="blend"/>'
            '<widget name="lbl%d" position="100,%d" size="190,56"'
            ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
            ' backgroundColor="#%s" valign="center" halign="left"'
            ' transparent="1" zPosition="4"/>'
            % (i, y + 22, B1_ICON_PATH, icon, i, y + 22, B1_RAIL_COLOUR))
    return xml + '</screen>'


class RailMenu(Screen):

    skin = _skin()

    def __init__(self, session, current=None):
        Screen.__init__(self, session)
        self._sel = 0
        if current is not None:
            # A label ("EPG Grid") or an action ("live" -> Live TV).
            self._sel = next((i for i, (l, a) in enumerate(ITEMS)
                              if current in (l, a)), 0)
        self["title"] = Label("E2iPTV Lab")
        self["tile"] = Label("")
        for i, (label, _a) in enumerate(ITEMS):
            self["ic%d" % i] = Pixmap()
            self["lbl%d" % i] = Label(label)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self._confirm,
                "cancel": self._dismiss,
                "right":  self._dismiss,
                "up":     self._up,
                "down":   self._down,
            }, -1)
        self.onLayoutFinish.append(self._paint)

    # Wrap round, like the rail itself.
    def _up(self):
        self._sel = (self._sel - 1) % len(ITEMS)
        self._paint()

    def _down(self):
        self._sel = (self._sel + 1) % len(ITEMS)
        self._paint()

    def _paint(self):
        try:
            self["tile"].instance.move(ePoint(5, B1_Y_POS[self._sel] + 22 - 5))
        except Exception:
            pass
        for i in range(len(ITEMS)):
            try:
                self["lbl%d" % i].instance.setForegroundColor(
                    gRGB(0x000000 if i == self._sel else 0xffffff))
            except Exception:
                pass

    def _confirm(self):
        self.close(ITEMS[self._sel])

    def _dismiss(self):
        self.close(None)
