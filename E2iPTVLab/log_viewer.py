# =============================================================================
# E2iPTV — Settings -> Status & Logs -> Logs
#
# open_logs(session, on_done) shows the list of the plugin's logs that
# actually exist (right-hand panel, side_menu.py) with when each was last
# written and its size. OK opens LogViewer on it; Exit from the viewer
# comes back to the list, Exit from the list calls on_done().
#
# LogViewer: full-screen, the plugin's dark style, opens at the newest
# lines. Up/Down scroll a few lines, Left/Right or CH+/CH- a page,
# RED clears the log (after Yes/No), GREEN re-reads it, EXIT goes back.
# =============================================================================

import glob
import os
import time
import textwrap

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from enigma import eActionMap

from .paths import DATA

# Friendly names for the logs that matter; anything else found is shown
# by its file name (tidied up).
_NAMES = {
    "nightly_refresh.log":   "Nightly EPG refresh",
    "scheduler_start.log":   "EPG scheduler start",
    "e2iptv_recordings.log": "Recordings",
    "e2iptv_music.log": "Music",
    "e2iptv_live.log":       "Live TV reconnects",
    "epg_fill.log":          "Guide gap filling",
    "e2iptv_rules.log":      "Recording rules",
    "e2iptv_xtream.log":     "Provider connection",
    "e2iptv_catchup.log":    "Catch-up",
    "e2iptv_slow.log":       "Slow loading",
    "moved_folders.log":     "Data folder moves",
}
# Shown first, in this order; the rest follow alphabetically.
_ORDER = list(_NAMES)

# Only logs the current plugin writes are listed — files left behind by
# older versions (e.g. an old picon download log) are ignored. The
# debug-only ones appear just when debug logging is switched on.
_KNOWN = set(_NAMES) | set(
    "e2iptv_%s.log" % n for n in (
        "b8", "backend", "epg_key", "favourites_debug", "jump_debug",
        "livetv_nav_debug", "livetv_tune_debug", "match", "nav", "play",
        "player_keys", "rec", "rec_indicator_debug", "record_key",
        "recording", "recplay_seek", "resolver", "resume", "scheduler",
        "screen2"))

_MAX_READ = 256 * 1024      # only the tail of a big log is read
_WRAP = 140                 # characters per screen line
_ROWS = 28                  # screen lines in the viewer
_STEP = 5                   # Up/Down scroll this many lines

# Raw key codes (the image can swallow the named colour/channel actions).
_KEY_RED, _KEY_GREEN = 398, 399
_KEY_CH_UP, _KEY_CH_DOWN = 402, 403
_KEY_PAGE_UP, _KEY_PAGE_DOWN = 104, 109


def _friendly(path):
    base = os.path.basename(path)
    old = base.endswith(".old")
    if old:
        base = base[:-4]
    name = _NAMES.get(base)
    if not name:
        name = base.replace("e2iptv_", "").replace(".log", "")
        name = name.replace("_", " ").strip().capitalize()
    return name + (" (previous)" if old else "")


