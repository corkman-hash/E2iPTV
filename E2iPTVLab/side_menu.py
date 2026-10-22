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
BG = "E2iRail"          # the icon rail colour
HIGHLIGHT = "E2iHi"


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
        '<widget name="sel" position="15,3000" size="528,%d" cornerRadius="14"'
        ' backgroundColor="%s" zPosition="2"/>'
        '%s</screen>'
        % (PANEL_X, PANEL_W, BG, PANEL_W, BG, BG, BG, ROW_H, HIGHLIGHT, rows))


class SideMenu(Screen):

    skin = _skin()
    ROW_X = 25
    SEL_X = 15
    MORE_DN_DX = 258

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
                self["detail"].instance.move(ePoint(self.ROW_X, 3000))
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
                    w.instance.move(ePoint(self.ROW_X, self._y0 + i * ROW_H))
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
                    r.instance.move(ePoint(self.ROW_X, self._y0 + i * ROW_H))
                    r.instance.setForegroundColor(
                        gRGB(0x1a3a5c if idx == self._sel else 0xaaddff))
                else:
                    r.setText("")
                    r.instance.move(ePoint(25, 3000))
            except Exception:
                pass
        try:
            self["sel"].instance.move(
                ePoint(self.SEL_X, self._y0 + (self._sel - self._top) * ROW_H))
        except Exception:
            pass
        try:
            # Both hints sit on one line under the last row.
            y = self._y0 + MAX_ROWS * ROW_H + 4
            self["more_up"].instance.move(
                ePoint(self.ROW_X, y if self._top > 0 else 3000))
            self["more_dn"].instance.move(
                ePoint(self.ROW_X + self.MORE_DN_DX, y if self._top + MAX_ROWS < n else 3000))
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


# =============================================================================
# Lab: the same menu drawn inside the Settings two-panel layout - used for
# the Settings pages that are menus (Playback, Status & Logs, Logs, EPG
# Fill Gaps From), so they look like the rest of Settings rather than a
# side pop-up over the TV. The left panel shows the Settings sections
# with the open one highlighted (grey).
# =============================================================================

SETTINGS_SECTIONS = ["Status & Logs", "Playlists", "EPG", "Appearance",
                     "Playback", "Parental Controls", "Other", "About"]
_SR_X, _SR_W = 670, 1210            # right panel
_SET_ROW_W = _SR_W - 60


def _settings_skin():
    rows = "".join(
        '<widget name="t%d" position="690,3000" size="%d,%d"'
        ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
        ' halign="left" valign="center" noWrap="1" transparent="1" zPosition="3"/>'
        % (i, _SET_ROW_W, ROW_H) for i in range(MAX_ROWS))
    rows += "".join(
        '<widget name="r%d" position="690,3000" size="%d,%d"'
        ' font="E2iRegular;24" foregroundColor="#aaddff"'
        ' halign="right" valign="center" noWrap="1" transparent="1" zPosition="3"/>'
        % (i, _SET_ROW_W - 10, ROW_H) for i in range(MAX_ROWS))
    rows += (
        '<widget name="more_up" position="690,3000" size="300,30"'
        ' font="E2iRegular;22" foregroundColor="#7f8fa6" backgroundColor="E2iPanel"'
        ' halign="left" valign="center" transparent="1" zPosition="3"/>'
        '<widget name="more_dn" position="690,3000" size="300,30"'
        ' font="E2iRegular;22" foregroundColor="#7f8fa6" backgroundColor="E2iPanel"'
        ' halign="right" valign="center" transparent="1" zPosition="3"/>')
    secs = "".join(
        '<widget name="sec%d" position="50,%d" size="560,100"'
        ' font="E2iRegular;30" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
        ' halign="left" valign="center" transparent="1" zPosition="3"/>'
        % (i, 100 + i * 100) for i in range(len(SETTINGS_SECTIONS)))
    return (
        '<screen name="E2iLabSettingsMenu" position="0,0" size="1920,1080"'
        ' flags="wfNoBorder" backgroundColor="E2iBg">'
        '<eLabel position="0,0" size="1920,1080" backgroundColor="E2iBg" zPosition="0"/>'
        '<eLabel position="40,90" size="600,960" backgroundColor="E2iPanel"'
        ' cornerRadius="18" zPosition="1"/>'
        '<eLabel position="%d,90" size="%d,960" backgroundColor="E2iPanel"'
        ' cornerRadius="18" zPosition="1"/>'
        '<widget name="set_title" position="60,20" size="560,60" font="E2iRegular;36"'
        ' foregroundColor="#FFFFFF" backgroundColor="E2iBg" halign="left"'
        ' valign="center" transparent="1" zPosition="3"/>'
        '<widget name="set_clock" position="1560,20" size="320,60" font="E2iRegular;32"'
        ' foregroundColor="E2iDim" backgroundColor="E2iBg" halign="right"'
        ' valign="center" transparent="1" zPosition="3"/>'
        '<widget name="sec_hl" position="50,3000" size="560,100" cornerRadius="14"'
        ' backgroundColor="E2iMuted" zPosition="2"/>'
        '%s'
        '<widget name="title" position="690,20" size="840,60"'
        ' font="E2iRegular;36" foregroundColor="#FFFFFF"'
        ' backgroundColor="E2iBg" halign="left" valign="center"'
        ' noWrap="1" transparent="1" zPosition="3"/>'
        '<widget name="detail" position="700,110" size="%d,40"'
        ' font="E2iRegular;26" foregroundColor="E2iDim"'
        ' backgroundColor="E2iPanel" halign="left" valign="center"'
        ' noWrap="1" transparent="1" zPosition="3"/>'
        '<widget name="sel" position="680,3000" size="%d,%d" cornerRadius="14"'
        ' backgroundColor="%s" zPosition="2"/>'
        '%s</screen>'
        % (_SR_X, _SR_W, secs, _SET_ROW_W, _SR_W - 20, ROW_H, HIGHLIGHT, rows))


class SettingsMenu(SideMenu):
    """SideMenu inside the Settings layout. `section` is the Settings
    section it belongs to (highlighted on the left)."""

    skin = _settings_skin()
    ROW_X = 690
    SEL_X = 680
    MORE_DN_DX = _SET_ROW_W - 300

    def __init__(self, session, title, options, detail="", current=0,
                 value_only=False, cancel_value=None, section=None):
        SideMenu.__init__(self, session, title, options, detail, current,
                          value_only, cancel_value)
        # Rows start a little lower, inside the right panel.
        self._y0 = 170 if detail else 110
        self["title"].setText(title or "")
        self["detail"].setText(detail or "")
        self["set_title"] = Label("Settings")
        self["set_clock"] = Label("")
        self["sec_hl"] = Label("")
        self._section = section
        for i, name in enumerate(SETTINGS_SECTIONS):
            self["sec%d" % i] = Label("  " + name)
        self.onLayoutFinish.append(self._settings_side)

    def _hide_empty_detail(self):
        if not self._has_detail:
            try:
                self["detail"].instance.move(ePoint(700, 3000))
            except Exception:
                pass

    def _settings_side(self):
        import time as _t
        try:
            self["set_clock"].setText(_t.strftime("%a %d %b  %H:%M"))
        except Exception:
            pass
        if self._section in SETTINGS_SECTIONS:
            i = SETTINGS_SECTIONS.index(self._section)
            try:
                self["sec_hl"].instance.move(ePoint(50, 100 + i * 100))
            except Exception:
                pass
