# =============================================================================
# E2iPTV - a plain message panel in the plugin's own look
#
# Used instead of the image's MessageBox where the text can be long (e.g.
# Settings -> Playlists -> Test Connection): the box's skin gives
# MessageBox a fixed size, so longer text was cut off. This panel grows to
# fit the text (up to nearly the full screen height). OK or Exit closes.
# =============================================================================

import textwrap

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label

W = 1300
LINE_H = 42
CHARS = 66          # about this many characters fit on a line at this width
MAX_LINES = 20


def _wrap(text):
    out = []
    for para in (text or "").split("\n"):
        if not para.strip():
            out.append("")
            continue
        indent = len(para) - len(para.lstrip())
        out.extend(textwrap.wrap(para, CHARS, subsequent_indent=" " * (indent + 3))
                   or [""])
    return out


class InfoBox(Screen):

    def __init__(self, session, title, text, ok=True):
        Screen.__init__(self, session)
        lines = _wrap(text)[:MAX_LINES]
        body_h = max(1, len(lines)) * LINE_H
        h = 60 + 60 + 20 + body_h + 80
        x = (1920 - W) // 2
        y = max(20, (1080 - h) // 2)
        accent = "E2iHi" if ok else "#ff6666"
        # Like the box's own message box: a see-through rounded panel with
        # a round icon on the left ("i" when all is well, "!" when not).
        from . import theme as _T
        panel = "#20%s" % _T.hexs("rail")
        tx = 170
        self.skin = (
            '<screen name="E2iInfoBox" position="%d,%d" size="%d,%d"'
            ' flags="wfNoBorder" backgroundColor="#ff000000">'
            '<eLabel position="0,0" size="%d,%d" backgroundColor="%s"'
            ' cornerRadius="24" zPosition="0"/>'
            '<widget name="icon" position="70,58" size="66,66" font="E2iRegular;40"'
            ' foregroundColor="%s" backgroundColor="%s" borderWidth="3"'
            ' borderColor="%s" cornerRadius="33" halign="center" valign="center"'
            ' zPosition="2"/>'
            '<widget name="title" position="%d,60" size="%d,60" font="E2iRegular;34"'
            ' foregroundColor="%s" backgroundColor="%s" transparent="1"'
            ' valign="center" noWrap="1" zPosition="2"/>'
            '<widget name="text" position="%d,140" size="%d,%d" font="E2iRegular;28"'
            ' foregroundColor="#ffffff" backgroundColor="%s" transparent="1"'
            ' valign="top" zPosition="2"/>'
            '<widget name="hint" position="40,%d" size="%d,40" font="E2iRegular;24"'
            ' foregroundColor="E2iDim" backgroundColor="%s" transparent="1"'
            ' halign="right" valign="center" zPosition="2"/>'
            '</screen>'
        ) % (x, y, W, h, W, h, panel,
             accent, panel, accent,
             tx, W - tx - 40, accent, panel,
             tx, W - tx - 40, body_h, panel,
             h - 60, W - 80, panel)
        self["icon"] = Label("i" if ok else "!")
        self["title"] = Label(title or "")
        self["text"] = Label("\n".join(lines))
        self["hint"] = Label("OK / Exit to close")
        self["actions"] = ActionMap(["OkCancelActions"],
                                    {"ok": self.close, "cancel": self.close}, -1)


# =============================================================================
# E2iBox - the plugin's Yes/No and message pop-up, in the same look as
# InfoBox above. Used everywhere the plugin used the image's MessageBox,
# so all its pop-ups match. Takes the same main arguments as MessageBox
# (text, type, timeout, default, timeout_default) and closes the same
# way: True / False for a Yes/No question, True for a message.
# =============================================================================

TYPE_YESNO, TYPE_INFO, TYPE_WARNING, TYPE_ERROR = 0, 1, 2, 3
ROW_H = 66


class E2iBox(Screen):

    def __init__(self, session, text="", type=TYPE_YESNO, timeout=-1,
                 close_on_any_key=False, default=True, enable_input=True,
                 msgBoxID=None, picon=None, simple=False, list=None,
                 timeout_default=None, title=None, windowTitle=None, **kwargs):
        Screen.__init__(self, session)
        self._type = type
        self._timeout_default = timeout_default
        self._yesno = (type == TYPE_YESNO)
        self._choices = [("Yes", True), ("No", False)] if self._yesno else []
        if list:
            # MessageBox's own list of (label, value) choices
            self._choices = [(str(c[0]), c[1]) for c in list]
            self._yesno = True
        self._sel = 0
        if self._yesno and not list and not default:
            self._sel = 1
        lines = _wrap(text)[:MAX_LINES]
        body_h = max(1, len(lines)) * LINE_H
        rows_h = len(self._choices) * ROW_H
        h = 60 + body_h + (30 + rows_h if rows_h else 0) + (100 if not rows_h else 60)
        x = (1920 - W) // 2
        y = max(20, (1080 - h) // 2)
        from . import theme as _T
        panel = "#20%s" % _T.hexs("rail")
        bad = type in (TYPE_ERROR, TYPE_WARNING)
        accent = "#ff6666" if bad else "E2iHi"
        icon = "?" if self._yesno else ("!" if bad else "i")
        tx = 170
        rows_y = 60 + body_h + 30
        rows = "".join(
            '<widget name="c%d" position="%d,%d" size="%d,%d" font="E2iRegular;30"'
            ' foregroundColor="#ffffff" backgroundColor="%s" transparent="1"'
            ' halign="left" valign="center" zPosition="3"/>'
            % (i, tx, rows_y + i * ROW_H, W - tx - 60, ROW_H, panel)
            for i in range(len(self._choices)))
        self.skin = (
            '<screen name="E2iBox" position="%d,%d" size="%d,%d"'
            ' flags="wfNoBorder" backgroundColor="#ff000000">'
            '<eLabel position="0,0" size="%d,%d" backgroundColor="%s"'
            ' cornerRadius="24" zPosition="0"/>'
            '<widget name="icon" position="70,48" size="66,66" font="E2iRegular;40"'
            ' foregroundColor="%s" backgroundColor="%s" borderWidth="3"'
            ' borderColor="%s" cornerRadius="33" halign="center" valign="center"'
            ' zPosition="2"/>'
            '<widget name="text" position="%d,60" size="%d,%d" font="E2iRegular;30"'
            ' foregroundColor="#ffffff" backgroundColor="%s" transparent="1"'
            ' valign="top" zPosition="2"/>'
            '<widget name="sel" position="%d,3000" size="%d,%d" backgroundColor="E2iHi"'
            ' cornerRadius="14" zPosition="2"/>'
            '%s'
            '<widget name="hint" position="40,%d" size="%d,40" font="E2iRegular;24"'
            ' foregroundColor="E2iDim" backgroundColor="%s" transparent="1"'
            ' halign="right" valign="center" zPosition="2"/>'
            '</screen>'
        ) % (x, y, W, h, W, h, panel,
             accent, panel, accent,
             tx, W - tx - 40, body_h + 4, panel,
             tx - 20, W - tx - 30, ROW_H,
             rows,
             h - 60, W - 80, panel)
        self._rows_y = rows_y
        self._tx = tx
        self["icon"] = Label(icon)
        self["text"] = Label("\n".join(lines))
        self["sel"] = Label("")
        self["hint"] = Label("" if self._yesno else "OK / Exit to close")
        for i, (label, _v) in enumerate(self._choices):
            self["c%d" % i] = Label("  " + label)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {"ok": self._ok, "cancel": self._cancel,
             "up": lambda: self._move(-1), "down": lambda: self._move(1)}, -1)
        self._timer = None
        if timeout and timeout > 0:
            from enigma import eTimer
            self._timer = eTimer()
            self._timer.callback.append(self._timed_out)
            self._timer.start(int(timeout * 1000), True)
            self.onClose.append(self._timer.stop)
        self.onLayoutFinish.append(self._paint)

    def _paint(self):
        from enigma import ePoint, gRGB
        for i in range(len(self._choices)):
            try:
                self["c%d" % i].instance.setForegroundColor(
                    gRGB(0x000000 if i == self._sel else 0xffffff))
            except Exception:
                pass
        try:
            if self._choices:
                self["sel"].instance.move(
                    ePoint(self._tx - 20, self._rows_y + self._sel * ROW_H))
        except Exception:
            pass

    def _move(self, d):
        if self._choices:
            self._sel = (self._sel + d) % len(self._choices)
            self._paint()

    def _ok(self):
        if self._choices:
            self.close(self._choices[self._sel][1])
        else:
            self.close(True)

    def _cancel(self):
        self.close(False)

    def _timed_out(self):
        if self._timeout_default is not None:
            self.close(self._timeout_default)
        else:
            self._ok()