def _size_text(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%d KB" % (n // 1024)
    return "%.1f MB" % (n / 1048576.0)


def find_logs():
    """[(path, friendly name, mtime, size)] for every log that exists."""
    paths = set()
    for pattern in ("/tmp/e2iptv_*.log", "/tmp/e2iptv_*.log.old",
                    DATA + "/epg/*.log", DATA + "/*.log",
                    DATA + "/recordings/*.log"):
        paths.update(glob.glob(pattern))
    out = []
    for p in paths:
        base = os.path.basename(p)
        if (base[:-4] if base.endswith(".old") else base) not in _KNOWN:
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append((p, _friendly(p), st.st_mtime, st.st_size))

    def key(item):
        base = os.path.basename(item[0])
        old = base.endswith(".old")
        base = base[:-4] if old else base
        rank = _ORDER.index(base) if base in _ORDER else len(_ORDER)
        return (rank, item[1].lower(), old)

    out.sort(key=key)
    return out


def open_logs(session, on_done=None, current=0):
    from .side_menu import SideMenu
    logs = find_logs()
    if not logs:
        options = [("No logs found", None)]
    else:
        options = [(name, path,
                    "%s · %s" % (time.strftime("%d %b %H:%M", time.localtime(mt)),
                                 _size_text(sz)))
                   for path, name, mt, sz in logs]

    def chosen(choice):
        if not choice or not choice[1]:
            if on_done:
                on_done()
            return
        idx = [o[1] for o in options].index(choice[1])
        session.openWithCallback(
            lambda *_: open_logs(session, on_done, idx),
            LogViewer, choice[1], choice[0])

    from .side_menu import SettingsMenu
    session.openWithCallback(chosen, SettingsMenu, "Logs", options,
                             detail="OK to view", current=current,
                             section="Status & Logs")


class LogViewer(Screen):

    skin = (
        '<screen name="E2iPTVLogViewer" position="0,0" size="1920,1080"'
        ' flags="wfNoBorder" backgroundColor="#0d1117">'
        '<eLabel position="0,0" size="1920,1080" backgroundColor="#0d1117" zPosition="1"/>'
        '<widget name="title" position="40,20" size="1840,50" font="E2iRegular;32"'
        ' foregroundColor="#FFFFFF" backgroundColor="#0d1117" halign="left"'
        ' valign="center" noWrap="1" zPosition="3"/>'
        '<widget name="info" position="40,70" size="1840,34" font="E2iRegular;22"'
        ' foregroundColor="#aaddff" backgroundColor="#0d1117" halign="left"'
        ' valign="center" noWrap="1" zPosition="3"/>'
        '<eLabel position="40,110" size="1840,2" backgroundColor="E2iHi" zPosition="2"/>'
        '<widget name="text" position="40,120" size="1840,900" font="E2iRegular;24"'
        ' foregroundColor="#e6e6e6" backgroundColor="#0d1117" halign="left"'
        ' valign="top" zPosition="3"/>'
        '<widget name="keys" position="40,1030" size="1840,40" font="E2iRegular;22"'
        ' foregroundColor="#7f8fa6" backgroundColor="#0d1117" halign="left"'
        ' valign="center" noWrap="1" zPosition="3"/>'
        '</screen>')

    def __init__(self, session, path, name):
        Screen.__init__(self, session)
        self._path = path
        self._name = name
        self._lines = []
        self._top = 0
        self["title"] = Label(name)
        self["info"] = Label("")
        self["text"] = Label("")
        self["keys"] = Label(
            "▲▼ scroll      ◄ ►  /  CH+ CH-  page      "
            "RED  clear log      GREEN  refresh      EXIT  back")
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self.close,
                "cancel": self.close,
                "up":     lambda: self._scroll(-_STEP),
                "down":   lambda: self._scroll(_STEP),
                "left":   lambda: self._scroll(-_ROWS),
                "right":  lambda: self._scroll(_ROWS),
            }, -1)
        self._raw = self._on_raw_key
        eActionMap.getInstance().bindAction("", -0x7FFFFFFF, self._raw)
        self.onClose.append(self._unbind)
        self.onLayoutFinish.append(self._load)

    # -- keys -----------------------------------------------------------

    def _unbind(self):
        try:
            eActionMap.getInstance().unbindAction("", self._raw)
        except Exception:
            pass

    def _on_raw_key(self, key, flag):
        if key not in (_KEY_RED, _KEY_GREEN, _KEY_CH_UP, _KEY_CH_DOWN,
                       _KEY_PAGE_UP, _KEY_PAGE_DOWN):
            return 0
        try:
            if self.session.current_dialog is not self:
                return 0
        except Exception:
            return 0
        if key in (_KEY_CH_UP, _KEY_PAGE_UP):
            if flag in (0, 2):
                self._scroll(-_ROWS)
        elif key in (_KEY_CH_DOWN, _KEY_PAGE_DOWN):
            if flag in (0, 2):
                self._scroll(_ROWS)
        elif flag == 0:
            from twisted.internet import reactor
            if key == _KEY_RED:
                reactor.callLater(0, self._ask_clear)
            else:
                reactor.callLater(0, self._load)
        return 1

    # -- content --------------------------------------------------------

    def _load(self):
        raw = ""
        cut = False
        try:
            size = os.path.getsize(self._path)
            with open(self._path, "rb") as f:
                if size > _MAX_READ:
                    f.seek(size - _MAX_READ)
                    cut = True
                raw = f.read().decode("utf-8", "replace")
            if cut:
                raw = raw.split("\n", 1)[-1]   # drop the partial first line
        except Exception as e:
            raw = "(could not read this log: %s)" % e
        lines = []
        for line in raw.rstrip("\n").split("\n"):
            line = line.replace("\t", "    ").rstrip("\r")
            lines.extend(textwrap.wrap(line, _WRAP, subsequent_indent="    ")
                         or [""])
        if raw.strip() == "":
            lines = ["(this log is empty)"]
        self._lines = lines
        self._cut = cut
        self._top = max(0, len(lines) - _ROWS)    # newest at the bottom
        self._paint()

    def _scroll(self, delta):
        if not self._lines:
            return
        last = max(0, len(self._lines) - _ROWS)
        self._top = max(0, min(last, self._top + delta))
        self._paint()

    def _paint(self):
        n = len(self._lines)
        shown = self._lines[self._top:self._top + _ROWS]
        self["text"].setText("\n".join(shown))
        try:
            st = os.stat(self._path)
            when = time.strftime("%d %b %H:%M", time.localtime(st.st_mtime))
            size = _size_text(st.st_size)
        except OSError:
            when, size = "-", "-"
        self["info"].setText(
            "%s      %s      last written %s      lines %d–%d of %d%s"
            % (self._path, size, when,
               self._top + 1 if n else 0, min(n, self._top + _ROWS), n,
               "  (newest part only)" if getattr(self, "_cut", False) else ""))

    # -- clear ----------------------------------------------------------

    def _ask_clear(self):
        try:
            from .screen2 import E2iMessageBox as Box
        except Exception:
            from Screens.MessageBox import MessageBox as Box
        self.session.openWithCallback(
            self._clear, Box,
            "Clear the %s log?\n\nIts contents will be deleted." % self._name,
            Box.TYPE_YESNO, default=False)

    def _clear(self, yes):
        if not yes:
            return
        try:
            open(self._path, "w").close()
        except Exception as e:
            print("[E2iPTV] clear log failed:", e)
        self._load()


