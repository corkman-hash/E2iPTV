# =============================================================================
# E2iPTV — shared pop-up menu: a panel down the right-hand side
#
# Same position, width, colours, title, row height and highlight as the
# EPG grid's OK menu (screen3.py smenu_*) and the Settings sidebar, so
# every pop-up looks part of the plugin. Only the right-hand strip is
# covered: whatever you're acting on stays visible to its left.
#
#   session.openWithCallback(cb, SideMenu, "Title",
#                            [("Play", "play"), ("Delete", "delete")],
#                            detail="BBC One  ·  08 Oct 19:28")
#
# Up/Down move (wrapping round), OK closes with the chosen (label, value)
# tuple, Exit or Left closes with None.
#
# value_only=True closes with just the value (cancel -> cancel_value),
# for call sites written for the older single-value menus.
# Long lists scroll: only VISIBLE rows are drawn at once, with a
# "▲ more" / "▼ more" hint when there is more above or below.
# =============================================================================

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from enigma import ePoint, gRGB

PANEL_X = 1352          # as the grid's SMENU_X / Settings' OV_X
PANEL_W = 568
ROW_H = 66
MAX_ROWS = 12           # rows drawn at once; longer lists scroll
BG = "#0d1117"
HIGHLIGHT = "#d0e8ff"


def _skin():
    rows = "".join(
        '<widget name="t%d" position="25,3000" size="508,%d"'
        ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
        ' halign="left" valign="center" noWrap="1" transparent="1" zPosition="3"/>'
        % (i, ROW_H) for i in range(MAX_ROWS))
    # Optional right-hand note per row: options given as
    # (label, value, note) show the note small and right-aligned.
    rows += "".join(
        '<widget name="r%d" position="25,3000" size="500,%d"'
        ' font="E2iRegular;22" foregroundColor="#aaddff"'
        ' halign="right" valign="center" noWrap="1" transparent="1" zPosition="3"/>'
        % (i, ROW_H) for i in range(MAX_ROWS))
    rows += (
        '<widget name="more_up" position="25,3000" size="250,30"'
        ' font="E2iRegular;22" foregroundColor="#7f8fa6" backgroundColor="%s"'
        ' halign="left" valign="center" zPosition="3"/>'
        '<widget name="more_dn" position="283,3000" size="250,30"'
        ' font="E2iRegular;22" foregroundColor="#7f8fa6" backgroundColor="%s"'
        ' halign="right" valign="center" zPosition="3"/>' % (BG, BG))
    return (
        '<screen name="E2iPTVSideMenu" position="%d,0" size="%d,1080"'
        ' flags="wfNoBorder" backgroundColor="%s">'
        '<eLabel position="0,0" size="%d,1080" backgroundColor="%s" zPosition="1"/>'
        '<widget name="title" position="20,30" size="528,60"'
        ' font="E2iRegular;32" foregroundColor="#FFFFFF"'
        ' backgroundColor="%s" halign="left" valign="center"'
        ' noWrap="1" zPosition="3"/>'
        '<widget name="detail" position="20,90" size="528,36"'
        ' font="E2iRegular;24" foregroundColor="#aaddff"'
        ' backgroundColor="%s" halign="left" valign="center"'
        ' noWrap="1" zPosition="3"/>'
        '<widget name="sel" position="15,3000" size="528,%d"'
        ' backgroundColor="%s" zPosition="2"/>'
        '%s</screen>'
        % (PANEL_X, PANEL_W, BG, PANEL_W, BG, BG, BG, ROW_H, HIGHLIGHT, rows))


class SideMenu(Screen):

    skin = _skin()

    def __init__(self, session, title, options, detail="", current=0,
                 value_only=False, cancel_value=None):
        Screen.__init__(self, session)
        self._options = list(options)
        self._value_only = value_only
        self._cancel_value = cancel_value
        self._sel = max(0, min(current, len(self._options) - 1))
        self._top = 0
        # Rows start under the title, or under the detail line if any.
        self._y0 = 140 if detail else 110
        self["title"] = Label("  " + (title or ""))
        self["detail"] = Label(("  " + detail) if detail else "")
        self["sel"] = Label("")
        self["more_up"] = Label("▲ more")
        self["more_dn"] = Label("▼ more")
        for i in range(MAX_ROWS):
            self["t%d" % i] = Label("")
            self["r%d" % i] = Label("")
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self._ok,
                "cancel": self._cancel,
                "left":   self._cancel,
                "up":     self._up,
                "down":   self._down,
            }, -1)
        self._has_detail = bool(detail)
        self.onLayoutFinish.append(self._hide_empty_detail)
        self.onLayoutFinish.append(self._paint)

    def _hide_empty_detail(self):
        # An empty detail line would still sit over the first row
        # (rows start higher without one) and cover part of the
        # highlight, leaving a sliver at its left edge — move it away.
        if not self._has_detail:
            try:
                self["detail"].instance.move(ePoint(20, 3000))
            except Exception:
                pass

    def _paint(self):
        n = len(self._options)
        # Keep the highlighted row inside the visible window.
        if self._sel < self._top:
            self._top = self._sel
        elif self._sel >= self._top + MAX_ROWS:
            self._top = self._sel - MAX_ROWS + 1
        self._top = max(0, min(self._top, max(0, n - MAX_ROWS)))
        for i in range(MAX_ROWS):
            idx = self._top + i
            try:
                w = self["t%d" % i]
                if idx < n:
                    w.setText("  " + str(self._options[idx][0]))
                    w.instance.move(ePoint(25, self._y0 + i * ROW_H))
                    w.instance.setForegroundColor(
                        gRGB(0x000000 if idx == self._sel else 0xffffff))
                else:
                    w.setText("")
                    w.instance.move(ePoint(25, 3000))
            except Exception:
                pass
            try:
                r = self["r%d" % i]
                note = (self._options[idx][2]
                        if idx < n and len(self._options[idx]) > 2 else "")
                if note:
                    r.setText(str(note) + "  ")
                    r.instance.move(ePoint(25, self._y0 + i * ROW_H))
                    r.instance.setForegroundColor(
                        gRGB(0x1a3a5c if idx == self._sel else 0xaaddff))
                else:
                    r.setText("")
                    r.instance.move(ePoint(25, 3000))
            except Exception:
                pass
        try:
            self["sel"].instance.move(
                ePoint(15, self._y0 + (self._sel - self._top) * ROW_H))
        except Exception:
            pass
        try:
            # Both hints sit on one line under the last row.
            y = self._y0 + MAX_ROWS * ROW_H + 4
            self["more_up"].instance.move(
                ePoint(25, y if self._top > 0 else 3000))
            self["more_dn"].instance.move(
                ePoint(283, y if self._top + MAX_ROWS < n else 3000))
        except Exception:
            pass

    def _up(self):
        if self._options:
            self._sel = (self._sel - 1) % len(self._options)
            self._paint()

    def _down(self):
        if self._options:
            self._sel = (self._sel + 1) % len(self._options)
            self._paint()

    def _ok(self):
        if not self._options:
            return self._cancel()
        choice = self._options[self._sel]
        self.close(choice[1] if self._value_only else choice)

    def _cancel(self):
        self.close(self._cancel_value if self._value_only else None)