# =============================================================================
# Settings -> Status & Logs: the small menu, and the EPG refresh summary
# =============================================================================

def open_status_logs(session, current=0):
    from .side_menu import SideMenu, SettingsMenu

    def chosen(choice):
        if not choice:
            return
        if choice[1] == "summary":
            session.openWithCallback(
                lambda *_: open_status_logs(session, 0), EpgSummaryViewer)
        else:
            open_logs(session, lambda: open_status_logs(session, 1))

    session.openWithCallback(
        chosen, SettingsMenu, "Status & Logs",
        [("EPG refresh summary", "summary"), ("Logs", "logs")],
        current=current, section="Status & Logs")


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _when(ts):
    return time.strftime("%a %d %b %H:%M", time.localtime(ts))


def _dur(secs):
    secs = int(secs or 0)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%dh %02dm" % (h, m)
    if m:
        return "%dm %02ds" % (m, s)
    return "%ds" % s


def _next_run_text():
    import json
    hour = 20
    try:
        with open(DATA + "/cache/app_settings.json") as f:
            hour = int(json.load(f).get("epg_fetch_hour", 20))
    except Exception:
        pass
    hour = max(0, min(23, hour))
    now = time.localtime()
    day = "today" if now.tm_hour < hour else "tomorrow"
    return "%02d:00 %s" % (hour, day)


def summary_lines():
    import json
    path = DATA + "/epg/last_summary.json"
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return ["No summary yet.",
                "",
                "One appears after the next guide refresh: tonight's automatic",
                "run (%s) or Settings -> EPG -> Refresh All." % _next_run_text()]

    out = []
    run = data.get("run") or {}
    started = run.get("started")
    if run.get("finished"):
        status = {"SUCCESS": "All OK",
                  "COMPLETED WITH ERRORS": "Finished, with some problems",
                  "FAILED": "Failed"}.get(run.get("status"), run.get("status"))
        out.append("Last refresh:  %s  (took %s)      Result:  %s"
                   % (_when(started), _dur(run["finished"] - started), status))
        if run.get("fatal"):
            out.append("    Stopped early: %s" % run["fatal"])
    elif started:
        if _pid_alive(run.get("pid")):
            out.append("Refresh running now - started %s (%s ago)"
                       % (_when(started), _dur(time.time() - started)))
            cur = run.get("current")
            if cur:
                line = "Now on provider %d of %d:  %s" % (
                    cur.get("index", 0), cur.get("total", 0), cur.get("name", "?"))
                if cur.get("channels"):
                    line += "  -  about %d of %d channels done" % (
                        min(cur.get("done", 0), cur["channels"]), cur["channels"])
                if cur.get("started"):
                    line += "  (on it for %s)" % _dur(time.time() - cur["started"])
                out.append(line)
            elif run.get("done"):
                out.append("%d provider%s finished so far" % (
                    run["done"], "" if run["done"] == 1 else "s"))
        else:
            out.append("Last refresh started %s but did not finish "
                       "(box restarted or the job was stopped)" % _when(started))
    out.append("Next automatic refresh:  %s" % _next_run_text())
    out.append("")

    try:
        with open(DATA + "/epg/fill/index.json") as f:
            fx = json.load(f)
        fill, fill_when, fill_count = fx.get("providers") or {}, fx.get("when"), len(fx.get("keys") or [])
    except Exception:
        fill, fill_when, fill_count = {}, None, 0
    provs = data.get("providers") or {}
    if not provs:
        out.append("No provider results yet.")
    order = {"FAILED": 0, "PARTIAL": 1, "SKIPPED": 2, "OK": 3}
    for name in sorted(provs, key=lambda n: (order.get(provs[n].get("status"), 9),
                                               n.lower())):
        p = provs[name]
        st = p.get("status", "?")
        head = "%-8s  %s" % (st, name)
        if p.get("when") and started and p["when"] < started:
            # not part of the latest refresh (yet) - say how old it is
            head += "      (from an earlier refresh, %s)" % _when(p["when"])
        out.append(head)
        if st != "SKIPPED":
            ch = p.get("channels", 0)
            bits = ["%d channels" % ch, "%d updated" % p.get("refreshed", 0)]
            if p.get("no_data"):
                bits.append("%d have no guide from the provider" % p["no_data"])
            if p.get("failed"):
                bits.append("%d failed" % p["failed"])
            if p.get("timeout"):
                bits.append("%d timed out" % p["timeout"])
            bits.append("took %s" % _dur(p.get("seconds")))
            out.append("            " + ",  ".join(bits))
        for text, count in p.get("reasons") or []:
            out.append("            - %s%s" % (text, ("   (x%d)" % count) if count > 1 else ""))
        f = fill.get(name)
        if f and (f.get("other") or f.get("file")):
            out.append("            + gaps filled: %d from other providers, %d from the guide file"
                       % (f.get("other", 0), f.get("file", 0)))
        out.append("")
    if fill_when:
        out.append("Guide gaps last filled %s - %d channels have a stand-in guide "
                   "(Settings -> EPG -> Fill Guide Gaps)." % (_when(fill_when), fill_count))
    return out


class EpgSummaryViewer(LogViewer):
    """Same screen as the log viewer, filled from epg/last_summary.json
    instead of a log file. Nothing to clear; GREEN re-reads it."""

    def __init__(self, session):
        LogViewer.__init__(self, session, DATA + "/epg/last_summary.json",
                           "EPG refresh summary")
        self["keys"].setText(
            "▲▼ scroll      ◄ ►  /  CH+ CH-  page      "
            "GREEN  refresh      EXIT  back")

    def _load(self):
        try:
            raw = summary_lines()
        except Exception as e:
            raw = ["(could not read the summary: %s)" % e]
        lines = []
        for line in raw:
            lines.extend(textwrap.wrap(line, _WRAP, subsequent_indent="              ")
                         or [""])
        self._lines = lines
        self._cut = False
        self._top = 0                       # summary reads from the top
        self._paint()

    def _paint(self):
        shown = self._lines[self._top:self._top + _ROWS]
        self["text"].setText("\n".join(shown))
        self["info"].setText("Each provider's latest result, problems first")

    def _ask_clear(self):
        pass
