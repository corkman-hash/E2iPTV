# =============================================================================
# E2iPTV Player
# Minimal blank screen — video plays with no infobar, no clock.
# Cancel/Stop/Exit to close.
# =============================================================================

from . import theme as _T
from .info_box import E2iBox as _E2iBox
from .paths import HDD, LOG  # data folder / debug logs
from .bg import run_now  # user-facing jobs skip the busy worker pool
from .paths import DATA  # E2iPTV_Data folder
from Screens.Screen import Screen
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.InfoBarGenerics import InfoBarSubtitleSupport
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.MultiContent import MultiContentEntryText
from Components.GUIComponent import GUIComponent
from Tools.LoadPixmap import LoadPixmap
from enigma import (
    eServiceReference, eTimer, ePoint, eSize, gRGB,
    eListbox, eListboxPythonMultiContent, gFont,
    RT_HALIGN_LEFT, RT_VALIGN_CENTER, iPlayableService, eActionMap
)
import time as _time
import os
import json
from twisted.internet import reactor
from .state import state, _nav_debug


def _log_any_key(context, action):
    # Diagnostic only, no longer bound anywhere — left here since the
    # raw-keycode fix below (_KEY_STOP) was found using exactly this,
    # and it may be useful again for identifying some other
    # unrecognised button in future. Returning 0 leaves the action to
    # be processed completely normally afterwards; call
    # eActionMap.getInstance().bindAction("", -0x7FFFFFFF, _log_any_key)
    # to re-enable, and unbindAction the same way to remove it again.
    try:
        with open(LOG("e2iptv_player_keys.log"), "a") as f:
            f.write("%s context=%r action=%r\n" % (
                _time.strftime("%H:%M:%S"), context, action))
    except Exception:
        pass
    return 0


# Raw Linux input keycode for the physical Stop/"X" button — confirmed
# via _log_any_key's own logging (see the diagnostic that identified
# this). keymap.xml on this box never translates it into any named
# action in these screens' contexts at all (hence the box's own
# "unmapped key" indicator appearing instead of anything happening) —
# neither "stop" nor "leavePlayer" could ever have caught it, since
# both only exist after that translation happens. Binding directly to
# the raw code via eActionMap (the same low-level mechanism the
# diagnostic used) bypasses the missing translation entirely.
_KEY_STOP = 128

def _make_key_stop_handler(stop_method_ref):
    def _handler(context, action):
        if context == _KEY_STOP:
            stop_method_ref()
        return 0
    return _handler

from .backend.epg import Epg
from .recording.status import TriggerType


# =============================================================================
# Recording resume-position store
# =============================================================================
# A small standalone JSON cache, keyed by absolute recording path, tracking
# where playback last stopped so RecordingPlayer can offer to resume rather
# than always starting from the beginning. Deliberately independent of
# Enigma2's own native resume mechanism (the CUT_TYPE=3 "last play position"
# marker in a recording's .cuts sidecar file) rather than trying to read/
# write that binary format ourselves — this plugin's own playback state
# doesn't need to be understood by the native Movie Player, and a plain JSON
# file is far lower-risk to get right than hand-rolling eCueSheet's cut
# format on a box that's already given us plenty of surprises this project.
_RESUME_STORE_PATH = DATA + "/cache/recording_resume.json"


def _load_resume_store():
    try:
        with open(_RESUME_STORE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_resume_store(store):
    try:
        os.makedirs(os.path.dirname(_RESUME_STORE_PATH), exist_ok=True)
        tmp_path = _RESUME_STORE_PATH + ".tmp"
        # Write-then-replace rather than writing the real path directly —
        # os.replace() is atomic, so a crash/power-loss mid-write can never
        # leave a half-written, unparseable resume store behind.
        with open(tmp_path, "w") as f:
            json.dump(store, f)
        os.replace(tmp_path, _RESUME_STORE_PATH)
    except Exception:
        pass


def _get_resume_pts(path):
    entry = _load_resume_store().get(path)
    return entry.get("pos_pts") if entry else None


def _set_resume_pts(path, pos_pts, len_pts):
    store = _load_resume_store()
    store[path] = {"pos_pts": pos_pts, "len_pts": len_pts, "ts": _time.time()}
    _save_resume_store(store)


def _resume_log(msg):
    """Recording resume saves/jumps (always on, small: /tmp/e2iptv_resume.log)."""
    try:
        with open(LOG("e2iptv_resume.log"), "a") as f:
            f.write("%s %s\n" % (_time.strftime("%d/%m %H:%M:%S"), msg))
    except Exception:
        pass


def _clear_resume_pts(path):
    store = _load_resume_store()
    if path in store:
        del store[path]
        _save_resume_store(store)


# -----------------------------------------------------------------------------
# Watched tracking — deliberately a separate store from resume above,
# not a flag added to those entries. Resume positions get cleared once
# near the end (see _save_resume_state's near_end check) precisely
# because resuming from the last few seconds is pointless — but that's
# exactly the moment "watched" needs to start being remembered, and it
# needs to persist indefinitely (long after there's no resume position
# left at all), not disappear alongside it.
#
# Keyed by the same stream URL resume already uses — built from
# provider base_url/credentials/episode_id (see build_series_url), so
# it's inherently provider-and-episode-specific already, the same
# reasoning every other provider-scoped store this session ended up
# needing to get right from the start.
# -----------------------------------------------------------------------------

_WATCHED_STORE_PATH = DATA + "/cache/watched.json"

# "Watched" means reached within this many seconds of the end — not
# necessarily the literal last frame, since credits/outros are rarely
# watched. 5 minutes suits typical episode lengths; for anything
# short enough that 5 minutes would be most of the runtime, this
# falls back to a percentage instead (see _is_watched_position).
_WATCHED_TAIL_SECONDS = 5 * 60
_WATCHED_TAIL_PTS = _WATCHED_TAIL_SECONDS * 90000
_WATCHED_TAIL_FRACTION = 0.9  # fallback for short content — see above


def _is_watched_position(pos_pts, len_pts):
    if not pos_pts or not len_pts:
        return False
    tail = min(_WATCHED_TAIL_PTS, int(len_pts * (1 - _WATCHED_TAIL_FRACTION)))
    return pos_pts >= (len_pts - tail)


def _load_watched_store():
    try:
        with open(_WATCHED_STORE_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_watched_store(store):
    try:
        directory = os.path.dirname(_WATCHED_STORE_PATH)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        tmp_path = _WATCHED_STORE_PATH + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(store, f)
        os.replace(tmp_path, _WATCHED_STORE_PATH)
    except Exception:
        pass


def is_watched(path):
    return bool(_load_watched_store().get(path))


def mark_watched(path):
    store = _load_watched_store()
    if not store.get(path):
        store[path] = {"ts": _time.time()}
        _save_watched_store(store)


def clear_watched(path):
    store = _load_watched_store()
    if path in store:
        del store[path]
        _save_watched_store(store)


class SeekOverlay(Screen):
    """Brief "+30s"/"-2:00" feedback popup — confirms a skip key actually
    registered. Sits at y=740 (clear of RecordingPlayer's own bottom info
    bar, which now runs 860-1080) and uses the same dark navy panel style
    as InfoCard/RecordingInfoOverlay for visual consistency."""

    skin = (
        '<screen name="SeekOverlay"'
        ' position="710,740" size="500,90"'
        ' flags="wfNoBorder" zPosition="100">'
        '<eLabel position="0,0" size="500,90"'
        ' backgroundColor="#CC0d1117" zPosition="0"/>'
        '<widget name="text"'
        ' position="0,0" size="500,90"'
        ' font="E2iRegular;34"'
        ' foregroundColor="#FFFFFF"'
        ' backgroundColor="transparent"'
        ' transparent="1"'
        ' halign="center" valign="center"'
        ' zPosition="1"/>'
        '</screen>'
    )

    def __init__(self, session, message, timeout=1000):
        Screen.__init__(self, session)

        self["text"] = Label()
        self["text"].setText(message)

        self._timer = eTimer()
        self._timer.callback.append(self.close)

        self._timeout = timeout
        self.onLayoutFinish.append(self._start)

    def _start(self):
        self._timer.start(self._timeout, True)

class ProgrammeOverlay(Screen):

    skin = (
        '<screen name="ProgrammeOverlay"'
        ' position="0,760" size="1920,220"'
        ' flags="wfNoBorder" zPosition="100">'
        '<eLabel position="0,0" size="1920,220"'
        ' backgroundColor="#C0000000" zPosition="0"/>'
        '<widget name="line1"'
        ' position="20,20" size="1880,44"'
        ' font="E2iRegular;34"'
        ' foregroundColor="#FFFFFF"'
        ' backgroundColor="transparent"'
        ' transparent="1"'
        ' halign="center" valign="center"'
        ' zPosition="1"/>'
        '<widget name="line2"'
        ' position="20,74" size="1880,64"'
        ' font="E2iRegular;46"'
        ' foregroundColor="#FFFFFF"'
        ' backgroundColor="transparent"'
        ' transparent="1"'
        ' halign="center" valign="center"'
        ' zPosition="1"/>'
        '<widget name="line3"'
        ' position="20,160" size="1880,36"'
        ' font="E2iRegular;28"'
        ' foregroundColor="#FFFFFF"'
        ' backgroundColor="transparent"'
        ' transparent="1"'
        ' halign="center" valign="center"'
        ' zPosition="1"/>'
        '</screen>'
    )

    def __init__(self, session, line1, line2="", line3="", timeout=1000):
        Screen.__init__(self, session)

        self["line1"] = Label()
        self["line2"] = Label()
        self["line3"] = Label()

        self["line1"].setText(line1)
        self["line2"].setText(line2)
        self["line3"].setText(line3)

        self._timer = eTimer()
        self._timer.callback.append(self.close)

        self._timeout = timeout
        self.onLayoutFinish.append(self._start)

    def _start(self):
        self._timer.start(self._timeout, True)


class RecordingInfoOverlay(Screen):
    """OK-button info panel for RecordingPlayer — same bottom-bar style
    as the Live TV ProgrammeOverlay above, but deeper (350 vs 220) so it
    has room for a 4th line: the recording's own description, the "info
    data" that's otherwise only visible back in Movie Selection/the
    Recordings browser. Channel name, recording title and a computed
    duration (date, start-end, minutes — the exact same formatting the
    Recordings list itself uses) fill the top three lines."""

    skin = (
        '<screen name="RecordingInfoOverlay"'
        ' position="0,730" size="1920,350"'
        ' flags="wfNoBorder" zPosition="100">'
        '<eLabel position="0,0" size="1920,350"'
        ' backgroundColor="#CC0d1117" zPosition="0"/>'
        '<widget name="line1"'
        ' position="20,20" size="1880,40"'
        ' font="E2iRegular;30" foregroundColor="E2iRail"'
        ' backgroundColor="transparent" transparent="1"'
        ' halign="center" valign="center" zPosition="1"/>'
        '<widget name="line2"'
        ' position="20,68" size="1880,60"'
        ' font="E2iRegular;44" foregroundColor="#FFFFFF"'
        ' backgroundColor="transparent" transparent="1"'
        ' halign="center" valign="center" zPosition="1"/>'
        '<widget name="line3"'
        ' position="20,136" size="1880,36"'
        ' font="E2iRegular;28" foregroundColor="#aaaaaa"'
        ' backgroundColor="transparent" transparent="1"'
        ' halign="center" valign="center" zPosition="1"/>'
        '<widget name="line4"'
        ' position="60,186" size="1800,150"'
        ' font="E2iRegular;24" foregroundColor="#cccccc"'
        ' backgroundColor="transparent" transparent="1"'
        ' halign="left" valign="top" zPosition="1"/>'
        '</screen>'
    )

    def __init__(self, session, line1, line2="", line3="", line4="", timeout=5000):
        Screen.__init__(self, session)

        self["line1"] = Label()
        self["line2"] = Label()
        self["line3"] = Label()
        self["line4"] = Label()

        self["line1"].setText(line1)
        self["line2"].setText(line2)
        self["line3"].setText(line3)
        self["line4"].setText(line4)

        self._timer = eTimer()
        self._timer.callback.append(self.close)

        self._timeout = timeout
        self.onLayoutFinish.append(self._start)

    def _start(self):
        self._timer.start(self._timeout, True)


class InfoCard(Screen):
    """Transparent info card overlay — no ActionMap so keys pass through."""

    skin = (
        '<screen name="InfoCard"'
        ' position="1045,20" size="855,240"'
        ' flags="wfNoBorder" zPosition="101">'
        '<eLabel position="0,0" size="805,240"'
        ' backgroundColor="#CC0d1117" zPosition="0"/>'
        '<widget name="ic_title" position="15,10" size="825,54"'
        ' font="E2iRegular;36" foregroundColor="#ffffff"'
        ' halign="left" valign="center" transparent="1" zPosition="1"/>'
        '<widget name="ic_times" position="15,68" size="825,34"'
        ' font="E2iRegular;26" foregroundColor="E2iRail"'
        ' halign="left" valign="center" transparent="1" zPosition="1"/>'
        '<widget name="ic_desc" position="15,106" size="825,124"'
        ' font="E2iRegular;24" foregroundColor="#cccccc"'
        ' halign="left" valign="top" transparent="1" zPosition="1"/>'
        '</screen>'
    )

    def __init__(self, session):
        Screen.__init__(self, session)
        self["ic_title"] = Label("")
        self["ic_times"] = Label("")
        self["ic_desc"]  = Label("")

    def update(self, title, times, desc):
        self["ic_title"].setText(title)
        self["ic_times"].setText(times)
        self["ic_desc"].setText(desc)


class ChannelBrowser(Screen):
    """Full-schedule EPG view for one channel — opened via OK on a channel row
    in screen 2's pane 1. Pane 1 (x=0-1080) holds the whole EPG timeline —
    same row style/font as screen 2 pane 2, but with real dates on the day
    markers instead of Today/Tomorrow/Yesterday. Pane 2 (x=1080-1200, 120px)
    holds a catchup badge plus a date rail: one row per distinct date the
    schedule has data for, independently scrollable. RIGHT moves focus from
    pane 1 into pane 2, LEFT (or Cancel) moves it back one step at a time —
    pane1 -> pane2 -> closes to screen 2. OK on a date in pane 2 jumps pane
    1's list to that day's first programme and returns focus to pane 1."""

    EPG_ROW_H = 72
    EPG_VISIBLE = 14
    EPG_CENTER_ROW = 6  # "now playing" row always renders here — list scrolls around it
    SMENU_X = 1352       # same position/style as the b5 grid's own action menu

    skin = (
        '<screen name="E2iPTV_PlayerB2" position="0,0" size="1920,1080" flags="wfNoBorder" zPosition="101" backgroundColor="transparent">'
        '<eLabel position="0,0" size="1200,1080" backgroundColor="#300d1117" zPosition="0"/>'
        '<widget name="epg_header" position="10,8" size="1060,46" font="E2iRegular;28" foregroundColor="#ffffff" halign="left" valign="center" transparent="1" zPosition="3"/>'
        '<widget name="epg_catchup" position="1090,4" size="100,54" font="E2iRegular;36" foregroundColor="#aaddff" halign="center" valign="center" transparent="1" zPosition="3"/>'
        '<widget name="line1" position="0,56" size="1080,2" backgroundColor="#ffffff" zPosition="3"/>'
        '<widget name="epg_sel" position="3500,0" size="1068,72" backgroundColor="E2iHi" zPosition="2"/>'
        + ''.join('<widget name="epg_time_%d" position="3500,0" size="90,72" font="E2iRegular;28" foregroundColor="#f5c542" halign="left" valign="center" transparent="1" zPosition="3"/>' % i for i in range(14))
        + ''.join('<widget name="epg_title_%d" position="3500,0" size="950,72" font="E2iRegular;28" foregroundColor="#ffffff" halign="left" valign="center" transparent="1" zPosition="3"/>' % i for i in range(14))
        + '<widget name="date_sel" position="3500,0" size="88,72" backgroundColor="E2iHi" zPosition="2"/>'
        + ''.join('<widget name="date_%d" position="3500,0" size="88,72" font="E2iRegular;22" foregroundColor="#c9d3e0" halign="center" valign="center" transparent="1" zPosition="3"/>' % i for i in range(14))
        + '<widget name="smenu_bg" position="3500,0" size="568,1080" backgroundColor="E2iRail" zPosition="20"/>'
        + '<widget name="smenu_hdr" position="3500,30" size="528,60" font="E2iRegular;32" foregroundColor="#ffffff" backgroundColor="E2iRail" halign="left" valign="center" zPosition="22"/>'
        + '<widget name="smenu_sel" position="3500,110" size="528,66" backgroundColor="E2iHi" zPosition="21"/>'
        + ''.join('<widget name="smenu_t%d" position="3500,110" size="528,66" font="E2iRegular;28" foregroundColor="#ffffff" halign="left" valign="center" transparent="1" zPosition="22"/>' % i for i in range(8))
        + '</screen>'
    )

    def __init__(self, session, provider, channel):
        Screen.__init__(self, session)
        self._provider = provider
        self._channel = channel
        self._rows = []      # flat (kind, payload) list — built once on open
        self._sel = None     # index into self._rows — moves with up/down
        self._dates = []     # (label, row_index) — one entry per distinct date in self._rows
        self._date_sel = None  # index into self._dates — moves with up/down while pane2 focused
        self._pane_focus = 'pane1'  # 'pane1' or 'pane2' — which side responds to up/down/ok
        self._menu_active = False
        self._menu_sel = 0
        self._manual_entry_active = False  # True once "Manual Recording"
        # is selected from the menu — reuses this same panel/widgets to
        # show Date/Start/End fields in place of the normal item list,
        # rather than opening a separate screen (that used to let live
        # video show through around it — see _enter_manual_entry).
        self._manual_field = 0       # 0=date 1=start 2=end
        self._manual_start_pts = 0
        self._manual_end_pts = 0
        self['epg_header'] = Label('')
        self['epg_catchup'] = Label('')
        self['line1'] = Label('')
        self['epg_sel'] = Label('')
        self['date_sel'] = Label('')
        self['smenu_bg'] = Label('')
        self['smenu_hdr'] = Label('')
        self['smenu_sel'] = Label('')
        for i in range(14):
            self['epg_time_%d' % i] = Label('')
            self['epg_title_%d' % i] = Label('')
            self['date_%d' % i] = Label('')
        for i in range(8):
            self['smenu_t%d' % i] = Label('')
        self['actions'] = ActionMap(
            ['OkCancelActions', 'DirectionActions',
             'InfobarActions', 'InfobarInstantRecord'], {
            'cancel': self._cancel, 'ok': self._ok, 'up': self._up,
            'down': self._down, 'left': self._on_left,
            'right': self._on_right,
            # Record key is a second way in to the same Watch/Record/
            # Download menu OK already opens — the Channel Schedule is
            # one of the confirmed recording-trigger points, so it gets
            # its own dedicated button too, behaving identically.
            'record': self._ok, 'instantRecord': self._ok}, -1)
        self.onLayoutFinish.append(self._ready)

        # Periodic redraw so a scheduled (green) recording's tile
        # actually turns red once its start time genuinely arrives and
        # it starts recording, without needing anything else (manual
        # navigation, opening a menu) to happen to force a redraw first
        # — confirmed happening: a recording was genuinely active while
        # its own tile still showed stale green. _draw_epg() only ever
        # ran in response to a specific action before this; nothing
        # previously re-checked colours purely because time had passed.
        self._colour_refresh_timer = eTimer()
        self._colour_refresh_timer.callback.append(self._colour_refresh_tick)
        self._colour_refresh_timer.start(30000, False)
        self.onClose.append(self._on_close_stop_timer)

    def _colour_refresh_tick(self):
        if not self._menu_active and not self._manual_entry_active:
            # Redrawing over an open menu/entry panel would fight its
            # own display — skip this tick rather than interrupt
            # whatever the user is actively doing; the next one 30s
            # later will pick the colours up once they're done.
            self._draw_epg()

    def _on_close_stop_timer(self):
        try:
            self._colour_refresh_timer.stop()
        except Exception:
            pass

    def _noop(self):
        pass

    def _ready(self):
        now = int(_time.time())
        self._rows, anchor = self._build_epg_rows(now)
        self._sel = anchor
        self._dates = self._build_date_list()
        self._date_sel = self._date_index_for_row(anchor) if anchor is not None else (
            0 if self._dates else None)
        self._pane_focus = 'pane1'
        self._draw_epg()

    def _build_date_list(self):
        """One (label, row_index) entry per distinct date found in self._rows
        — row_index points at that date's first event row, which is where
        pane 1 lands when you OK that date in pane 2."""
        dates = []
        last_ymd = None
        for idx, (kind, payload) in enumerate(self._rows):
            if kind != 'event':
                continue
            ymd = self._epg_ymd(payload.start)
            if ymd != last_ymd:
                dates.append((self._epg_date_label(payload.start), idx))
                last_ymd = ymd
        return dates

    def _date_index_for_row(self, row_idx):
        if not self._dates:
            return None
        best = 0
        for i, (label, ridx) in enumerate(self._dates):
            if ridx <= row_idx:
                best = i
            else:
                break
        return best

    def _epg_ymd(self, ts):
        lt = _time.localtime(ts)
        return (lt.tm_year, lt.tm_mon, lt.tm_mday)

    def _epg_date_label(self, ts):
        """Always a real date here (no Today/Tomorrow) — this screen shows
        the full schedule, so relative day names would be ambiguous."""
        return _time.strftime('%a %d %b', _time.localtime(ts))

    def _build_epg_rows(self, now):
        """Returns (rows, anchor) — rows is the flat (kind, payload) list for
        the whole known schedule, anchor is the index of whatever's airing
        right now (or the next upcoming event if nothing's airing)."""
        try:
            events = sorted(state.backend.epg_store.get_for(self._channel), key=lambda e: e.start)
        except Exception:
            events = []
        if not events:
            return [], None

        combined = []
        last_ymd = None
        running_idx = None
        next_idx = None
        for ev in events:
            ymd = self._epg_ymd(ev.start)
            if last_ymd is not None and ymd != last_ymd:
                combined.append(('marker', self._epg_date_label(ev.start)))
            last_ymd = ymd
            combined.append(('event', ev))
            if running_idx is None and ev.start <= now < ev.end:
                running_idx = len(combined) - 1
            if next_idx is None and ev.start >= now:
                next_idx = len(combined) - 1

        anchor = running_idx if running_idx is not None else (
            next_idx if next_idx is not None else len(combined) - 1)
        return combined, anchor

    def _up(self):
        if self._manual_entry_active:
            self._manual_field = (self._manual_field - 1) % 3
            self._manual_refresh()
            return
        if self._menu_active:
            if self._menu_sel > 0:
                self._menu_sel -= 1
                self._menu_refresh()
            return
        if self._pane_focus == 'pane2':
            if self._date_sel is not None and self._date_sel > 0:
                self._date_sel -= 1
                self._draw_epg()
            return
        if self._sel is None:
            return
        i = self._sel - 1
        while i >= 0 and self._rows[i][0] == 'marker':
            i -= 1
        if i >= 0:
            self._sel = i
            self._draw_epg()

    def _down(self):
        if self._manual_entry_active:
            self._manual_field = (self._manual_field + 1) % 3
            self._manual_refresh()
            return
        if self._menu_active:
            items = self._get_ctx_items()
            if self._menu_sel < len(items) - 1:
                self._menu_sel += 1
                self._menu_refresh()
            return
        if self._pane_focus == 'pane2':
            if self._date_sel is not None and self._date_sel < len(self._dates) - 1:
                self._date_sel += 1
                self._draw_epg()
            return
        if self._sel is None:
            return
        i = self._sel + 1
        while i < len(self._rows) and self._rows[i][0] == 'marker':
            i += 1
        if i < len(self._rows):
            self._sel = i
            self._draw_epg()

    def _ok(self):
        if self._manual_entry_active:
            self._manual_confirm()
        elif self._menu_active:
            self._execute_menu()
        elif self._pane_focus == 'pane2':
            self._jump_to_date()
        else:
            self._open_menu()

    def _jump_to_date(self):
        """OK on a date row in pane 2 — scroll pane 1 so that day's first
        programme lands on its centre row, then hand focus back to pane 1
        so up/down immediately walks that day's schedule."""
        if not self._dates or self._date_sel is None:
            return
        label, row_idx = self._dates[self._date_sel]
        self._sel = row_idx
        self._pane_focus = 'pane1'
        self._draw_epg()

    def _cancel(self):
        # Exit/Cancel closes the entire 3-screen chain outright, regardless
        # of depth — this is the top of that chain now (opened directly
        # from Live TV), so it just needs to close itself; screens 2/3
        # each forward this same "exit_all" sentinel up through their own
        # callbacks rather than closing only themselves, which is what
        # used to leave you needing to press Exit once per screen.
        #
        # One deliberate exception: from manual-entry mode, Exit steps
        # back to the normal menu first, same as it would from any
        # other nested state in this app — it does NOT discard straight
        # out to Live TV from two levels deep in one press.
        if self._manual_entry_active:
            self._manual_exit()
            return
        _nav_debug("ChannelBrowser._cancel (screen1) fired — closing exit_all")
        self.close("exit_all")

    def _on_right(self):
        """RIGHT moves focus pane1 -> pane2 (nowhere further to go once
        already in pane2 — that side has no third pane)."""
        if self._manual_entry_active:
            self._manual_adjust(1)
            return
        if self._menu_active:
            return
        if self._pane_focus == 'pane1' and self._dates:
            self._pane_focus = 'pane2'
            self._draw_epg()

    def _on_left(self):
        """LEFT steps back one level at a time: pane2 -> pane1 -> screen 2
        (channel list/preview — picking a different channel there just
        refreshes this screen's display via update_channel() below, it
        doesn't tune anything itself)."""
        if self._manual_entry_active:
            self._manual_adjust(-1)
            return
        if self._menu_active:
            self._hide_menu()
            return
        if self._pane_focus == 'pane2':
            self._pane_focus = 'pane1'
            self._draw_epg()
        else:
            self.session.openWithCallback(
                self._screen2_closed, ChannelBrowser2,
                self._provider, [], self)

    def _screen2_closed(self, result=None):
        if result == 'exit_all':
            self.close('exit_all')

    def update_channel(self, provider, channel):
        """Called by screen 2 (ChannelBrowser2) when a different channel is
        committed there — refreshes this screen's own display to match,
        same as _ready() does on first open. Deliberately does not tune
        anything — only this screen's own Watch/Record action ever does."""
        self._provider = provider
        self._channel = channel
        self._ready()

    def _selected_event(self):
        """The Event at self._sel, or None if the cursor is on a date marker
        (or there's no schedule data at all)."""
        if self._sel is None or not (0 <= self._sel < len(self._rows)):
            return None
        kind, payload = self._rows[self._sel]
        return payload if kind == 'event' else None

    def _get_ctx_items(self):
        """Delegates to the module-level get_ctx_items() — the single
        shared source for this menu's content, also used by the EPG
        grid (screen3.py) and Live TV's instant-record. Custom Record
        (scheduling) is left out everywhere for now — it's deferred
        along with the rest of the scheduling feature."""
        return get_ctx_items(self._provider, self._channel, self._selected_event())

    def _open_menu(self):
        if self._sel is None or self._rows[self._sel][0] == 'marker':
            return  # nothing sensible to do on a date-marker row
        self._menu_active = True
        self._menu_sel = 0
        self['smenu_hdr'].setText('  ' + getattr(self._channel, 'name', ''))
        try:
            self['smenu_bg'].instance.move(ePoint(self.SMENU_X, 0))
            self['smenu_hdr'].instance.move(ePoint(self.SMENU_X + 20, 30))
        except Exception: pass
        self._menu_refresh()

    def _hide_menu(self):
        self._menu_active = False
        try:
            self['smenu_bg'].instance.move(ePoint(3500, 0))
            self['smenu_hdr'].instance.move(ePoint(3500, 0))
            self['smenu_sel'].instance.move(ePoint(3500, 0))
            for i in range(8):
                self['smenu_t%d' % i].instance.move(ePoint(3500, 0))
        except Exception: pass

    def _menu_refresh(self):
        items = self._get_ctx_items()
        x, y0, ih = self.SMENU_X + 20, 110, 66
        for i in range(8):
            try:
                if i < len(items):
                    label, active, action = items[i]
                    y = y0 + i * ih
                    self['smenu_t%d' % i].setText('  ' + label)
                    self['smenu_t%d' % i].instance.move(ePoint(x, y))
                    col = 0x000000 if i == self._menu_sel else (0xffffff if active else 0x666666)
                    self['smenu_t%d' % i].instance.setForegroundColor(gRGB(col))
                else:
                    self['smenu_t%d' % i].instance.move(ePoint(3500, 0))
            except Exception: pass
        try:
            self['smenu_sel'].instance.move(ePoint(x - 5, y0 + self._menu_sel * ih))
        except Exception: pass

    def _enter_manual_entry(self):
        """"Manual Recording" selected from the menu — switches this
        same panel into Date/Start/End entry using its own smenu_t
        widgets, rather than the normal item list. Defaults: the
        highlighted event's own start/end if it has real ones — "real"
        meaning not the synthetic 30-min "No EPG Available" filler
        (see screen3.py's _get_events), which would be a misleading
        default to prefill here of all places. Falls back to "now,
        rounded to the nearest 5 minutes" / "+1 hour" for a synthetic
        tile or no event at all — so just pressing OK immediately here
        reproduces what used to be the separate "Record from Now"
        option, with no extra step needed for that common case."""
        now = int(_time.time())
        ev = self._selected_event()

        default_start = now
        default_end   = now + 3600
        if ev is not None and getattr(ev, 'title', '') != 'No EPG Available':
            if getattr(ev, 'start', 0):
                default_start = ev.start
            if getattr(ev, 'end', 0):
                default_end = ev.end
        else:
            default_start = (now // 300) * 300

        self._manual_start_pts = default_start
        self._manual_end_pts   = default_end
        if self._manual_end_pts <= self._manual_start_pts:
            self._manual_end_pts = self._manual_start_pts + 3600

        self._manual_entry_active = True
        self._manual_field = 0
        self['smenu_hdr'].setText('  Manual Recording')
        self._manual_refresh()

    def _manual_refresh(self):
        x, y0, ih = self.SMENU_X + 20, 110, 66
        rows = [
            "Date    %s" % _time.strftime("%a %d %b", _time.localtime(self._manual_start_pts)),
            "Start   %s" % _time.strftime("%H:%M", _time.localtime(self._manual_start_pts)),
            "End     %s" % _time.strftime("%H:%M", _time.localtime(self._manual_end_pts)),
        ]
        for i in range(8):
            try:
                if i < len(rows):
                    y = y0 + i * ih
                    self['smenu_t%d' % i].setText('  ' + rows[i])
                    self['smenu_t%d' % i].instance.move(ePoint(x, y))
                    col = 0x000000 if i == self._manual_field else 0xffffff
                    self['smenu_t%d' % i].instance.setForegroundColor(gRGB(col))
                else:
                    self['smenu_t%d' % i].instance.move(ePoint(3500, 0))
            except Exception: pass
        try:
            self['smenu_sel'].instance.move(ePoint(x - 5, y0 + self._manual_field * ih))
        except Exception: pass

    def _manual_adjust(self, direction):
        if self._manual_field == 0:
            self._manual_start_pts += direction * 86400
            self._manual_end_pts   += direction * 86400
        elif self._manual_field == 1:
            self._manual_start_pts += direction * 900
        elif self._manual_field == 2:
            self._manual_end_pts += direction * 900
        self._manual_refresh()

    def _manual_confirm(self):
        if self._manual_end_pts <= self._manual_start_pts:
            # End before start makes no sense to schedule — just
            # ignore the press rather than silently create a
            # zero/negative-length rule.
            return

        from .state import state as _state

        manager = getattr(_state, 'recording_manager', None)
        if manager is None:
            self._manual_exit()
            return

        custom_ev = Epg()
        custom_ev.stream_id   = self._channel.stream_id
        custom_ev.title       = self._channel.name
        custom_ev.description = ""
        custom_ev.start       = self._manual_start_pts
        custom_ev.end         = self._manual_end_pts

        rec_path = getattr(_state, 'recording_path', HDD + '/movie/')

        rule = manager.create_rule(
            self._provider, self._channel, custom_ev,
            0, 0, [], self._channel.name, rec_path
        )
        rule.trigger = TriggerType.MANUAL

        manager.add_rule(rule)

        self._manual_entry_active = False
        self._hide_menu()
        # Same missing-refresh gap already found and fixed in
        # _execute_menu for every other action — _hide_menu alone
        # doesn't recompute the schedule's own red/green indicators, so
        # without this the tile only ever appeared to update once
        # something else (e.g. normal up/down scrolling) happened to
        # force a redraw afterward.
        self._draw_epg()

    def _manual_exit(self):
        """Exit/Cancel from manual-entry mode — back to the normal
        menu (not all the way out), same "step back one level" as
        everywhere else in this app."""
        self._manual_entry_active = False
        self._menu_refresh()

    def _execute_menu(self):
        """Delegates to the module-level execute_ctx_action() — the same
        shared executor the EPG grid and Live TV's instant-record use."""
        items = self._get_ctx_items()
        if self._menu_sel >= len(items):
            self._hide_menu()
            return
        label, active, action = items[self._menu_sel]
        if not active:
            return

        if action == 'rec_manual':
            # Deliberately does NOT hide the menu or open a separate
            # screen — switches this same panel into Date/Start/End
            # entry in place, using the same smenu_t widgets the normal
            # item list just used. A standalone screen for this used to
            # let live video show through everywhere outside its own
            # small box, regardless of which screen it was opened from
            # — reusing this panel (already proven to render solidly
            # over whatever this screen itself is opened over) avoids
            # that entirely rather than working around it.
            self._enter_manual_entry()
            return

        self._hide_menu()

        is_tune = action in ('watch_start', 'watch_end', 'play')

        if is_tune:
            # Deliberately does NOT tune here — closes with a "tune" result
            # instead, and the OLD player (which opened this screen, still
            # running underneath, unclosed) does the actual work from its
            # own _browser_closed callback once this screen has genuinely
            # finished closing.
            #
            # Earlier versions tried closing the old player from HERE
            # first (via state.live_player), then this screen second —
            # completed without error, but closing a screen out of normal
            # top-down order (the parent, before the child that was opened
            # from it) left this screen visually stuck open for several
            # seconds before Enigma2's screen stack caught up, which
            # showed as an unwanted flash of this screen before the Main
            # Grid finally appeared. Closing in normal order — this screen
            # first, the old player second, once it's genuinely the
            # active/modal screen again — avoids that entirely.
            _nav_debug(
                "ChannelBrowser._execute_menu: is_tune, closing self with "
                "tune result — action=%r" % (action,)
            )
            self.close((
                'tune', action, self._provider, self._channel,
                self._selected_event()
            ))
            return

        execute_ctx_action(
            self.session, action, self._provider, self._channel,
            self._selected_event(),
            recording_path=state.recording_path,
            rec_start_offset=getattr(state, 'rec_start_offset', 0),
            rec_end_offset=getattr(state, 'rec_end_offset', 0),
            catchup_start_offset=getattr(state, 'catchup_start_offset', 0),
            catchup_end_offset=getattr(state, 'catchup_end_offset', 0),
            epg_store=state.backend.epg_store,
        )
        # Re-render so the schedule's own recording-colour indicators
        # (red/green, via _epg_recording_colour) reflect whatever this
        # action just changed immediately — previously missing entirely
        # here (screen3.py's identical menu already did this, via its
        # own _b5_refresh/_update_info), so e.g. Remove Recording left
        # the tile showing stale green until something else eventually
        # forced a redraw, even though the underlying data was already
        # correct at that point.
        self._draw_epg()

    def _draw_epg(self):
        has_catchup = bool(getattr(self._channel, 'tv_archive', False))
        self['epg_header'].setText('  ' + getattr(self._channel, 'name', ''))
        # Same catch-up glyph/colour as the b3 channel list (↺), in its
        # own fixed badge spot so it's never squeezed out by a long name.
        self['epg_catchup'].setText(u'↺' if has_catchup else '')

        rows, sel, total = self._rows, self._sel, len(self._rows)
        if total <= self.EPG_VISIBLE:
            scroll = 0
        else:
            scroll = max(0, min((sel or 0) - self.EPG_CENTER_ROW, total - self.EPG_VISIBLE))
        try:
            if sel is not None:
                self['epg_sel'].instance.move(ePoint(12, 60 + (sel - scroll) * self.EPG_ROW_H))
            else:
                self['epg_sel'].instance.move(ePoint(3500, 0))
        except Exception: pass
        for row in range(self.EPG_VISIBLE):
            idx, y = scroll + row, 60 + row * self.EPG_ROW_H
            try:
                if idx < total:
                    kind, payload = rows[idx]
                    selected = (idx == sel)
                    text_color = 0x000000 if selected else 0xffffff
                    time_color = 0x000000 if selected else 0xf5c542
                    if kind == 'marker':
                        self['epg_time_%d' % row].setText('')
                        self['epg_title_%d' % row].setText('  — %s —' % payload.upper())
                        marker_color = text_color if selected else 0xf5c542
                        self['epg_title_%d' % row].instance.setForegroundColor(gRGB(marker_color))
                    else:
                        ev = payload
                        # Recording indicator — no separate badge widget
                        # here, so this row's own title text and colour
                        # carry it instead. Screen 1 had no indicator at
                        # all before; this makes it consistent with the
                        # grid and channel tile.
                        rec_status = None
                        if self._provider:
                            for _rk, _rec in state.active_recordings.items():
                                if (_rec.get('provider_id') == self._provider.provider_id
                                        and _rec.get('stream_id') == self._channel.stream_id
                                        and _rec.get('event_start') == ev.start):
                                    rec_status = _rec.get('status')
                                    break
                        if rec_status == 'active':
                            title_text  = u'● REC  ' + ev.title
                            title_color = 0xff4444 if not selected else text_color
                        elif rec_status == 'scheduled':
                            title_text  = u'● SCHEDULED  ' + ev.title
                            title_color = 0x44cc44 if not selected else text_color
                        else:
                            title_text  = ev.title
                            title_color = text_color
                        self['epg_time_%d' % row].setText(
                            '  ' + _time.strftime('%H:%M', _time.localtime(ev.start)))
                        self['epg_title_%d' % row].setText(title_text)
                        self['epg_title_%d' % row].instance.setForegroundColor(gRGB(title_color))
                    self['epg_time_%d' % row].instance.move(ePoint(12, y))
                    self['epg_time_%d' % row].instance.setForegroundColor(gRGB(time_color))
                    self['epg_title_%d' % row].instance.move(ePoint(102, y))
                else:
                    self['epg_time_%d' % row].instance.move(ePoint(3500, 0))
                    self['epg_title_%d' % row].instance.move(ePoint(3500, 0))
            except Exception: pass

        self._draw_date_rail()

    def _draw_date_rail(self):
        """Pane 2's own list — one row per distinct date. Unlike pane 1
        (which only recentres once it has enough rows to fill the view),
        the current/selected date always sits on the centre row here — with
        this list usually being short (a handful of days), pinning it to
        the top whenever it's short would leave it looking off-centre and
        mostly empty. Blank rows above/below simply aren't drawn."""
        dates, dsel, dtotal = self._dates, self._date_sel, len(self._dates)
        pane2_focused = (self._pane_focus == 'pane2')
        dscroll = (dsel if dsel is not None else 0) - self.EPG_CENTER_ROW
        try:
            if dsel is not None and pane2_focused:
                self['date_sel'].instance.move(ePoint(1096,60 + self.EPG_CENTER_ROW * self.EPG_ROW_H))
            else:
                self['date_sel'].instance.move(ePoint(3500, 0))
        except Exception: pass
        for row in range(self.EPG_VISIBLE):
            idx, y = dscroll + row, 60 + row * self.EPG_ROW_H
            try:
                if 0 <= idx < dtotal:
                    label, _ridx = dates[idx]
                    selected = (idx == dsel and pane2_focused)
                    color = 0x000000 if selected else 0xc9d3e0
                    self['date_%d' % row].setText(label.replace(' ', '\n', 1))
                    self['date_%d' % row].instance.move(ePoint(1096,y))
                    self['date_%d' % row].instance.setForegroundColor(gRGB(color))
                else:
                    self['date_%d' % row].instance.move(ePoint(3500, 0))
            except Exception: pass

    def _close(self):
        self.close(None)


class ChannelBrowser2(Screen):
    """Live-player LEFT-from-LiveTV screen 2 of 3.
    Pane 1 (left, x=0-600) starts bare, and gets populated on demand: when OK
    is pressed on a category in screen 3's pane 1, that category's
    'Provider - Category' header and channel/EPG list are pushed here, so
    it's waiting when the user presses RIGHT back to this screen.
    Pane 2 (right, x=600-1200) is still bare, no text yet.
    LEFT opens screen 3. RIGHT (or Cancel) closes back to screen 1."""

    ROW_H = 110
    VISIBLE = 9
    CENTER_ROW = 4  # highlighted row always renders here — list scrolls around it

    EPG_ROW_H = 72       # pane 2 EPG timeline — thinner single-line rows, more fit
    EPG_VISIBLE = 14
    EPG_CENTER_ROW = 6   # "now playing" row always renders here — list scrolls around it

    skin = (
        '<screen name="E2iPTV_LeftScreen2" position="0,0" size="1920,1080" flags="wfNoBorder" zPosition="101" backgroundColor="transparent">'
        '<eLabel position="0,0" size="600,1080" backgroundColor="#300d1117" zPosition="0"/><eLabel position="600,0" size="600,1080" backgroundColor="#300d1117" zPosition="0"/>'
        '<widget name="ch_header" position="10,8" size="580,46" font="E2iRegular;28" foregroundColor="#ffffff" halign="left" valign="center" transparent="1" zPosition="3"/>'
        '<widget name="epg_header" position="610,8" size="580,46" font="E2iRegular;28" foregroundColor="#ffffff" halign="left" valign="center" transparent="1" zPosition="3"/>'
        '<widget name="line1" position="0,56" size="600,2" backgroundColor="#ffffff" zPosition="3"/>'
        '<widget name="line2" position="600,56" size="600,2" backgroundColor="#ffffff" zPosition="3"/>'
        '<widget name="ch_sel" position="3500,0" size="576,108" backgroundColor="E2iHi" zPosition="2"/>'
        + ''.join('<widget name="ch_%d" position="3500,0" size="556,64" font="E2iRegular;28" foregroundColor="#ffffff" halign="left" valign="top" transparent="1" zPosition="3"/>' % i for i in range(9))
        + ''.join('<widget name="ch_prog_%d" position="3500,0" size="556,36" font="E2iRegular;24" foregroundColor="#f5c542" halign="left" valign="center" transparent="1" zPosition="3"/>' % i for i in range(9))
        + '<widget name="epg_sel" position="3500,0" size="576,72" backgroundColor="E2iHi" zPosition="2"/>'
        + ''.join('<widget name="epg_time_%d" position="3500,0" size="90,72" font="E2iRegular;28" foregroundColor="#f5c542" halign="left" valign="center" transparent="1" zPosition="3"/>' % i for i in range(14))
        + ''.join('<widget name="epg_title_%d" position="3500,0" size="480,72" font="E2iRegular;28" foregroundColor="#ffffff" halign="left" valign="center" transparent="1" zPosition="3"/>' % i for i in range(14))
        + '</screen>'
    )

    def __init__(self, session, provider, channels, parent_screen=None):
        Screen.__init__(self, session)
        self._provider = provider
        self._channels = list(channels or [])
        self._sel = 0  # highlighted row — moves with up/down
        self._category = None
        self._load_gen = 0
        self._parent_screen = parent_screen  # screen 1 (ChannelBrowser) —
        # for pushing a committed channel selection back up to it, via
        # update_channel() below, without tuning anything itself
        self['ch_header'] = Label('')
        self['epg_header'] = Label('')
        self['line1'] = Label('')
        self['line2'] = Label('')
        self['ch_sel'] = Label('')
        self['epg_sel'] = Label('')
        for i in range(9):
            self['ch_%d' % i] = Label('')
            self['ch_prog_%d' % i] = Label('')
        for i in range(14):
            self['epg_time_%d' % i] = Label('')
            self['epg_title_%d' % i] = Label('')
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions'], {
            'cancel': self._exit_all, 'ok': self._commit_channel, 'up': self._up,
            'down': self._down, 'left': self._open_next,
            'right': self._commit_channel}, -1)
        self.onLayoutFinish.append(self._ready)

    def _exit_all(self):
        # Closes the whole 3-screen chain outright, from wherever it's
        # called — see ChannelBrowser._cancel's identical comment for the
        # full reasoning (this is the same "exit_all" sentinel, forwarded
        # by _next_closed if screen 3 is what's actually open right now).
        self.close('exit_all')

    def _noop(self):
        pass

    def _commit_channel(self):
        """OK or RIGHT on the highlighted channel: push it up to screen 1
        (ChannelBrowser) via update_channel(), then close back to it —
        does NOT tune anything itself, only refreshes what screen 1 will
        display next time it's shown."""
        if not self._channels:
            return
        ch = self._channels[self._sel]

        # Sync the global browse state (state.open_prov/channels/sel_cat) to
        # match what's actually being committed to here — this is what
        # screen1.py's own B2/B5 browse view and screen3.py's EPG grid read,
        # and until now nothing in this whole screen-1/2/3 overlay chain ever
        # touched it, so switching provider this way left the main EPG grid
        # showing the OLD provider's channels even after the new one started
        # playing. It also silently broke play_channel()'s own channel_idx
        # lookup (searched the stale state.channels for the new channel's
        # stream_id and never found it), so this fixes channel Up/Down too.
        state.open_prov = self._provider
        state.open_prov_idx = next(
            (i for i, (n, p) in enumerate(state.providers) if p is self._provider),
            state.open_prov_idx
        )
        state.channels = self._channels
        state.sel_cat = self._category
        state.b5_focused = self._sel
        state.b5_scroll = 0
        state.b5_window_start = 0
        state.b5_cursor_time = 0

        if self._parent_screen is not None:
            try:
                self._parent_screen.update_channel(self._provider, ch)
            except Exception:
                pass
        self.close(None)

    def _ready(self):
        self._auto_populate()

    def _auto_populate(self):
        """Fill pane 1 with whatever's currently playing, so it's never blank
        on open. If screen 3 later commits a different category via
        update_channel_list(), that explicit choice wins."""
        self._load_gen += 1
        run_now(self._auto_read_categories, self._provider, self._load_gen)

    def _auto_read_categories(self, provider, generation):
        try:
            cats = state.backend.get_live_categories(provider)
        except Exception:
            cats = []
        reactor.callFromThread(self._auto_categories_ready, provider, cats, generation)

    def _auto_categories_ready(self, provider, cats, generation):
        if generation != self._load_gen:
            return
        # Same name-keyed hidden-categories store screen1/screen3 use.
        try:
            from .backend.provider_category_settings import load as _load_disabled_cats
            disabled = {str(x) for x in _load_disabled_cats(provider)}
        except Exception:
            disabled = set()
        try:
            from .backend.private import hidden as _private_hidden
            disabled |= _private_hidden(provider, "live")
        except Exception:
            pass
        cats = [c for c in cats if str(c.category_id) not in disabled]
        if not cats:
            return
        focus_cat = getattr(state, 'sel_cat', None)
        category = None
        if focus_cat is not None:
            focus_id = str(getattr(focus_cat, 'category_id', ''))
            category = next((c for c in cats if str(c.category_id) == focus_id), None)
        if category is None:
            category = cats[0]
        run_now(self._auto_read_channels, provider, category, generation)

    def _auto_read_channels(self, provider, category, generation):
        try:
            channels = state.backend.get_live_channels(provider, category)
        except Exception:
            channels = []
        _preread_guides(channels)
        reactor.callFromThread(self._auto_channels_ready, provider, category, channels, generation)

    def _auto_channels_ready(self, provider, category, channels, generation):
        if generation != self._load_gen:
            return
        t0 = _time.time()
        self.update_channel_list(provider, category, channels)
        _slow_note("channel list (Left)", t0, getattr(category, "category_name", ""))

    def _open_next(self):
        self.session.openWithCallback(self._next_closed, ChannelBrowser3, self._provider, self._channels, self)

    def _next_closed(self, result=None):
        if result == 'exit_all':
            self.close('exit_all')

    def update_channel_list(self, provider, category, channels):
        """Called on open (auto-populate) and by screen 3 when OK is pressed on
        a category — either way this mirrors that category's channel/EPG list
        into this screen's pane 1. An explicit screen-3 commit always wins over
        a still-in-flight auto-populate fetch (see the generation bump below).
        The highlighted
        channel is always drawn at CENTER_ROW; the list scrolls around it.
        Row height gives the channel name room to wrap to 2 lines (long names)
        without overlapping the EPG line underneath."""
        self._load_gen += 1  # invalidate any still-in-flight auto-populate fetch
        self._provider = provider  # keep in sync — _open_epg reads this on OK,
        # and previously never got updated here, so selecting a channel after
        # switching provider via screen 3 would open the full EPG with the
        # OLD provider paired with the NEW channel — a mismatched pair that
        # can't build a valid stream URL, which was the actual freeze.
        self._channels = list(channels or [])
        now = int(_time.time())
        total = len(self._channels)

        # Default the highlight to whatever's actually playing right now, if
        # it's in this list — else the first row.
        self._sel = 0
        playing_url = getattr(state, 'last_play_url', None)
        if playing_url:
            for i, ch in enumerate(self._channels):
                try:
                    if _live_url_matches(provider, ch, playing_url):
                        self._sel = i
                        break
                except Exception:
                    pass

        self._category = category
        self['ch_header'].setText('  %s - %s' % (
            getattr(provider, 'name', ''), getattr(category, 'category_name', '')))
        self._draw_channel_rows()

        # Pane 2: EPG timeline for whichever channel is highlighted above.
        self._draw_epg(provider, self._channels[self._sel] if self._channels else None)

    def _draw_channel_rows(self):
        """Draws pane 1's channel/EPG-now rows and highlight tile for the
        current self._sel — used both by update_channel_list() (fresh data)
        and by _up()/_down() (just moving the highlight, same data)."""
        now = int(_time.time())
        total = len(self._channels)
        if total <= self.VISIBLE:
            scroll = 0
        else:
            scroll = max(0, min(self._sel - self.CENTER_ROW, total - self.VISIBLE))

        try: self['ch_sel'].instance.move(ePoint(12, 60 + (self._sel - scroll) * self.ROW_H))
        except Exception: pass
        for row in range(self.VISIBLE):
            idx, y = scroll + row, 60 + row * self.ROW_H
            try:
                if idx < total:
                    ch = self._channels[idx]
                    selected = (idx == self._sel)
                    name_color = 0x000000 if selected else 0xffffff
                    prog_color = 0x000000 if selected else 0xf5c542
                    self['ch_%d' % row].setText('  ' + ch.name)
                    self['ch_%d' % row].instance.move(ePoint(12, y + 6))
                    self['ch_%d' % row].instance.setForegroundColor(gRGB(name_color))
                    prog = ''
                    prog_ev = None
                    try:
                        if state.backend and state.backend.epg_store:
                            for ev in state.backend.epg_store.get_for(ch):
                                if ev.start <= now < ev.end:
                                    prog, prog_ev = ev.title, ev
                                    break
                    except Exception:
                        pass
                    # Recording indicator — scoped to THIS event's start, not
                    # just provider+channel, so a channel with an active
                    # recording doesn't get badged once its current
                    # programme has actually moved on to something else.
                    rec_status = None
                    if self._provider and prog_ev is not None:
                        for _rk, _rec in state.active_recordings.items():
                            if (_rec.get('provider_id') == self._provider.provider_id
                                    and _rec.get('stream_id') == ch.stream_id
                                    and _rec.get('event_start') == prog_ev.start):
                                rec_status = _rec.get('status')
                                break
                    if rec_status == 'active':
                        prog_text = u'● REC  ' + prog
                        if not selected:
                            prog_color = 0xff4444
                    elif rec_status == 'scheduled':
                        prog_text = u'● SCHEDULED  ' + prog
                        if not selected:
                            prog_color = 0x44cc44
                    else:
                        prog_text = prog
                    self['ch_prog_%d' % row].setText('  ' + prog_text)
                    self['ch_prog_%d' % row].instance.move(ePoint(12, y + 72))
                    self['ch_prog_%d' % row].instance.setForegroundColor(gRGB(prog_color))
                else:
                    self['ch_%d' % row].instance.move(ePoint(3500, 0))
                    self['ch_prog_%d' % row].instance.move(ePoint(3500, 0))
            except Exception: pass

    def _up(self):
        if self._sel > 0:
            self._sel -= 1
            self._draw_channel_rows()
            self._draw_epg(self._provider, self._channels[self._sel] if self._channels else None)

    def _down(self):
        if self._sel < len(self._channels) - 1:
            self._sel += 1
            self._draw_channel_rows()
            self._draw_epg(self._provider, self._channels[self._sel] if self._channels else None)

    def _epg_ymd(self, ts):
        lt = _time.localtime(ts)
        return (lt.tm_year, lt.tm_mon, lt.tm_mday)

    def _epg_date_label(self, ts, now):
        ymd = self._epg_ymd(ts)
        if ymd == self._epg_ymd(now):
            return 'Today'
        if ymd == self._epg_ymd(now + 86400):
            return 'Tomorrow'
        if ymd == self._epg_ymd(now - 86400):
            return 'Yesterday'
        return _time.strftime('%a %d %b', _time.localtime(ts))

    def _build_epg_rows(self, channel, now):
        """Full schedule for one channel as a flat (kind, payload) list — real
        events plus a ('marker', label) entry wherever the calendar date
        changes — then the same centre-lock scroll math used everywhere else,
        anchored on whatever's airing right now (or the next upcoming event)."""
        try:
            events = sorted(state.backend.epg_store.get_for(channel), key=lambda e: e.start)
        except Exception:
            events = []
        if not events:
            return [], None, 0

        combined = []
        last_ymd = None
        running_idx = None
        next_idx = None
        for ev in events:
            ymd = self._epg_ymd(ev.start)
            if last_ymd is not None and ymd != last_ymd:
                combined.append(('marker', self._epg_date_label(ev.start, now)))
            last_ymd = ymd
            combined.append(('event', ev))
            if running_idx is None and ev.start <= now < ev.end:
                running_idx = len(combined) - 1
            if next_idx is None and ev.start >= now:
                next_idx = len(combined) - 1

        anchor = running_idx if running_idx is not None else (
            next_idx if next_idx is not None else len(combined) - 1)

        total = len(combined)
        if total <= self.EPG_VISIBLE:
            scroll = 0
        else:
            scroll = max(0, min(anchor - self.EPG_CENTER_ROW, total - self.EPG_VISIBLE))
        return combined, anchor, scroll

    def _draw_epg(self, provider, channel):
        self['epg_header'].setText('  ' + getattr(channel, 'name', '') if channel else '')
        if channel is None:
            try: self['epg_sel'].instance.move(ePoint(3500, 0))
            except Exception: pass
            for row in range(self.EPG_VISIBLE):
                try:
                    self['epg_time_%d' % row].instance.move(ePoint(3500, 0))
                    self['epg_title_%d' % row].instance.move(ePoint(3500, 0))
                except Exception: pass
            return

        now = int(_time.time())
        rows, anchor, scroll = self._build_epg_rows(channel, now)
        total = len(rows)
        try:
            if anchor is not None:
                self['epg_sel'].instance.move(ePoint(612, 60 + (anchor - scroll) * self.EPG_ROW_H))
            else:
                self['epg_sel'].instance.move(ePoint(3500, 0))
        except Exception: pass
        for row in range(self.EPG_VISIBLE):
            idx, y = scroll + row, 60 + row * self.EPG_ROW_H
            try:
                if idx < total:
                    kind, payload = rows[idx]
                    selected = (idx == anchor)
                    text_color = 0x000000 if selected else 0xffffff
                    time_color = 0x000000 if selected else 0xf5c542
                    if kind == 'marker':
                        self['epg_time_%d' % row].setText('')
                        self['epg_title_%d' % row].setText('  — %s —' % payload.upper())
                        self['epg_title_%d' % row].instance.setForegroundColor(gRGB(0xf5c542 if not selected else 0x000000))
                    else:
                        ev = payload
                        # Recording indicator — scoped to this event's own
                        # start time, not just provider+channel, so only
                        # the actual recording slot gets badged rather than
                        # every event tile for a channel that's recording.
                        rec_status = None
                        if provider:
                            for _rk, _rec in state.active_recordings.items():
                                if (_rec.get('provider_id') == provider.provider_id
                                        and _rec.get('stream_id') == channel.stream_id
                                        and _rec.get('event_start') == ev.start):
                                    rec_status = _rec.get('status')
                                    break
                        if rec_status == 'active':
                            title_text  = u'● REC  ' + ev.title
                            title_color = 0xff4444 if not selected else text_color
                        elif rec_status == 'scheduled':
                            title_text  = u'● SCHEDULED  ' + ev.title
                            title_color = 0x44cc44 if not selected else text_color
                        else:
                            title_text  = ev.title
                            title_color = text_color
                        self['epg_time_%d' % row].setText(
                            '  ' + _time.strftime('%H:%M', _time.localtime(ev.start)))
                        self['epg_title_%d' % row].setText(title_text)
                        self['epg_title_%d' % row].instance.setForegroundColor(gRGB(title_color))
                    self['epg_time_%d' % row].instance.move(ePoint(612, y))
                    self['epg_time_%d' % row].instance.setForegroundColor(gRGB(time_color))
                    self['epg_title_%d' % row].instance.move(ePoint(702, y))
                else:
                    self['epg_time_%d' % row].instance.move(ePoint(3500, 0))
                    self['epg_title_%d' % row].instance.move(ePoint(3500, 0))
            except Exception: pass

    def _close(self):
        self.close(None)


class ChannelBrowser3(Screen):
    """Live-player LEFT-from-LiveTV screen 3 of 3.
    Pane 1 (left, x=0-600): scrolling provider/category list — same layout and
    behaviour as the original Screen 1 'b2' pane.
    Pane 2 (right, x=600-1200): header 'Provider - Category' plus the list of
    channels in whichever category pane 1's cursor is currently over — updates
    live as the user moves up/down.
    LEFT does nothing (end of chain). RIGHT (or Cancel) closes back to screen 2."""

    ROW_H = 100        # pane 1 (provider/category) row height — unchanged
    VISIBLE = 10        # pane 1 visible rows — unchanged
    CH_ROW_H = 110      # pane 2 (channel/EPG) row height — taller, room for a wrapped name
    CH_VISIBLE = 9      # pane 2 visible rows
    CH_CENTER_ROW = 4   # pane 2 highlighted row always renders here — list scrolls around it

    skin = (
        '<screen name="E2iPTV_LeftScreen3" position="0,0" size="1920,1080" flags="wfNoBorder" zPosition="101" backgroundColor="transparent">'
        '<eLabel position="0,0" size="600,1080" backgroundColor="#300d1117" zPosition="0"/><eLabel position="600,0" size="600,1080" backgroundColor="#300d1117" zPosition="0"/>'
        '<widget name="b2_header" position="10,8" size="580,46" font="E2iRegular;28" foregroundColor="#ffffff" halign="left" valign="center" transparent="1" zPosition="3"/>'
        '<widget name="ch_header" position="610,8" size="580,46" font="E2iRegular;28" foregroundColor="#ffffff" halign="left" valign="center" transparent="1" zPosition="3"/>'
        '<widget name="line1" position="0,56" size="600,2" backgroundColor="#ffffff" zPosition="3"/>'
        '<widget name="line2" position="600,56" size="600,2" backgroundColor="#ffffff" zPosition="3"/>'
        '<widget name="b2_sel" position="3500,0" size="576,99" backgroundColor="E2iHi" zPosition="2"/>'
        '<widget name="ch_sel" position="3500,0" size="576,108" backgroundColor="E2iHi" zPosition="2"/>'
        + ''.join('<widget name="b2_%d" position="3500,0" size="556,99" font="E2iRegular;28" foregroundColor="#ffffff" halign="left" valign="center" transparent="1" zPosition="3"/>' % i for i in range(10))
        + ''.join('<widget name="ch_%d" position="3500,0" size="556,64" font="E2iRegular;28" foregroundColor="#ffffff" halign="left" valign="top" transparent="1" zPosition="3"/>' % i for i in range(9))
        + ''.join('<widget name="ch_prog_%d" position="3500,0" size="556,36" font="E2iRegular;24" foregroundColor="#f5c542" halign="left" valign="center" transparent="1" zPosition="3"/>' % i for i in range(9))
        + '</screen>'
    )

    def __init__(self, session, provider, channels, parent_screen=None):
        Screen.__init__(self, session)
        self._provider = provider
        self._channels = list(channels or [])
        self._parent_screen = parent_screen  # the live screen-2 instance, for OK-commit
        self._flat = []
        self._sel = 0
        self._scroll = 0
        self._cats = {}
        self._expanded = set()  # providers whose categories are currently shown
        self._did_initial_focus = False  # one-shot: land on the playing category on open
        self._preview_provider = None
        self._preview_category = None
        self._preview_channels = []
        self._preview_sel = 0
        self._load_gen = 0
        self['b2_header'] = Label('  Providers / categories')
        self['ch_header'] = Label('')
        self['line1'] = Label('')
        self['line2'] = Label('')
        self['b2_sel'] = Label('')
        self['ch_sel'] = Label('')
        for i in range(10):
            self['b2_%d' % i] = Label('')
        for i in range(9):
            self['ch_%d' % i] = Label('')
            self['ch_prog_%d' % i] = Label('')
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions'], {
            'cancel': self._exit_all, 'ok': self._ok, 'up': self._up,
            'down': self._down, 'left': self._noop,  # next-provider disabled for now — was confusing during chain-length testing
            'right': self._close}, -1)
        self.onLayoutFinish.append(self._ready)

    def _noop(self):
        pass

    def _ready(self):
        self._ensure_provider(self._provider, True)

    def _providers(self):
        # My Bouquets first, as in Live TV's B2, then the providers.
        from .backend.provider import shows as _shows
        provs = [p for n, p in state.providers if _shows(p, "live")]
        return sorted(provs, key=lambda p: 0 if getattr(p, 'is_bouquet', False) else 1)

    def _ensure_provider(self, provider, choose_first=False):
        self._provider = provider
        self._expanded.add(provider)
        if provider in self._cats:
            self._rebuild(choose_first)
            return
        run_now(self._read_categories, provider, choose_first)

    def _read_categories(self, provider, choose_first):
        try: cats = state.backend.get_live_categories(provider)
        except Exception: cats = []
        reactor.callFromThread(self._categories_ready, provider, cats, choose_first)

    def _categories_ready(self, provider, cats, choose_first):
        # Filter hidden/disabled categories — same name-keyed store screen1 uses,
        # NOT the legacy per-provider-id disabled_cats_<id>.json file.
        try:
            from .backend.provider_category_settings import load as _load_disabled_cats
            disabled = {str(x) for x in _load_disabled_cats(provider)}
        except Exception:
            disabled = set()
        # Private categories too, unless shown with 5-5 (backend/private.py).
        try:
            from .backend.private import hidden as _private_hidden
            disabled |= _private_hidden(provider, "live")
        except Exception:
            pass
        cats = [c for c in cats if str(c.category_id) not in disabled]
        self._cats[provider] = cats
        self._rebuild(choose_first)

    def _rebuild(self, choose_first=False):
        self._flat = []
        for provider in self._providers():
            self._flat.append(('provider', provider, provider.name))
            if provider in self._expanded:
                for cat in self._cats.get(provider, []):
                    self._flat.append(('category', cat, '   ' + cat.category_name))
        target = None
        if not self._did_initial_focus:
            # One-shot, on first open only: land the highlight on the category
            # that's actually playing right now, not just its provider's row.
            self._did_initial_focus = True
            focus_cat = getattr(state, 'sel_cat', None)
            if focus_cat is not None:
                focus_id = str(getattr(focus_cat, 'category_id', ''))
                # source_id required alongside category_id — self._flat
                # spans every provider's categories here, and Xtream
                # numbers each provider's categories independently, so
                # category_id alone could land the initial focus on a
                # different provider's category that just happens to
                # share the same id.
                focus_source_id = getattr(focus_cat, 'source_id', None)
                target = next((i for i, row in enumerate(self._flat)
                               if row[0] == 'category'
                               and str(row[1].category_id) == focus_id
                               and getattr(row[1], 'source_id', None) == focus_source_id), None)
        if target is None:
            target = next((i for i, row in enumerate(self._flat) if row[0] == 'provider' and row[1] is self._provider), 0)
        self._sel = target
        self._draw()
        self._maybe_preview()

    def _current_row_context(self):
        """(kind, provider, category) for the row at self._sel.
        For a category row, the parent provider is found by scanning backward
        to the nearest provider row — self._provider alone isn't reliable
        since several providers can be expanded at once."""
        if not self._flat or not (0 <= self._sel < len(self._flat)):
            return None, None, None
        kind, item, _text = self._flat[self._sel]
        if kind == 'provider':
            return 'provider', item, None
        for i in range(self._sel - 1, -1, -1):
            k2, item2, _ = self._flat[i]
            if k2 == 'provider':
                return 'category', item2, item
        return 'category', None, item

    def _maybe_preview(self):
        kind, provider, category = self._current_row_context()
        if kind != 'category' or provider is None or category is None:
            return
        if category is self._preview_category and provider is self._preview_provider:
            return
        self._preview_provider = provider
        self._preview_category = category
        self._load_gen += 1
        run_now(self._read_channels, provider, category, self._load_gen)

    def _read_channels(self, provider, category, generation):
        try: channels = state.backend.get_live_channels(provider, category)
        except Exception: channels = []
        reactor.callFromThread(self._channels_ready, provider, category, channels, generation)

    def _channels_ready(self, provider, category, channels, generation):
        if generation != self._load_gen:
            return
        self._preview_channels = channels
        self._draw_channels(provider, category)

    def _draw_channels(self, provider, category):
        now = int(_time.time())
        total = len(self._preview_channels)

        # Default the highlight to whatever's actually playing right now, if
        # it's in this category's list — else the first row.
        self._preview_sel = 0
        playing_url = getattr(state, 'last_play_url', None)
        if playing_url:
            for i, ch in enumerate(self._preview_channels):
                try:
                    if _live_url_matches(provider, ch, playing_url):
                        self._preview_sel = i
                        break
                except Exception:
                    pass

        if total <= self.CH_VISIBLE:
            scroll = 0
        else:
            scroll = max(0, min(self._preview_sel - self.CH_CENTER_ROW, total - self.CH_VISIBLE))

        self['ch_header'].setText('  %s - %s' % (
            getattr(provider, 'name', ''), getattr(category, 'category_name', '')))
        try: self['ch_sel'].instance.move(ePoint(612, 60 + (self._preview_sel - scroll) * self.CH_ROW_H))
        except Exception: pass
        for row in range(self.CH_VISIBLE):
            idx, y = scroll + row, 60 + row * self.CH_ROW_H
            try:
                if idx < total:
                    ch = self._preview_channels[idx]
                    selected = (idx == self._preview_sel)
                    name_color = 0x000000 if selected else 0xffffff
                    prog_color = 0x000000 if selected else 0xf5c542
                    self['ch_%d' % row].setText('  ' + ch.name)
                    self['ch_%d' % row].instance.move(ePoint(612, y + 6))
                    self['ch_%d' % row].instance.setForegroundColor(gRGB(name_color))
                    prog = ''
                    prog_ev = None
                    try:
                        if state.backend and state.backend.epg_store:
                            for ev in state.backend.epg_store.get_for(ch):
                                if ev.start <= now < ev.end:
                                    prog, prog_ev = ev.title, ev
                                    break
                    except Exception:
                        pass
                    # Recording indicator — scoped to THIS event's start,
                    # same as the other channel-list panes, so only the
                    # channel's actual currently-recording programme (not
                    # every row for that channel) gets badged.
                    rec_status = None
                    if provider and prog_ev is not None:
                        for _rk, _rec in state.active_recordings.items():
                            if (_rec.get('provider_id') == provider.provider_id
                                    and _rec.get('stream_id') == ch.stream_id
                                    and _rec.get('event_start') == prog_ev.start):
                                rec_status = _rec.get('status')
                                break
                    if rec_status == 'active':
                        prog_text = u'● REC  ' + prog
                        if not selected:
                            prog_color = 0xff4444
                    elif rec_status == 'scheduled':
                        prog_text = u'● SCHEDULED  ' + prog
                        if not selected:
                            prog_color = 0x44cc44
                    else:
                        prog_text = prog
                    self['ch_prog_%d' % row].setText('  ' + prog_text)
                    self['ch_prog_%d' % row].instance.move(ePoint(612, y + 72))
                    self['ch_prog_%d' % row].instance.setForegroundColor(gRGB(prog_color))
                else:
                    self['ch_%d' % row].instance.move(ePoint(3500, 0))
                    self['ch_prog_%d' % row].instance.move(ePoint(3500, 0))
            except Exception: pass

    def _draw(self):
        self._scroll = max(0, min(max(0, len(self._flat) - self.VISIBLE), self._sel - 5))
        try: self['b2_sel'].instance.move(ePoint(12, 60 + (self._sel - self._scroll) * self.ROW_H))
        except Exception: pass
        for row in range(self.VISIBLE):
            idx, y = self._scroll + row, 60 + row * self.ROW_H
            try:
                if idx < len(self._flat):
                    text = self._flat[idx][2]
                    self['b2_%d' % row].setText('  ' + text)
                    self['b2_%d' % row].instance.move(ePoint(12, y + 6))
                    self['b2_%d' % row].instance.setForegroundColor(gRGB(0x000000 if idx == self._sel else 0xffffff))
                else:
                    self['b2_%d' % row].instance.move(ePoint(3500, 0))
            except Exception: pass

    def _up(self):
        if self._sel > 0:
            self._sel -= 1
            self._draw()
            self._maybe_preview()

    def _down(self):
        if self._sel < len(self._flat) - 1:
            self._sel += 1
            self._draw()
            self._maybe_preview()

    def _next_provider(self):
        """LEFT on this screen jumps the cursor to the next provider row,
        wrapping from the last provider back to the first."""
        providers = self._providers()
        if not providers:
            return
        _kind, current, _cat = self._current_row_context()
        if current is None:
            current = self._provider
        try:
            idx = providers.index(current)
        except ValueError:
            idx = -1
        next_provider = providers[(idx + 1) % len(providers)]
        target = next((i for i, row in enumerate(self._flat)
                        if row[0] == 'provider' and row[1] is next_provider), None)
        if target is None:
            return
        self._provider = next_provider
        self._sel = target
        self._draw()
        self._maybe_preview()

    def _ok(self):
        if not self._flat: return
        kind, item, text = self._flat[self._sel]
        if kind == 'category':
            self._commit_category(item)
            return
        if kind != 'provider':
            return
        self._provider = item  # keep the cursor on this provider's row
        if item in self._expanded:
            # already expanded — OK again collapses just this one, others stay open
            self._expanded.discard(item)
            self._rebuild()
        else:
            self._ensure_provider(item, True)

    def _commit_category(self, category):
        """OK on a category: push its channel/EPG list into screen 2's pane 1."""
        _kind, provider, _cat = self._current_row_context()
        if provider is None:
            provider = self._provider
        self._load_gen += 1
        reactor.callInThread(self._commit_fetch, provider, category, self._load_gen)

    def _commit_fetch(self, provider, category, generation):
        try: channels = state.backend.get_live_channels(provider, category)
        except Exception: channels = []
        reactor.callFromThread(self._commit_ready, provider, category, channels, generation)

    def _commit_ready(self, provider, category, channels, generation):
        if generation != self._load_gen:
            return
        try:
            if self._parent_screen is not None:
                self._parent_screen.update_channel_list(provider, category, channels)
        except Exception:
            pass
        # Same as pressing RIGHT: return to screen 2, now showing what we just pushed.
        self.close(None)

    def _close(self):
        self.close(None)

    def _exit_all(self):
        # Closes the whole 3-screen chain outright — see
        # ChannelBrowser._cancel's identical comment for the full
        # reasoning. RIGHT (_close above) stays a plain "go back to
        # screen 2 without committing" — only Exit/Cancel gets this.
        self.close('exit_all')


# -----------------------------------------------------------------------------
# Custom subtitle track selection — replaces InfoBarSubtitleSupport's
# own subtitleSelection(), which opens Enigma2's stock SubtitleSelection
# screen (native OpenATV chrome). This queries the same underlying
# iSubtitleOutput API and enables tracks the same way, just through
# this plugin's own dark-panel scrollable list instead — same
# GUIComponent shape as search.py's _SearchList/favourites.py's
# _FavouritesList (GUI_WIDGET + postWidgetCreate, the pattern proven
# to render correctly here), duplicated rather than imported for the
# same reason every other small UI helper class in this project is:
# keeps this file's own import direction simple.
#
# One real caveat, unlike most of the rest of this project: the exact
# shape of a subtitle track tuple from getSubtitleList() varies by
# track type (DVB vs teletext vs embedded text) in ways that aren't
# fully verifiable without a real box to test against, so
# _subtitle_track_label()'s attempt at a readable name (language,
# type) falls back to a generic "Track N" rather than guessing wrong
# — Enigma2's own stock screen has the same problem and solves it with
# lookups this plugin doesn't have easy access to reproduce exactly.
# -----------------------------------------------------------------------------

_SUBTITLE_LIST_ITEM_H = 60


class _SubtitleList(GUIComponent):

    GUI_WIDGET = eListbox

    def __init__(self, entries):
        GUIComponent.__init__(self)
        self.l = eListboxPythonMultiContent()
        self.l.setItemHeight(_SUBTITLE_LIST_ITEM_H)
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
        self._entries = labels
        rows = []
        for i, text in enumerate(labels):
            bg = _T.HI if i == selected_index else _T.PANEL
            fg = 0x000000 if i == selected_index else 0xffffff
            rows.append([
                None,
                MultiContentEntryText(
                    pos=(20, 0), size=(1800, _SUBTITLE_LIST_ITEM_H),
                    font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text=text, color=fg, color_sel=fg,
                    backcolor=bg, backcolor_sel=bg
                )
            ])
        self.l.setList(rows)
        if self.instance and labels:
            self.instance.moveSelectionTo(
                min(selected_index, len(labels) - 1)
            )

    def setSelection(self, index):
        self.setEntries(self._entries, index)


def _subtitle_track_label(track, index):
    """Best-effort readable label for one subtitle track tuple — see
    this section's comment above for why this can't be fully precise
    without a real box to verify the exact tuple shape against. Tries
    to find a plain-text language/description string among the
    tuple's own elements first; falls back to a generic numbered
    label rather than showing something potentially wrong."""
    try:
        for part in track:
            if isinstance(part, str) and part.strip():
                return part
    except Exception:
        pass
    return "Track %d" % (index + 1)


class SubtitleSelectionMenu(Screen):

    skin = (
        '<screen name="SubtitleSelectionMenu" position="0,0" size="1920,1080"'
        ' flags="wfNoBorder" backgroundColor="#0d1117">'
        '<widget name="header" position="40,30" size="1840,50"'
        ' font="E2iRegular;34" foregroundColor="E2iRail"'
        ' halign="left" valign="center" transparent="1" zPosition="1"/>'
        '<widget name="list" position="40,100" size="1840,900"'
        ' transparent="1" scrollbarMode="showOnDemand"/>'
        '</screen>'
    )

    def __init__(self, session, tracks):
        Screen.__init__(self, session)

        self._tracks = tracks
        self._sel = 0

        self["header"] = Label(
            "Subtitles" if tracks else "No subtitle tracks available"
        )
        self["list"] = _SubtitleList(self._labels())

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self._confirm,
                "cancel": self._dismiss,
                "up":     self._up,
                "down":   self._down,
            }, -1)

    def _labels(self):
        labels = ["Off"]
        for i, track in enumerate(self._tracks):
            labels.append(_subtitle_track_label(track, i))
        return labels

    def _up(self):
        if self._sel > 0:
            self._sel -= 1
            self["list"].setSelection(self._sel)

    def _down(self):
        if self._sel < len(self._tracks):  # +1 entries incl. "Off"
            self._sel += 1
            self["list"].setSelection(self._sel)

    def _confirm(self):
        # index 0 is "Off"; index N is self._tracks[N-1]
        if self._sel == 0:
            self.close("off")
        else:
            self.close(self._tracks[self._sel - 1])

    def _dismiss(self):
        self.close(None)


class E2iPTVPlayer(InfoBarSubtitleSupport, Screen):

    skin = (
        '<screen name="E2iPTVPlayer"'
        ' position="0,0" size="1920,1080"'
        ' flags="wfNoBorder"'
        ' backgroundColor="#ff000000"'
        ' zPosition="-1"'
        ' transparent="1">'
        '</screen>'
    )
    # info_bg/info_ch/info_prog/info_time were a dead leftover bottom bar:
    # info_bg (0d1117, y=900-1080) painted opaque and permanently visible
    # since nothing ever hid it (the timer meant to via _hide_info was
    # never started), while info_ch/info_prog/info_time were never
    # populated by _show_channel_info (it opens ProgrammeOverlay instead)
    # — so the bar showed forever with no text, disappearing only while
    # ProgrammeOverlay's own opaque panel covered it on OK/channel change.
    # ProgrammeOverlay already fully covers this screen's on-demand info
    # display, so the widgets (and the timer/hide plumbing below) are
    # removed rather than wired up to duplicate it.

    def __init__(self, session, url, title="", channel_idx=None,
                 is_vod=False, description="", duration_pts=None,
                 resume_key=None, vod_provider=None, vod_cmd=None,
                 vod_kind="movie"):
        Screen.__init__(self, session)
        # Standard Enigma2 subtitle-track-selection mechanism (see
        # Screens.InfoBarGenerics) — this player had nothing wired up
        # for subtitles at all before; mixing this in is what actually
        # lets the "0" key below query whatever subtitle tracks the
        # current service has and offer them, exactly like the stock
        # Live TV infobar does. Subtitle rendering itself (once a
        # track is picked) is handled by the service/video pipeline,
        # not by anything this screen's own skin needs to provide.
        InfoBarSubtitleSupport.__init__(self)
        self._url        = url
        # Resume/watched tracking needs a STABLE identifier, not
        # necessarily the playback url itself — confirmed directly
        # that Stalker's resolved VOD url carries a fresh, randomly
        # -generated play_token on every single resolution, so two
        # resolutions of the exact same movie never produce the same
        # url. Keying resume/watched by self._url (as this used to)
        # meant neither could ever match a previous viewing for
        # Stalker content, even seconds later — a caller with a
        # stable identity for this content (see play_movie) passes it
        # in here; anything else (Live TV, or if a caller has no
        # better key) falls back to the url itself, unchanged from
        # the old behaviour.
        self._resume_key = resume_key or url
        # Stalker VOD only — see _play's own use of these for why: a
        # url resolved once, upfront, in play_movie()/play_episode()
        # can go stale by the time it's actually used, if the resume
        # prompt sits on screen for any length of time waiting on the
        # person to choose. Xtream/Live TV never sets these, since
        # their own urls don't carry a short-lived token the way
        # Stalker's do. vod_kind picks which StalkerClient method
        # actually resolves vod_cmd at play time — resolve_vod_url
        # for a movie, resolve_series_url for an episode (whose
        # vod_cmd is "<season_cmd>|<episode_number>", not a plain
        # cmd — see SeriesInfoReader._read_stalker and
        # StalkerClient.resolve_series_url).
        self._vod_provider = vod_provider
        self._vod_cmd = vod_cmd
        self._vod_kind = vod_kind
        self._title      = title
        self._prev_ref   = None
        self._ch_idx     = channel_idx
        # VOD (movie/episode) playback has no channel/EPG context —
        # _show_channel_info is built entirely around Live TV's
        # channel lookup and would show blank/wrong info here, so OK
        # instead toggles a RecordingHud-style bar (title/progress),
        # reused as-is from RecordingPlayer since its position/length
        # reporting already comes from the generic seek object, not
        # anything Recording-specific.
        self._is_vod      = is_vod
        self._description = description
        self._hud         = None
        self._skip_osd    = None
        # Resume-from-last-position and the skip-key OSD — both ported
        # from RecordingPlayer, same mechanism (generic seek object,
        # nothing recording-specific). Keyed by self._url instead of a
        # local file path — the resume store is just a plain dict on
        # disk keyed by string, so a stream URL works identically.
        # VOD only: resuming/skip-readout a live channel doesn't make
        # sense (you're joining a live broadcast, not a fixed-length
        # asset), so these stay unused whenever is_vod is False.
        #
        # duration_pts: caller-supplied fallback for the HUD's progress
        # bar/time readout, used until (or if) the live seek object's
        # own getLength() becomes available — see RecordingHud._tick().
        # Movies have no duration metadata from Xtream's list call (see
        # MovieInfoReader), so this is None for them; episodes carry
        # duration_secs from the series detail fetch (see
        # SeriesInfoReader), which play_episode() converts and passes
        # in here.
        self._duration_pts = duration_pts
        self._pending_resume_pts = None
        self._resume_seek_attempts = 0
        from .state import state as _st
        # Snapshot provider+channels at open time so they don't change under us
        self._my_provider = _st.open_prov
        self._my_cat_id   = str(getattr(getattr(_st, 'sel_cat', None), 'category_id', '') or '')
        self._my_channels = list(_st.channels or [])
        self._prev_ch_idx = getattr(_st, "prev_ch_idx", None)
        _st.last_ch_idx   = channel_idx
        self["actions"] = ActionMap(
            ["OkCancelActions","DirectionActions","TvRadioActions",
             "InfobarEPGActions","EPGSelectActions",
             "InfobarSeekActions","NumberActions","InfobarActions",
             "InfobarInstantRecord", "LeavePlayerActions"],
            {
                "cancel":     self._stop,
                "ok":         self._toggle_hud if is_vod else self._show_channel_info,
                "stop":       self._stop,
                "tv":         self._stop,
                # The physical dedicated Stop/"X" button on many
                # remotes fires this action specifically — distinct
                # from the generic DVR-style "stop" above, which some
                # boxes never actually map that key to at all. Same
                # standard Enigma2 context (LeavePlayerActions) the
                # native Movie Player itself uses for this exact
                # button — reported as not working via "stop" alone.
                "leavePlayer": self._stop,
                "epg":        self._go_to_grid,
                "showEPG":    self._go_to_grid,
                "EPGPressed": self._go_to_grid,
                "up":         self._channel_up,
                "down":       self._channel_down,
                "right":      self._channel_swap,
                "left":       self._open_browser,
                # Bound under both "record" and "instantRecord" — different
                # OpenATV keymaps name the physical Record key's action
                # differently depending on context, and an unmatched dict
                # key is simply never called, so covering both is free.
                "record":         self._instant_record,
                "instantRecord":  self._instant_record,
                "1": self._skip_back_30,   "4": self._skip_back_120,
                "7": self._skip_back_300,  "3": self._skip_forward_30,
                "6": self._skip_forward_120, "9": self._skip_forward_300,
                # Opens the standard subtitle-track-selection screen
                # (from InfoBarSubtitleSupport, mixed in above) — "0"
                # was free (skip keys already use 1/3/4/6/7/9).
                "0": self.subtitleSelection,
                # What's in this channel's stream (subtitles? which
                # sound?) - a few seconds sampled, see _stream_details.
                "2": self._stream_details,
                # Physical Pause/Play buttons — same native action names
                # RecordingPlayer binds (see its docstring there for
                # why these specific names). Already reachable here
                # since "InfobarSeekActions" is one of this screen's
                # contexts too.
                "pauseService":     self._pause_only,
                "unPauseService":   self._resume_only,
                "playpauseService": self._toggle_pause,
            }, -1)
        self._paused = False
        self.onLayoutFinish.append(self._schedule_resume_check)

        # Detects natural end-of-playback (VOD reaching the end of the
        # file) so this closes cleanly on its own, the same way Stop/
        # cancel/tv already do, rather than sitting frozen on the last
        # frame waiting for the user to press Exit manually — this
        # screen had no service-event handling of any kind before.
        # Deliberately VOD-only: a Live TV stream hitting a transient
        # buffering/EOF blip is routine and must never auto-close the
        # whole screen out from under the user.
        self.session.nav.event.append(self.__serviceEvent)
        self.onClose.append(self.__removeServiceEventHandler)

        # Live TV freeze watchdog (see _wd_tick): reconnects a channel
        # whose stream has stopped.
        self._wd_ref = None
        self._wd_timer = eTimer()
        self._wd_timer.callback.append(self._wd_tick)
        self._wd_timer.start(500, False)
        self.onClose.append(self._wd_timer.stop)

        # Physical Stop/"X" button — confirmed via raw-keycode logging
        # (see _KEY_STOP's own comment) to be KEY_STOP, which
        # keymap.xml never translates into any named action at all in
        # this screen's contexts. Bound directly at the raw-keycode
        # level to bypass that missing translation, rather than a
        # named-action binding that could never have caught it.
        self.__key_stop_handler = _make_key_stop_handler(self._stop)
        eActionMap.getInstance().bindAction("", -0x7FFFFFFF, self.__key_stop_handler)
        self.onClose.append(self.__removeKeyStopHandler)

        # Menu: the icon rail's sections in a small pop-up over the picture
        # (rail_menu.py). Caught by its raw key code — the image takes the
        # Menu key before any named action reaches a plugin screen.
        self.__key_menu_handler = self._on_raw_menu_key
        eActionMap.getInstance().bindAction("", -0x7FFFFFFF, self.__key_menu_handler)
        self.onClose.append(self.__removeKeyMenuHandler)
        self.onClose.append(self.__mark_closed)

    def __mark_closed(self):
        self._e2i_closed = True

    _KEY_MENU = 139

    def __removeKeyMenuHandler(self):
        try:
            eActionMap.getInstance().unbindAction("", self.__key_menu_handler)
        except Exception:
            pass

    def _on_raw_menu_key(self, key, flag):
        """Menu while this player is the screen in front: open the pop-up
        and keep the key from the image. Every other key, or any other
        screen in front, is left alone."""
        if key != self._KEY_MENU:
            return 0
        # Plugin closed (Exit / Close), or this player already closed:
        # leave Menu to the box.
        from .state import state as _st
        if not getattr(_st, "plugin_open", False) or getattr(self, "_e2i_closed", False):
            return 0
        try:
            if self.session.current_dialog is not self:
                return 0
        except Exception:
            return 0
        if flag == 0:
            from twisted.internet import reactor as _r
            _r.callLater(0, self._open_rail_menu)
        return 1

    def _open_rail_menu(self):
        try:
            from .rail_menu import RailMenu
            self.session.openWithCallback(
                self._rail_menu_closed, RailMenu,
                "Movies" if getattr(self, "_is_vod", False) else "Live TV")
        except Exception as e:
            print("[E2iPTV] Menu pop-up:", e)

    # Menu pop-up options (all wired).
    _RAIL_WIRED = ("series", "movies", "music", "recordings", "search", "favourites",
                   "settings", "live", "exit")

    def _rail_menu_closed(self, choice=None):
        if not choice or choice[1] not in self._RAIL_WIRED:
            return
        if choice[0] == "Live TV" and not self._is_vod:
            return              # already watching Live TV full screen
        from .state import state as _st
        _st.live_choice = ("full" if choice[0] == "Live TV" else
                           "grid" if choice[0] == "EPG Grid" else None)
        if choice[0] == "EPG Grid" and not self._is_vod:
            # Same as the EPG key: back to the grid (Live TV opens it if
            # this channel wasn't started from the grid).
            from .goto import nav_log
            nav_log("MENU picked 'EPG Grid' (live)")
            self._go_to_grid()
            return
        from .goto import nav_log
        nav_log("MENU picked %r (vod=%r) live_choice=%r" % (
            choice[0], self._is_vod, _st.live_choice))
        from .goto import request
        request(choice[1])
        if self._is_vod:
            self._stop()            # saves where the film/episode got to
        else:
            self._go_to_grid()      # live channel keeps playing in the mini TV

    def __removeKeyStopHandler(self):
        try:
            eActionMap.getInstance().unbindAction("", self.__key_stop_handler)
        except Exception:
            pass

    def __serviceEvent(self, evt):
        if evt == iPlayableService.evEOF and self._is_vod:
            self._stop()
        elif evt == iPlayableService.evEOF and self._wd_is_live():
            # A live stream "ending" means the provider dropped it - but
            # not in the first 2 s after a channel change: that is the old
            # channel's stream closing (reconnecting then fought the
            # Right-key swap and could land back on the old channel).
            if _time.time() - getattr(self, "_wd_started", 0) < 2:
                return
            from twisted.internet import reactor as _r
            _r.callLater(0.3, self._wd_restart, "stream ended")

    def __removeServiceEventHandler(self):
        try:
            self.session.nav.event.remove(self.__serviceEvent)
        except Exception:
            pass

    # subtitleSelection() deliberately NOT overridden here — reverted
    # back to InfoBarSubtitleSupport's own default implementation a
    # second time. The position-restore attempt (capture position,
    # correct after a delay) turned out to rely on reactor.callLater()
    # firing promptly after the modal subtitle-selection screen
    # closes, which logged data showed isn't reliable — a correction
    # scheduled for 0.8s later didn't actually run until 15 seconds
    # later, using stale data from an earlier toggle by the time it
    # did. That's a broken foundation, not a tuning problem, so this
    # goes back to the plain, proven-stable call rather than
    # continuing to patch it. The keyframe-restart seek-back on
    # enabling a subtitle track (see the git history/prior
    # conversation for the diagnosis) remains present — it appears to
    # be a genuine characteristic of the video service/decoder for
    # this content, not something this plugin's code is causing.

    @staticmethod
    def _log(msg):
        try:
            with open(LOG("e2iptv_recplay_seek.log"), "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    # How far in (and how close to the end) a saved position needs to
    # be to actually offer a resume — same thresholds RecordingPlayer
    # uses, see its docstring for the reasoning.
    _RESUME_MIN_PTS  = 30 * 90000
    _RESUME_TAIL_PTS = 30 * 90000

    def _schedule_resume_check(self):
        """onLayoutFinish hook. Live TV skips straight to _play() —
        only VOD (movies/episodes) has a meaningful saved position to
        offer resuming from. Deferred by a zero-delay reactor.callLater
        for the same reason RecordingPlayer's version is: the resume
        prompt opens a _E2iBox, and onLayoutFinish can fire before
        this screen is fully modal yet (see RecordingPlayer's
        _schedule_resume_check docstring for the full explanation)."""
        if not self._is_vod:
            self._play()
            return
        reactor.callLater(0, self._maybe_prompt_resume)

    def _maybe_prompt_resume(self):
        saved = None
        try:
            saved = _get_resume_pts(self._resume_key)
        except Exception:
            import traceback
            self._log("VOD RESUME LOAD ERROR:\n" + traceback.format_exc())
        if saved and saved >= self._RESUME_MIN_PTS:
            self._pending_resume_pts = saved
            total_secs = int(saved // 90000)
            h, rem = divmod(total_secs, 3600)
            m, s = divmod(rem, 60)
            time_str = "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)
            self.session.openWithCallback(
                self._resume_choice_made,
                ResumeChoiceMenu,
                "Resume from %s?" % time_str,
                detail=self._title or "",
            )
        else:
            self._play()

    def _resume_choice_made(self, resume):
        if not resume:
            self._pending_resume_pts = None
        self._play()

    def _apply_resume_seek(self):
        try:
            service = self.session.nav.getCurrentService()
            seek = service and service.seek()
            if not seek:
                self._resume_seek_attempts += 1
                if self._resume_seek_attempts < 10:
                    reactor.callLater(0.3, self._apply_resume_seek)
                else:
                    self._log("VOD RESUME SEEK gave up — no seek object after retries")
                    self._pending_resume_pts = None
                return
            seek.seekTo(self._pending_resume_pts)
            self._log("VOD RESUME SEEK applied pts=%r" % (self._pending_resume_pts,))
            if self._hud:
                self._hud.reveal()
        except Exception:
            import traceback
            self._log("VOD RESUME SEEK ERROR:\n" + traceback.format_exc())
        finally:
            self._pending_resume_pts = None

    def _save_resume_state(self):
        """Called from _stop(), before the service is torn down."""
        try:
            service = self.session.nav.getCurrentService()
            seek = service and service.seek()
            if not seek:
                return
            pos = seek.getPlayPosition()
            pos_pts = pos[1] if pos and not pos[0] else None
            if pos_pts is None:
                return
            length = seek.getLength()
            len_pts = length[1] if length and not length[0] and length[1] else self._duration_pts
            near_start = pos_pts < self._RESUME_MIN_PTS
            near_end = False
            if len_pts:
                tail = min(self._RESUME_TAIL_PTS, int(len_pts * 0.02))
                near_end = pos_pts >= (len_pts - tail)
            if near_start or near_end:
                _clear_resume_pts(self._resume_key)
            else:
                _set_resume_pts(self._resume_key, pos_pts, len_pts)
            # Separate from the resume near_end check just above (a
            # much narrower 30s/2% window, tuned only for "is
            # resuming still worth offering") — see this module's
            # watched-store section for why "watched" needs its own,
            # more generous threshold and its own permanent store
            # rather than reusing that one.
            if _is_watched_position(pos_pts, len_pts):
                mark_watched(self._resume_key)
        except Exception:
            import traceback
            self._log("VOD RESUME SAVE ERROR:\n" + traceback.format_exc())

    def _play(self):
        _nav_debug("E2iPTVPlayer._play (new instance starting) — id=%r url=%r" % (id(self), getattr(self, '_url', None)))
        from .state import state as _st
        if getattr(_st, 'prev_service_ref', None) is not None:
            # A previous E2iPTVPlayer instance already captured the TRUE
            # original service for this plugin session (see screen1.py's
            # _on_close, which restores this on full plugin exit) — inherit
            # it rather than re-capturing "whatever's playing right now".
            # For a channel switch via the full-EPG "Watch to End" flow,
            # the previous E2iPTVPlayer instance is still stacked
            # underneath and still actively playing at this exact moment
            # (nothing in that chain closes it first), so re-capturing here
            # would wrongly grab THAT stream as "previous" — which is what
            # was causing Exit to revert playback to the channel you just
            # switched away from, rather than truly leaving the plugin.
            self._prev_ref = _st.prev_service_ref
        else:
            self._prev_ref = self.session.nav.getCurrentlyPlayingServiceReference()
            _st.prev_service_ref = self._prev_ref
        # Store previous URL for channel swap
        self._swap_url      = getattr(_st, 'last_play_url',      None)
        self._swap_title    = getattr(_st, 'last_play_title',    "")
        self._swap_channel  = getattr(_st, 'last_play_chan',     None)  # channel obj for swap EPG
        self._swap_provider = getattr(_st, 'last_play_provider', None)  # provider for swap EPG/browse
        self._swap_channels = getattr(_st, 'last_play_channels', None)  # that provider's channel list
        self._swap_cat_id   = getattr(_st, 'last_play_cat_id', '')
        self._cur_channel  = None   # current channel object (set in _play)
        _st.live_player = self  # lets ChannelBrowser (screen 1) find and
        # stop this exact instance before a re-tune commits, instead of
        # leaving it running underneath a newly-opened E2iPTVPlayer — that
        # stacking (two live instances at once) was the actual root cause
        # behind Exit reverting the channel from deeper in this screen
        # chain, however many overlay-level fixes were tried around it.
        _st.last_play_url     = self._url
        _st.last_play_title   = self._title
        # Find and store current channel object for EPG lookup — matches
        # by channel_idx (already correctly resolved via stream_id by
        # play_channel, regardless of whether this is a live or catchup
        # URL) rather than re-matching the URL itself. The URL-match this
        # replaced only ever built and compared LIVE urls, so it silently
        # never matched for catchup playback — self._cur_channel stayed
        # None, which made _open_browser's own "nothing to browse" guard
        # treat catchup playback as if it had no channel at all, silently
        # doing nothing on Left instead of opening screen 1.
        if (
            self._ch_idx is not None
            and self._my_channels
            and 0 <= self._ch_idx < len(self._my_channels)
        ):
            self._cur_channel  = self._my_channels[self._ch_idx]
            _st.last_play_chan = self._cur_channel
        elif not self._is_vod:
            for c in (self._my_channels or []):
                try:
                    if _live_url_matches(self._my_provider, c, self._url):
                        self._cur_channel  = c
                        _st.last_play_chan = c   # persist for swap
                        break
                except Exception:
                    pass
        _st.last_play_provider = self._my_provider   # persist for swap — keeps "current provider" accurate
        _st.last_play_cat_id   = getattr(self, '_my_cat_id', '')  # for Live TV's 0 key
        _st.last_play_channels = self._my_channels

        if self._vod_provider is not None and self._vod_cmd:
            # Re-resolve right before actual use rather than trusting
            # the url resolved back in play_movie()/play_episode() —
            # confirmed directly that Stalker hands back a fresh,
            # different play_token on every single resolution, so
            # however long the resume prompt sat on screen waiting
            # for a choice is exactly how stale that first url has
            # had a chance to get. Falls back to the original url
            # (unchanged behaviour) if this fails for any reason,
            # rather than blocking playback outright over it.
            try:
                from .backend.stalker_client import StalkerClient
                client = StalkerClient(self._vod_provider)
                if self._vod_kind == "series":
                    fresh_url = client.resolve_series_url(self._vod_cmd)
                else:
                    fresh_url = client.resolve_vod_url(self._vod_cmd)
                if fresh_url:
                    self._url = fresh_url
                    _st.last_play_url = self._url
            except Exception:
                import traceback
                self._log("VOD re-resolve ERROR:\n" + traceback.format_exc())

        # Catch-up goes through the local relay that keeps its sound
        # working (catchup_proxy.py); live and VOD play directly.
        from .catchup_proxy import wrap as _cu_wrap
        if getattr(self, "_is_vod", False) or "/timeshift/" in (self._url or ""):
            ref = eServiceReference(4097, 0, _cu_wrap(self._url))
            ref.setName(self._title)
        else:
            ref = make_live_ref(self._url, self._title)   # Settings -> Playback
        self.session.nav.playService(ref)
        if not self._is_vod:
            self._remember_live()
        from twisted.internet import reactor

        if self._is_vod:
            # Reused as-is from RecordingPlayer — see the __init__
            # comment for why this is a straight fit for VOD too.
            self._hud = self.session.instantiateDialog(
                RecordingHud, self._title, "",
                self._description, self._duration_pts
            )
            self._hud.show()
            self._hud.start()
            self._hud.reveal()
            self._skip_osd = self.session.instantiateDialog(RecordingSkipOsd)
            self._skip_osd.show()
            if self._pending_resume_pts:
                self._resume_seek_attempts = 0
                reactor.callLater(0.3, self._apply_resume_seek)
        else:
            reactor.callLater(0.3, self._show_channel_info)

    # ------------------------------------------------------------------
    # Right: the last two live channels
    #
    # One record per channel - provider, channel, category, channel list,
    # link and name together - in state.live_hist ([now, before]), kept
    # across player screens. Every live channel you start goes to the
    # front; Right swaps the two and always asks for a fresh link (a
    # stored Stalker link has usually expired). Reconnects after a freeze
    # don't touch it. (Before, those pieces were kept separately and
    # updated in different places - the Live TV preview changed some but
    # not others - so Right could give the same channel or an older one.)
    # ------------------------------------------------------------------

    @staticmethod
    def _hist_key(provider, channel):
        return (str(getattr(provider, "provider_key", "") or ""),
                str(getattr(channel, "stream_id", "") or getattr(channel, "name", "")))

    def _hist_push(self):
        """The live channel now playing becomes 'now'; what was 'now'
        becomes 'before' (unless it's the same channel again)."""
        if self._is_vod or "/timeshift/" in (self._url or "") or self._cur_channel is None:
            return
        from .state import state as _st
        rec = {"provider": self._my_provider, "channel": self._cur_channel,
               "cat_id": getattr(self, "_my_cat_id", "") or "",
               "channels": self._my_channels, "url": self._url, "title": self._title}
        hist = list(getattr(_st, "live_hist", None) or [])
        cur = hist[0] if hist else None
        if cur and self._hist_key(cur["provider"], cur["channel"]) == \
                self._hist_key(rec["provider"], rec["channel"]):
            hist[0] = rec
        else:
            hist = [rec, cur]
        _st.live_hist = hist[:2]

    def _channel_swap(self):
        if self._is_vod:
            return
        # One swap per press: a press can arrive twice (the remote's
        # repeat, or presses queued while a stream is starting), and a
        # second swap straight away goes back to where you were.
        now = _time.time()
        if now - getattr(self, "_last_swap", 0) < 1.5:
            _live_log("Right: ignored (again within 1.5 s)")
            return
        self._last_swap = now
        from .state import state as _st
        hist = list(getattr(_st, "live_hist", None) or [])
        prev = hist[1] if len(hist) > 1 else None
        if not prev or prev.get("channel") is None:
            _live_log("Right: no previous channel on record")
            return
        _live_log("Right: %s -> %s" % (
            (hist[0] or {}).get("title", "?"), prev.get("title", "?")))
        provider, channel = prev["provider"], prev["channel"]
        url = None
        try:
            url = build_live_url(provider, channel)
        except Exception as e:
            _live_log("swap: fresh link for %s failed (%r)" % (prev.get("title"), e))
        url = url or prev.get("url")
        if not url:
            return
        self._my_provider = provider
        self._my_channels = prev.get("channels") or self._my_channels
        self._my_cat_id = prev.get("cat_id", "")
        self._cur_channel = channel
        self._ch_idx = None
        key = self._hist_key(provider, channel)
        for i, c in enumerate(self._my_channels or []):
            if self._hist_key(provider, c) == key:
                self._ch_idx = i
                break
        self._url, self._title = url, getattr(channel, "name", "") or prev.get("title", "")
        prev = dict(prev, url=url)
        _st.live_hist = [prev, hist[0]]
        _st.last_play_url      = self._url
        _st.last_play_title    = self._title
        _st.last_play_chan     = channel
        _st.last_play_provider = provider
        _st.last_play_cat_id   = self._my_cat_id
        _st.last_play_channels = self._my_channels
        _st.open_prov = provider
        _st.channels  = self._my_channels
        if self._ch_idx is not None:
            _st.b5_focused = self._ch_idx
        ref = make_live_ref(self._url, self._title)   # Settings -> Playback
        self.session.nav.playService(ref)
        self._remember_live(push=False)
        from twisted.internet import reactor as _r
        _r.callLater(0.3, self._show_channel_info)

    def _channel_swap_old(self):
        if self._is_vod:
            return
        if not self._swap_url:
            return
        from .state import state as _st
        # Find current channel object to store for EPG on swap-back
        cur_ch = getattr(self, '_cur_channel', None)
        if cur_ch is None:
            for c in (self._my_channels or []):
                try:
                    if _live_url_matches(self._my_provider, c, self._url):
                        cur_ch = c
                        break
                except Exception:
                    pass
        # Swap URLs
        cur_url, cur_title = self._url, self._title
        self._url, self._title = self._swap_url, self._swap_title
        self._swap_url, self._swap_title = cur_url, cur_title
        # Swap channel objects
        self._cur_channel  = getattr(self, '_swap_channel', None)
        self._swap_channel = cur_ch
        # Swap provider + channel list too — this is what "current provider" (LEFT
        # from Live TV) reads, so it must follow the swap, not just url/title/EPG.
        cur_provider, cur_channels = self._my_provider, self._my_channels
        cur_cat_id = getattr(self, '_my_cat_id', '')
        self._my_provider  = getattr(self, '_swap_provider', None) or cur_provider
        if getattr(self, '_swap_provider', None) is not None:
            self._my_cat_id = getattr(self, '_swap_cat_id', '') or ''
            self._swap_cat_id = cur_cat_id
        self._my_channels  = getattr(self, '_swap_channels', None) or cur_channels
        self._swap_provider, self._swap_channels = cur_provider, cur_channels
        _st.last_play_url      = self._url
        _st.last_play_title    = self._title
        _st.last_play_chan     = self._cur_channel
        _st.last_play_provider = self._my_provider
        _st.last_play_cat_id   = getattr(self, '_my_cat_id', '')
        _st.last_play_channels = self._my_channels
        _st.open_prov = self._my_provider
        _st.channels  = self._my_channels
        # Catch-up goes through the local relay that keeps its sound
        # working (catchup_proxy.py); live and VOD play directly.
        from .catchup_proxy import wrap as _cu_wrap
        if getattr(self, "_is_vod", False) or "/timeshift/" in (self._url or ""):
            ref = eServiceReference(4097, 0, _cu_wrap(self._url))
            ref.setName(self._title)
        else:
            ref = make_live_ref(self._url, self._title)   # Settings -> Playback
        self.session.nav.playService(ref)
        self._remember_live()
        from twisted.internet import reactor as _r
        _r.callLater(0.3, self._show_channel_info)

    def _open_browser(self):
        _nav_debug(
            "E2iPTVPlayer._open_browser (left) fired — id=%r "
            "browser_active_before=%r" % (id(self), getattr(self, '_browser_active', False))
        )
        if self._is_vod:
            return
        if not self._my_channels or self._cur_channel is None:
            return
        # Reordered per spec: LEFT now opens screen 1 (ChannelBrowser, the
        # full program EPG for whatever's actually playing) — screen 2
        # (channel list) and screen 3 (Providers/Categories) are reached
        # from there via further LEFT presses, each refreshing the level
        # above it rather than tuning anything themselves. Only screen 1's
        # own Watch/Record action ever actually tunes.
        self._browser_active = True  # guards _stop() below — see its comment
        self._browser_screen = self.session.openWithCallback(
            self._browser_closed,
            ChannelBrowser,
            self._my_provider,
            self._cur_channel)

    def _browser_closed(self, result=None):
        _nav_debug("E2iPTVPlayer._browser_closed fired — id=%r result=%r" % (id(self), result))
        self._browser_screen = None

        if isinstance(result, tuple) and result and result[0] == 'tune':
            # Screen 1 (ChannelBrowser) closed itself specifically to hand
            # this off — it has now genuinely finished closing, so THIS
            # player instance is the active/modal screen again, which is
            # what session.open() below needs (same "Modal open are
            # allowed only from a screen which is modal!" rule that
            # crashed an earlier version of this).
            #
            # Order here matters for a second, separate reason: stop THIS
            # stream first, before opening the new one — many IPTV
            # providers limit concurrent connections per account, so
            # opening the new stream while this one is still connected
            # risked the new connection being rejected/stuck, which
            # showed as a freeze on the new channel rather than an error.
            # Stopping the service (not closing the screen) drops the
            # connection while keeping this screen itself open/modal, so
            # the session.open() below still satisfies Enigma2's rule —
            # then this screen closes after, once the new one is up,
            # matching that same proven-safe order. Deliberately doesn't
            # restore _prev_ref — switching channels, not leaving the
            # plugin.
            _tag, action, provider, channel, ev = result
            if self._hud:
                try:
                    self._hud.stop()
                    self.session.deleteDialog(self._hud)
                except Exception:
                    pass
                self._hud = None
            if self._skip_osd:
                try:
                    self._skip_osd.stop()
                    self.session.deleteDialog(self._skip_osd)
                except Exception:
                    pass
                self._skip_osd = None
            try:
                self.session.nav.stopService()
            except Exception:
                pass
            execute_ctx_action(
                self.session, action, provider, channel, ev,
                recording_path=state.recording_path,
                rec_start_offset=getattr(state, 'rec_start_offset', 0),
                rec_end_offset=getattr(state, 'rec_end_offset', 0),
                catchup_start_offset=getattr(state, 'catchup_start_offset', 0),
                catchup_end_offset=getattr(state, 'catchup_end_offset', 0),
                epg_store=state.backend.epg_store,
            )
            self.close()
            return

        # Deliberately delayed rather than clearing _browser_active
        # immediately: if Exit reached BOTH this screen chain's own Cancel
        # handling (which legitimately, correctly closes it and lands
        # here) AND this player's own "cancel"/_stop from the exact same
        # physical keypress — the same "one key reaches more than one
        # screen's ActionMap" behaviour already seen elsewhere with </
        # Left — then clearing the guard immediately here meant _stop()
        # could still see it as already False by the time IT ran a
        # moment later, so it ran its full body anyway: stopping
        # playback and closing this player too, dropping all the way
        # down to whatever's underneath (the original Grid EPG) instead
        # of just leaving Live TV playing, which is what Exit from this
        # chain is actually supposed to do. A brief delay covers that
        # same-keypress window without risking suppressing a genuinely
        # separate, later Exit press.
        reactor.callLater(0.3, self._clear_browser_active)

    def _clear_browser_active(self):
        self._browser_active = False

    def _browser_play(self, channel, idx):
        url = build_live_url(self._my_provider, channel)
        self._swap_url     = self._url
        self._swap_title   = self._title
        self._swap_channel = self._cur_channel
        self._ch_idx       = idx
        self._cur_channel  = channel
        from .state import state as _st
        _st.b5_focused = idx
        self._url, self._title = url, channel.name
        ref = make_live_ref(url, channel.name)
        self.session.nav.playService(ref)
        self._remember_live()
        from twisted.internet import reactor as _r
        _r.callLater(0.3, self._show_channel_info)

    def _remember_live(self, push=True):
        """Save the live channel now playing as the one Live TV comes
        back to (last_live.py)."""
        if getattr(self, "_is_vod", False):
            return
        if push:
            self._hist_push()
        self._wd_reset()
        try:
            from .last_live import remember
            remember(self._my_provider, self._cur_channel,
                     getattr(self, "_my_cat_id", ""))
        except Exception:
            pass
        # Watching live full screen: open the plugin here next time.
        try:
            from .b1common import save_b1_last_choice
            save_b1_last_choice("Live TV")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Live TV freeze watchdog
    #
    # Twice a second while a live channel plays full screen: is the stream's
    # clock (play position) still moving? If it has stood still for
    # WD_STALL seconds while not paused - or the box reports the stream
    # ended - the channel is started again (with a fresh link, so Stalker
    # portals hand out a new token). At most WD_MAX_TRIES times in
    # WD_WINDOW seconds, then it stops trying and says so, rather than
    # flapping forever on a channel that's genuinely down. Logged
    # (channel names only) to e2iptv_live.log - Settings -> Status & Logs.
    # Catch-up, Movies, Series and the box-decoder player (no clock to
    # read) aren't watched.
    # ------------------------------------------------------------------

    WD_STALL = 2           # seconds without movement = frozen
    WD_GRACE = 8           # seconds after starting before checking
    WD_MAX_TRIES = 5
    WD_WINDOW = 120

    def _wd_is_live(self):
        # Never once the plugin is closing / closed, or this player is
        # gone: the exit itself stops the stream, which must not look
        # like a dropped channel ("Reconnecting..." then the wrong
        # programme on screen).
        if getattr(self, "_e2i_closed", False):
            return False
        try:
            from .state import state as _st
            if not getattr(_st, "plugin_open", False):
                return False
        except Exception:
            pass
        if getattr(self, "_wd_enabled", None) is None:
            # Settings -> Playback -> Auto-restart frozen channels
            # (read once per player screen; default on)
            try:
                with open(DATA + "/cache/app_settings.json") as f:
                    self._wd_enabled = bool(json.load(f).get("live_watchdog", True))
            except Exception:
                self._wd_enabled = True
        if not self._wd_enabled:
            return False
        return (not getattr(self, "_is_vod", False)
                and "/timeshift/" not in (getattr(self, "_url", "") or ""))

    def _wd_box_busy(self):
        """Shutting down, restarting or in standby: never touch playback
        then (reconnecting during a shutdown locked the box up)."""
        try:
            import Screens.Standby as _sb
            if getattr(_sb, "inTryQuitMainloop", False):
                return True
            if getattr(_sb, "inStandby", None) is not None:
                return True
        except Exception:
            pass
        # Another full screen (the shutdown/restart question, standby...)
        # in front of the player, other than the plugin's own overlays.
        try:
            dlg = self.session.current_dialog
            if dlg is not self and dlg.__class__.__module__.split(".")[0] != "Plugins":
                return True
        except Exception:
            pass
        return False

    def _wd_reset(self):
        """A live channel has just been started (by you or by us)."""
        now = _time.time()
        self._wd_last_pos = None
        self._wd_still_since = None
        self._wd_grace_until = now + self.WD_GRACE
        self._wd_started = now
        try:
            cur = self.session.nav.getCurrentlyPlayingServiceReference()
            self._wd_ref = cur.toString() if cur is not None else None
        except Exception:
            self._wd_ref = None
        # A different channel starts the count again.
        title = getattr(self, "_title", "")
        if getattr(self, "_wd_title", None) != title:
            self._wd_title = title
            self._wd_tries = []
            self._wd_gave_up = False

    def _wd_tick(self):
        try:
            self._wd_check()
        except Exception as e:
            _live_log("watchdog error: %r" % (e,))

    def _wd_check(self):
        if not self._wd_is_live() or self._wd_ref is None:
            return
        if (getattr(self, "_paused", False) or getattr(self, "_wd_gave_up", False)
                or self._wd_box_busy()):
            self._wd_still_since = None
            return
        now = _time.time()
        if now < getattr(self, "_wd_grace_until", 0):
            return
        try:
            cur = self.session.nav.getCurrentlyPlayingServiceReference()
            if cur is None or cur.toString() != self._wd_ref:
                return              # something else is playing now
            seek = self.session.nav.getCurrentService().seek()
            r = seek.getPlayPosition() if seek is not None else None
        except Exception:
            return
        if not r or r[0]:
            # This player gives no clock to watch - say so once per
            # channel, so the log shows why nothing was restarted.
            if getattr(self, "_wd_noclock_for", None) != self._wd_ref:
                self._wd_noclock_for = self._wd_ref
                _live_log("%s: watching not possible - the player in use (type %s) "
                          "gives no play position" % (
                              getattr(self, "_title", "") or "channel",
                              self._wd_ref.split(":", 1)[0] if self._wd_ref else "?"))
            return
        if getattr(self, "_wd_watching_for", None) != self._wd_ref:
            self._wd_watching_for = self._wd_ref
            _live_log("%s: watching for freezes (player type %s)" % (
                getattr(self, "_title", "") or "channel",
                self._wd_ref.split(":", 1)[0] if self._wd_ref else "?"))
        pos = r[1]
        if pos != self._wd_last_pos:
            self._wd_last_pos = pos
            self._wd_still_since = None
            return
        if self._wd_still_since is None:
            self._wd_still_since = now
        elif now - self._wd_still_since >= self.WD_STALL:
            self._wd_restart("picture stopped for %d s" % self.WD_STALL)

    def _wd_restart(self, reason):
        if not self._wd_is_live() or self._wd_ref is None:
            return
        if getattr(self, "_wd_gave_up", False) or self._wd_box_busy():
            return
        try:
            cur = self.session.nav.getCurrentlyPlayingServiceReference()
            # Nothing playing = stopped on purpose (shutdown, standby,
            # another screen) - a frozen stream is still "playing".
            if cur is None or cur.toString() != self._wd_ref:
                return
        except Exception:
            return
        now = _time.time()
        title = getattr(self, "_title", "") or "channel"
        self._wd_tries = [t for t in getattr(self, "_wd_tries", [])
                          if now - t < self.WD_WINDOW] + [now]
        if len(self._wd_tries) > self.WD_MAX_TRIES:
            self._wd_gave_up = True
            _live_log("%s: %s - still failing after %d reconnects, stopped trying"
                      % (title, reason, self.WD_MAX_TRIES))
            _live_note("%s keeps dropping - try it again later or pick another channel"
                       % title)
            return
        _live_log("%s: %s - reconnecting (%d of %d)" % (
            title, reason, len(self._wd_tries), self.WD_MAX_TRIES))
        _live_note("Reconnecting %s..." % title)
        url = self._url
        try:
            # A fresh link only when the channel on record is certainly
            # the one playing; otherwise the same link again - a reconnect
            # must never change channel.
            if (self._my_provider is not None and self._cur_channel is not None
                    and getattr(self._cur_channel, "name", None) == title):
                url = build_live_url(self._my_provider, self._cur_channel) or url
        except Exception as e:
            _live_log("%s: fresh link failed (%r) - using the old one" % (title, e))
        self._url = url
        try:
            from .state import state as _st
            if _st.last_play_url and _st.last_play_title == title:
                _st.last_play_url = url
        except Exception:
            pass
        try:
            self.session.nav.stopService()
        except Exception:
            pass
        ref = make_live_ref(url, title)
        self.session.nav.playService(ref)
        self._wd_reset()

    # ------------------------------------------------------------------
    # 2: stream details - what the channel actually sends
    # ------------------------------------------------------------------

    def _stream_details(self):
        if not self._wd_is_live() or not getattr(self, "_url", ""):
            return
        if getattr(self, "_sd_busy", False):
            return
        self._sd_busy = True
        self._sd_result = None
        url, title = self._url, getattr(self, "_title", "") or "this channel"
        _live_note("Checking %s (a few seconds)..." % title)

        def work():
            lines = []
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": "Enigma2"})
                data = b""
                t0 = _time.time()
                with urllib.request.urlopen(req, timeout=8) as r:
                    while len(data) < 3 * 1024 * 1024 and _time.time() - t0 < 4:
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        data += chunk
                if data[:7] == b"#EXTM3U":
                    lines = ["This channel comes as a playlist of short pieces (HLS),",
                             "which this check can't look inside."]
                else:
                    import importlib.util
                    spec = importlib.util.spec_from_file_location(
                        "e2iptv_ts_streams", os.path.join(
                            os.path.dirname(os.path.abspath(__file__)),
                            "tools", "ts_streams.py"))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    lines = mod.describe(data)
            except Exception as e:
                lines = ["Couldn't sample the stream (%s)." % e.__class__.__name__]
            self._sd_result = (title, lines)

        import threading
        threading.Thread(target=work, name="E2iPTV-StreamDetails", daemon=True).start()
        self._sd_timer = eTimer()
        self._sd_timer.callback.append(self._stream_details_done)
        self._sd_timer.start(250, False)

    def _stream_details_done(self):
        if self._sd_result is None:
            return
        self._sd_timer.stop()
        self._sd_busy = False
        title, lines = self._sd_result
        _live_log("%s: stream details - %s" % (title, lines[-1] if lines else "?"))
        from Screens.MessageBox import MessageBox
        self.session.open(_E2iBox, "%s\n\n%s" % (title, "\n".join(lines)),
                          MessageBox.TYPE_INFO)

    def _go_to_grid(self):
        """Close player without stopping stream — IPTV continues in PiG."""
        if self._is_vod:
            # No equivalent of the Live TV EPG grid for a movie/
            # episode — safest is to do nothing rather than run the
            # channel-index logic below against content that isn't a
            # channel at all.
            return
        import time as _t
        from .state import state as _st
        if self._ch_idx is not None:
            _st.b5_focused = self._ch_idx
        # Reset EPG grid to current time so it doesn't show stale historical view
        now = int(_t.time())
        _st.b5_cursor_time  = now
        _st.b5_window_start = (now // 1800) * 1800 - 1800
        _st.b5_scroll       = max(0, self._ch_idx or 0)
        self.close()

    def _show_channel_info(self):
        from .state import state as _state
        import time as _t
        try:
            if self.session.current_dialog.__class__.__name__ == "ProgrammeOverlay":
                self.session.current_dialog.close()
        except Exception:
            pass
        try:
            ch_name, prog, start, end = self._title, "", 0, 0
            ch = getattr(self, "_cur_channel", None)
            if ch is None:
                for c in (self._my_channels or []):
                    try:
                        if _live_url_matches(self._my_provider, c, self._url):
                            ch = c; break
                    except Exception:
                        pass
            if ch is None:
                for c in (_state.channels or []):
                    try:
                        if _state.open_prov and _live_url_matches(_state.open_prov, c, self._url):
                            ch = c; break
                    except Exception:
                        pass
            cu = getattr(_state, "last_catchup", None)
            if cu and cu.get("url") == self._url:
                # Catch-up: show the programme being replayed.
                if ch is not None:
                    ch_name = ch.name
                prog, start, end = cu.get("title", ""), cu.get("start", 0), cu.get("end", 0)
            elif ch and _state.backend and _state.backend.epg_store:
                ch_name = ch.name
                now = int(_t.time())
                for ev in _state.backend.epg_store.get_for(ch):
                    if ev.start <= now < ev.end:
                        prog, start, end = ev.title, ev.start, ev.end
                        break
            _show_programme_overlay(self.session, ch_name, prog, start, end)
        except Exception:
            pass

    def _instant_record(self):
        """
        Physical Record key on Live TV. No menu — this is deliberately the
        one instant action: "Record To End" on whatever is airing right now.
        A second press while already recording this channel offers the same
        Stop Recording — Delete / Stop Recording — Keep choice as the grid
        and Screen 1, via the shared get_ctx_items/execute_ctx_action pair
        so all three recording entry points can never drift apart again.
        """
        if self._is_vod:
            # No "airing now" concept for a movie/episode — it's
            # already fully available on demand, nothing to record.
            return

        # Temporary diagnostic: confirms whether the Record key actually
        # reaches this handler at all, vs. being intercepted by a native/
        # background handler before it gets here (e.g. a still-open
        # Infobar behind this screen, or a keymap action-name mismatch).
        # Safe to remove once the physical key is confirmed working.
        try:
            with open(LOG("e2iptv_record_key.log"), "a") as _lg:
                _lg.write("%s _instant_record fired\n" % _time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass

        provider = self._my_provider
        ch = getattr(self, "_cur_channel", None)
        if ch is None:
            for c in (self._my_channels or []):
                try:
                    if _live_url_matches(provider, c, self._url):
                        ch = c
                        break
                except Exception:
                    pass
        if provider is None or ch is None:
            return

        ev = None
        try:
            if state.backend and state.backend.epg_store:
                now = int(_time.time())
                for e in state.backend.epg_store.get_for(ch):
                    if e.start <= now < e.end:
                        ev = e
                        break
        except Exception:
            pass

        if ev is None:
            # No current EPG data for this channel — nothing to record to
            # the end of, and instant record has no other mode from Live TV.
            _show_programme_overlay(self.session, ch.name, "No programme info — can't record", 0, 0)
            return

        # Ask the single shared menu-builder whether THIS specific
        # programme is already being recorded — same function the grid
        # and Screen 1 use, so an unrelated recording already running on
        # this channel (e.g. a historical download in progress) can't be
        # mistaken for "the thing airing right now is being recorded".
        items = get_ctx_items(provider, ch, ev)
        actions = set(action for _label, _active, action in items)

        if 'stop_rec_delete' in actions:
            self._instant_rec_provider = provider
            self._instant_rec_channel  = ch
            self._instant_rec_ev       = ev
            self.session.openWithCallback(
                self._instant_record_stop_choice,
                SideMenu,
                "Currently recording",
                [
                    ("Stop Recording — Delete", "stop_rec_delete"),
                    ("Stop Recording — Keep",   "stop_rec_keep"),
                ],
                detail=ch.name,
            )
            return

        execute_ctx_action(
            self.session, 'rec_end', provider, ch, ev,
            recording_path=state.recording_path,
            rec_start_offset=state.rec_start_offset,
            rec_end_offset=state.rec_end_offset,
        )
        _show_programme_overlay(self.session, ch.name, "● REC  " + ev.title, ev.start, ev.end)

    def _instant_record_stop_choice(self, result):
        if not result:
            return
        action   = result[1]
        provider = getattr(self, "_instant_rec_provider", None)
        ch       = getattr(self, "_instant_rec_channel", None)
        ev       = getattr(self, "_instant_rec_ev", None)
        if provider is None or ch is None:
            return
        execute_ctx_action(self.session, action, provider, ch, ev)
        label = ("Recording deleted" if action == "stop_rec_delete"
                 else "Recording stopped")
        _show_programme_overlay(self.session, ch.name, label, 0, 0)

    def _stop(self):
        _nav_debug(
            "E2iPTVPlayer._stop (cancel/stop/tv) fired — id=%r "
            "browser_active=%r" % (id(self), getattr(self, '_browser_active', False))
        )
        if getattr(self, '_browser_active', False):
            # Exit reaching this handler while ChannelBrowser (screen 1)
            # is open — whether that's because Exit itself is somehow
            # also triggering Left on this box (same "one physical key,
            # more than one handler" behaviour seen elsewhere with </
            # Left in this codebase), or because you genuinely opened
            # screen 1 earlier and are now pressing Exit deliberately —
            # either way, per spec Exit from anywhere in this screen
            # chain should close the overlay and land back on Live TV,
            # never touch playback itself. Previously this just returned
            # and did nothing, leaving screen 1 sitting open indefinitely
            # if Exit's "cancel" had reached here without its own
            # "left" — actively closing it here instead guarantees that
            # result immediately, regardless of why this fired.
            browser_screen = getattr(self, '_browser_screen', None)
            if browser_screen is not None:
                try:
                    browser_screen.close('exit_all')
                except Exception:
                    pass
            return
        if self._is_vod:
            # Needs the live seek object, which stopService() below
            # tears down — must happen first.
            self._save_resume_state()
        if self._hud:
            try:
                self._hud.stop()
                self.session.deleteDialog(self._hud)
            except Exception:
                pass
            self._hud = None
        if self._skip_osd:
            try:
                self._skip_osd.stop()
                self.session.deleteDialog(self._skip_osd)
            except Exception:
                pass
            self._skip_osd = None
        if self._is_vod:
            # VOD has no "Grid underneath" to reveal — stopping the
            # service and restoring whatever was playing before this
            # screen opened is still the right behaviour here.
            self.session.nav.stopService()
            if self._prev_ref:
                self.session.nav.playService(self._prev_ref)
        # Live TV: deliberately does NOT touch the service at all here.
        # This screen closing reveals screen1.py's own Grid EPG
        # underneath (still open the whole session, never closed by
        # anything in this chain) — whatever's actually, currently
        # playing should just keep playing and show through the Grid's
        # own "MiniTV" preview, reflecting whatever channel/program was
        # last tuned. Previously this unconditionally stopped the
        # service and restored _prev_ref — which, once _prev_ref
        # started correctly inheriting the ONE true pre-plugin service
        # for the whole session (an earlier fix in this same file), meant
        # every single Exit from Live TV reverted all the way back to
        # whatever was playing before the plugin was ever opened,
        # regardless of how many channels you'd since switched to. That
        # restore only belongs in screen1.py's own close handler, for
        # when the Grid itself — not just this player — closes and the
        # whole plugin is genuinely being left.
        self.close()

    def _toggle_hud(self):
        try:
            if self._hud:
                self._hud.toggle()
        except Exception:
            pass

    def _channel_up(self):   self._switch_channel(-1)
    def _channel_down(self): self._switch_channel(+1)

    def _switch_channel(self, direction):
        if self._is_vod:
            return
        if self._ch_idx is None:
            self._ch_idx = 0
        self._switch_channel_to(self._ch_idx + direction)

    def _switch_channel_to(self, idx):
        if idx is None or not self._my_channels or not self._my_provider:
            return
        if idx < 0 or idx >= len(self._my_channels):
            return
        from .state import state as _state
        self._prev_ch_idx     = self._ch_idx
        self._ch_idx          = idx
        _state.b5_focused     = idx
        _state.open_prov      = self._my_provider
        _state.channels       = self._my_channels
        ch  = self._my_channels[idx]
        url = build_live_url(self._my_provider, ch)
        self._url, self._title = url, ch.name
        # Keep "what's actually playing" in sync on every up/down channel
        # change — without this, self._cur_channel and state.last_play_*
        # stay pinned to whatever channel Live TV was first opened on, so
        # anything that asks "what's playing right now" (instant record,
        # LEFT into the channel browser/schedule, the info overlay's
        # fallback lookup) keeps answering with the wrong channel.
        self._cur_channel         = ch
        _state.last_play_url      = url
        _state.last_play_title    = ch.name
        _state.last_play_chan     = ch
        _state.last_play_provider = self._my_provider
        _state.last_play_cat_id   = getattr(self, '_my_cat_id', '')
        _state.last_play_channels = self._my_channels
        ref = make_live_ref(url, ch.name)
        self.session.nav.playService(ref)
        self._remember_live()
        from twisted.internet import reactor as _r
        _r.callLater(0.3, self._show_channel_info)

    def _skip_back_30(self):    self._seek_relative(-30)
    def _skip_back_120(self):   self._seek_relative(-120)
    def _skip_back_300(self):   self._seek_relative(-300)
    def _skip_forward_30(self): self._seek_relative(30)
    def _skip_forward_120(self):self._seek_relative(120)
    def _skip_forward_300(self):self._seek_relative(300)

    def _pause_only(self):
        self._set_paused(True)

    def _resume_only(self):
        self._set_paused(False)

    def _toggle_pause(self):
        self._set_paused(not self._paused)

    def _set_paused(self, paused):
        try:
            service   = self.session.nav.getCurrentService()
            pauseable = service and service.pause()
            if not pauseable:
                return
            if paused:
                pauseable.pause()
            else:
                pauseable.unpause()
            self._paused = paused
        except Exception:
            pass

    def _seek_relative(self, seconds):
        try:
            service = self.session.nav.getCurrentService()
            seek = service and service.seek()
            if seek:
                # Native convention (see InfoBarGenerics.doSeekRelative):
                # direction is 1 for forward, -1 for backward — NOT 1/0.
                # Passing 0 for a forward seek (the previous code here)
                # is an invalid direction value and is what was causing
                # forward skips (3/6/9) to hang instead of moving.
                direction = 1 if seconds > 0 else -1
                pts = abs(int(seconds * 90000))
                # Read position BEFORE seeking and compute where we're
                # landing ourselves — see RecordingPlayer._seek_relative's
                # docstring for why (decoder lag makes an immediate
                # post-seek re-query unreliable).
                pos = seek.getPlayPosition()
                pos_pts = pos[1] if pos and not pos[0] else 0
                length = seek.getLength()
                len_pts = length[1] if length and not length[0] and length[1] else self._duration_pts
                new_pos_pts = pos_pts + (pts if direction == 1 else -pts)
                new_pos_pts = max(0, new_pos_pts)
                if len_pts:
                    new_pos_pts = min(new_pos_pts, len_pts)
                # The actual seek MUST use this same clamped distance,
                # not the raw requested pts — asking seekRelative for
                # more than actually remains (a 5-minute skip with
                # under 5 minutes left) sends the decoder after a
                # target that doesn't exist. A zero-distance seek
                # (already sitting exactly on the clamped boundary)
                # is skipped entirely rather than calling
                # seekRelative(direction, 0) — no reason to assume a
                # no-op distance is handled cleanly by the native
                # pipeline rather than being its own edge case.
                clamped_pts = abs(new_pos_pts - pos_pts)
                if clamped_pts > 0:
                    seek.seekRelative(direction, clamped_pts)
                if self._skip_osd:
                    self._skip_osd.flash(new_pos_pts, len_pts, seconds)
        except Exception:
            pass


class RecordingHud(Screen):
    """Bottom info bar for RecordingPlayer — channel, title,
    description, progress bar and duration all in one place. Opened via
    session.instantiateDialog() alongside the player (the same
    mechanism Enigma2's own InfoBar uses for its persistent overlays).

    This screen's own footprint is sized to EXACTLY the bar's visible
    area (1920x220, anchored to the bottom of the screen) rather than a
    full 1920x1080 transparent canvas with the bar floating inside it
    as a widget. That earlier full-canvas design is what caused both
    the "fully opaque, can't see video" bug (seen on the Vu+'s skin)
    and the follow-up "blue screen over live TV" bug (seen on the
    Zgemma's Kiddac SlyQ skin) — and even after fixing the screen-level
    opacity (adding transparent="1"), the bar itself stopped rendering
    at all. Working theory: this box's compositor grants video-overlay
    priority per SCREEN, not per widget — a transparent screen gets
    treated as having no visual content and doesn't get composited over
    video at all, while an opaque one does (but then paints its whole
    footprint solid, hence the earlier symptom). RecordingInfoOverlay
    (the box's separate OK-button info panel, no longer used by this
    screen — see reveal()/dismiss()/toggle() below) never hit either
    problem because it was already sized to match its own visible
    content instead of covering the whole screen; this class follows
    that same proven pattern instead of fighting the compositor.

    Not persistent anymore either: shows itself for 5s on open, and OK
    toggles it after that — reveal()/dismiss()/toggle(), driven by
    moving this screen's own root instance on/off the visible canvas
    (x=0 shown, x=3500 hidden), the same convention RecordingSkipOsd
    below uses. This replaced the separate RecordingInfoOverlay pop-up
    for this screen entirely: with the bar itself now carrying the
    title/channel/description, a second overlay for the same
    information was redundant, and OK toggling the bar directly is a
    more direct, more discoverable control than OK opening yet another
    screen on top of it."""

    _ONSCREEN_X  = 0
    _OFFSCREEN_X = 3500
    _Y           = 860
    _AUTO_HIDE_MS = 5000

    skin = (
        '<screen name="RecordingHud" position="3500,860" size="1920,220"'
        ' flags="wfNoBorder" zPosition="100"'
        ' backgroundColor="#0d1117" transparent="0">'
        '<widget name="progress_track" position="20,12" size="1880,8"'
        ' backgroundColor="#30ffffff" zPosition="1" transparent="0"/>'
        '<widget name="progress_fill" position="20,12" size="0,8"'
        ' backgroundColor="E2iRail" zPosition="2" transparent="0"/>'
        '<widget name="info_time"    position="20,26" size="1880,26"'
        ' font="E2iRegular;22" foregroundColor="#ffffff"'
        ' halign="left" valign="center" transparent="1" zPosition="1"/>'
        '<widget name="info_channel" position="20,54" size="1880,30"'
        ' font="E2iRegular;26" foregroundColor="#aaaaaa"'
        ' halign="left" valign="center" transparent="1" zPosition="1"/>'
        '<widget name="info_title"   position="20,86" size="1880,48"'
        ' font="E2iRegular;38" foregroundColor="E2iRail"'
        ' halign="left" valign="center" transparent="1" zPosition="1"/>'
        '<widget name="info_desc"    position="20,136" size="1880,64"'
        ' font="E2iRegular;22" foregroundColor="#aaaaaa"'
        ' halign="left" valign="top" transparent="1" zPosition="1"/>'
        '</screen>'
    )

    @staticmethod
    def _log(msg):
        try:
            with open(LOG("e2iptv_recplay_seek.log"), "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    def __init__(self, session, title, channel_name, description, duration_pts):
        Screen.__init__(self, session)
        self._duration_pts = duration_pts
        self._visible = False
        self["progress_track"] = Label("")
        self["progress_fill"]  = Label("")
        self["info_time"]    = Label("--:-- / --:--")
        self["info_channel"] = Label(channel_name or "")
        self["info_title"]   = Label(title)
        self["info_desc"]    = Label(self._wrap_desc(description))
        self._position_timer = eTimer()
        self._position_timer.callback.append(self._tick)
        self._auto_hide_timer = eTimer()
        self._auto_hide_timer.callback.append(self.dismiss)

    def start(self):
        self._position_timer.start(1000, False)

    def stop(self):
        self._position_timer.stop()
        self._auto_hide_timer.stop()

    def reveal(self):
        """Show the bar and (re)start its 5s auto-hide countdown — used
        both when playback opens and every time OK toggles it back on.
        eTimer restarts its countdown on a re-start rather than
        stacking, so this is safe to call repeatedly."""
        self._visible = True
        self._move(True)
        self._auto_hide_timer.start(self._AUTO_HIDE_MS, True)

    def dismiss(self):
        self._visible = False
        self._auto_hide_timer.stop()
        self._move(False)

    def toggle(self):
        self.dismiss() if self._visible else self.reveal()

    def _move(self, visible):
        try:
            target_x = self._ONSCREEN_X if visible else self._OFFSCREEN_X
            self.instance.move(ePoint(target_x, self._Y))
        except Exception:
            import traceback
            self._log("HUD MOVE ERROR:\n" + traceback.format_exc())

    @staticmethod
    def _wrap_desc(text, max_chars_per_line=140, max_lines=2):
        text = (text or "").strip()
        if not text:
            return ""
        words = text.split()
        lines = []
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            if len(trial) > max_chars_per_line:
                lines.append(cur)
                cur = w
                if len(lines) >= max_lines:
                    break
            else:
                cur = trial
        used_words = sum(len(l.split()) for l in lines)
        truncated = len(lines) >= max_lines and used_words < len(words)
        if len(lines) < max_lines and cur:
            lines.append(cur)
            used_words += len(cur.split())
            truncated = used_words < len(words)
        result = "\n".join(lines[:max_lines])
        if truncated:
            result = result.rstrip() + "…"
        return result

    @staticmethod
    def _fmt_time(seconds):
        if seconds is None:
            return "--:--"
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return "%d:%02d:%02d" % (h, m, s)
        return "%d:%02d" % (m, s)

    def _tick(self):
        """Runs every second while playback is up — drives the progress
        bar and the elapsed/total readout. Prefers the live seek
        object's own getLength() (the actual media length) once it's
        available; falls back to the recording's start/end metadata
        (self._duration_pts) until then, or if it never becomes
        available at all for this service type."""
        try:
            service = self.session.nav.getCurrentService()
            seek = service and service.seek()
            if not seek:
                return
            pos = seek.getPlayPosition()
            pos_pts = pos[1] if pos and not pos[0] else None
            if pos_pts is None:
                return
            # Asking for the length makes the box re-read the end of the
            # file, and on a recording still being written that re-read
            # happens every time - once a second it made playback stall
            # every few seconds. So: ask until a length is known, then
            # only every 30 s (enough for a growing recording's total).
            import time as _tm
            now_t = _tm.time()
            if (getattr(self, "_len_pts", None) is None
                    or now_t - getattr(self, "_len_at", 0) >= 30):
                length = seek.getLength()
                got = length[1] if length and not length[0] and length[1] else None
                if got:
                    self._len_pts, self._len_at = got, now_t
            len_pts = getattr(self, "_len_pts", None)
            if len_pts and pos_pts > len_pts:
                len_pts = pos_pts           # growing file: never show past the end
            if not len_pts:
                len_pts = self._duration_pts
            elapsed = pos_pts // 90000
            if len_pts:
                total = len_pts // 90000
                self["info_time"].setText(
                    "%s / %s" % (self._fmt_time(elapsed), self._fmt_time(total)))
                frac = 0.0 if total <= 0 else max(0.0, min(1.0, elapsed / float(total)))
                try:
                    self["progress_fill"].instance.resize(eSize(int(1880 * frac), 8))
                except Exception:
                    pass
            else:
                self["info_time"].setText(self._fmt_time(elapsed))
        except Exception:
            import traceback
            self._log("HUD POSITION ERROR:\n" + traceback.format_exc())


from .side_menu import SideMenu


class ResumeChoiceMenu(SideMenu):
    """Resume / Restart — the plugin's right-hand panel. Closes True to
    resume, False to restart (Exit also counts as restart, as before)."""

    def __init__(self, session, title, detail=""):
        SideMenu.__init__(self, session, title,
                          [("Resume", True), ("Restart from beginning", False)],
                          detail=detail, value_only=True, cancel_value=False)

class RecordingSkipOsd(Screen):
    """Skip-key feedback — absolute position + a mini progress bar,
    shown briefly on every skip-key press. A separate small screen
    sized to its own visible footprint (1200x140) rather than a widget
    floating inside RecordingHud's old full-canvas design, for the same
    compositor reason RecordingHud itself was just rewritten that way —
    see RecordingHud's docstring above.

    Created once (in RecordingPlayer._play, right alongside the HUD
    bar) and left instantiated for the life of playback. Shown/hidden
    by moving this screen's own root instance between an on-screen
    x and an off-screen one (x=3500) — the same move-based show/hide
    convention already used elsewhere in this file, just applied to a
    whole small screen instead of a widget inside a bigger one, since
    that's the piece that wasn't reliable on this box's compositor."""

    _OFFSCREEN_X = 3500
    _ONSCREEN_X  = 360
    _Y           = 700

    skin = (
        '<screen name="RecordingSkipOsd" position="3500,700" size="1200,140"'
        ' flags="wfNoBorder" zPosition="110"'
        ' backgroundColor="#CC0d1117" transparent="0">'
        '<widget name="skip_time"  position="40,12" size="1120,44"'
        ' font="E2iRegular;32" foregroundColor="#ffffff"'
        ' halign="center" valign="center" transparent="1" zPosition="1"/>'
        '<widget name="skip_track" position="40,72" size="1120,10"'
        ' backgroundColor="#30ffffff" zPosition="1" transparent="0"/>'
        '<widget name="skip_fill"  position="40,72" size="0,10"'
        ' backgroundColor="E2iRail" zPosition="2" transparent="0"/>'
        '</screen>'
    )

    @staticmethod
    def _log(msg):
        try:
            with open(LOG("e2iptv_recplay_seek.log"), "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    def __init__(self, session):
        Screen.__init__(self, session)
        self["skip_time"]  = Label("")
        self["skip_track"] = Label("")
        self["skip_fill"]  = Label("")
        # Auto-hide — 3s, and every fresh skip press just calls
        # .start() again: eTimer restarts its countdown on a re-start
        # rather than stacking, so rapid repeated presses keep the OSD
        # visible and extend it instead of it flickering closed
        # between presses.
        self._hide_timer = eTimer()
        self._hide_timer.callback.append(self._hide)

    def stop(self):
        self._hide_timer.stop()

    @staticmethod
    def _fmt_time(seconds):
        if seconds is None:
            return "--:--"
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return "%d:%02d:%02d" % (h, m, s)
        return "%d:%02d" % (m, s)

    def flash(self, pos_pts, len_pts, delta_seconds):
        """Update content, show, (re)start the auto-hide countdown.
        pos_pts/len_pts are RecordingPlayer's own just-computed
        estimate, not a fresh live query."""
        try:
            elapsed = pos_pts // 90000
            sign = "+" if delta_seconds > 0 else "-"
            delta_str = "%s%s" % (sign, self._fmt_time(abs(delta_seconds)))
            if len_pts:
                total = len_pts // 90000
                self["skip_time"].setText("%s / %s   (%s)" % (
                    self._fmt_time(elapsed), self._fmt_time(total), delta_str))
                frac = 0.0 if total <= 0 else max(0.0, min(1.0, elapsed / float(total)))
                try:
                    self["skip_fill"].instance.resize(eSize(int(1120 * frac), 10))
                except Exception:
                    pass
            else:
                self["skip_time"].setText("%s   (%s)" % (self._fmt_time(elapsed), delta_str))
            self._move(True)
            self._hide_timer.start(3000, True)
        except Exception:
            import traceback
            self._log("SKIP OSD ERROR:\n" + traceback.format_exc())

    def _hide(self):
        self._move(False)

    def _move(self, visible):
        try:
            target_x = self._ONSCREEN_X if visible else self._OFFSCREEN_X
            self.instance.move(ePoint(target_x, self._Y))
        except Exception:
            import traceback
            self._log("SKIP OSD MOVE ERROR:\n" + traceback.format_exc())


class RecordingPlayer(Screen):
    """Plays back a local recording from the Recordings screen.

    This was originally meant to just hand off to Enigma2's own native
    movie player class so trick-play/pause/resume all came for free —
    but that class (Screens.MovieSelection.MoviePlayer) doesn't exist
    on this OpenATV build (confirmed by testing: it's simply not
    exported from that module here), so instead this reuses the exact
    same playback mechanism E2iPTVPlayer already uses successfully for
    live streams — eServiceReference(4097, 0, <path>) plus
    session.nav.playService() — just pointed at a local file path
    instead of a stream URL, with a small dedicated action set (seek,
    pause, stop) rather than E2iPTVPlayer's live-TV-specific ones
    (channel up/down, instant record, etc.) which don't make sense for
    a static recording. There's no resume-from-last-position yet — this
    always starts from the beginning."""

    skin = (
        '<screen name="E2iPTVRecordingPlayer"'
        ' position="0,0" size="1920,1080"'
        ' flags="wfNoBorder"'
        ' backgroundColor="#ff000000"'
        ' zPosition="-1"'
        ' transparent="1">'
        # The title/progress/duration bar and the skip-key OSD used to
        # be widgets right here — moved out to RecordingHud (a separate
        # overlay opened via session.instantiateDialog()) after
        # on-device testing showed widgets embedded in THIS screen never
        # actually render over the playing video, despite being
        # correctly positioned/bound. See RecordingHud's docstring
        # above for the full story. Only the dormant restart-fallback's
        # seek veil is still a plain widget here, since that path isn't
        # currently active.
        '<widget name="seek_veil" position="3500,0" size="1920,1080"'
        ' backgroundColor="E2iPanel" zPosition="30" transparent="0"/>'
        '<widget name="seek_text" position="3500,0" size="1920,1080"'
        ' font="E2iRegular;40" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
        ' halign="center" valign="center" transparent="0" zPosition="31"/>'
        '</screen>'
    )

    @staticmethod
    def _log(msg):
        try:
            with open(LOG("e2iptv_recplay_seek.log"), "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    def __init__(self, session, path, title="", channel_name=None,
                 start=None, end=None, description=None):
        Screen.__init__(self, session)
        self._path     = path
        self._title    = title or ""
        # Metadata purely for the OK-button info panel (RecordingInfoOverlay)
        # — none of this affects playback. All four come straight from the
        # same recording-entry dict the Recordings browser itself builds
        # (see screen2.py's _recordings_load), so "available in Movie
        # Selection" and "available here" are the same data, just passed
        # through the call that opens this screen.
        self._channel_name = channel_name
        self._start        = start
        self._end           = end
        self._description   = description or ""
        self._prev_ref = None
        self._paused   = False
        self._seek_token = 0
        # Fallback duration (pts) from the recording's own start/end
        # metadata, used for the progress bar/readout only until (or if)
        # the live seek object's own getLength() becomes available —
        # that's the more accurate figure once playback is up, since it
        # reflects the actual media rather than the scheduled EPG window.
        self._duration_pts = (
            int((self._end - self._start) * 90000)
            if self._start and self._end else None
        )
        # The bottom bar (RecordingHud) and the skip-key OSD
        # (RecordingSkipOsd) are both separate right-sized overlay
        # screens now, not widgets here — see RecordingHud's docstring.
        # Both are set once _play() opens them, and referenced from
        # _stop()/_seek_relative().
        self._hud = None
        self._skip_osd = None
        # Resume-from-last-position — see the resume-store helpers near
        # the top of this file and _maybe_prompt_resume/_apply_resume_seek/
        # _save_resume_state below.
        self._pending_resume_pts = None
        self._resume_seek_attempts = 0
        self["seek_veil"] = Label("")
        self["seek_text"] = Label("Seeking…")
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "InfobarSeekActions",
             "NumberActions", "InfobarActions", "LeavePlayerActions"],
            {
                "cancel": self._stop,
                "stop":   self._stop,
                "tv":     self._stop,
                # See E2iPTVPlayer's identical binding for why this is
                # needed alongside "stop" — the physical dedicated
                # Stop/"X" button fires this action specifically on
                # some remotes, not the generic one.
                "leavePlayer": self._stop,
                # OK now toggles the bottom bar (channel/title/
                # description/progress) open and closed directly,
                # rather than opening a separate info overlay on top of
                # it — see RecordingHud's docstring for why.
                "ok":     self._toggle_hud,
                "left":   self._skip_back_30,
                "right":  self._skip_forward_30,
                "1": self._skip_back_30,    "4": self._skip_back_120,
                "7": self._skip_back_300,   "3": self._skip_forward_30,
                "6": self._skip_forward_120, "9": self._skip_forward_300,
                # << / >> — same native action names InfoBarSeek uses for
                # the physical rewind/fast-forward keys. Deliberately
                # bound to plain seekRelative() jumps (another rung on
                # the same ladder as 1/4/7  3/6/9) rather than true
                # multi-speed trick-play: variable-speed FF/RW is a much
                # bigger, fiddlier piece of native machinery, and given
                # how much trouble plain seeking gave us before, staying
                # on the mechanism we've already proven solid is the
                # safer choice for now.
                "seekFwd":  self._skip_forward_600,
                "seekBack": self._skip_back_600,
                # Physical Pause/Play buttons — the standard native
                # action names for these (same ones InfoBarGenerics'
                # InfoBarSeek binds for Live TV/native Movie Player),
                # already reachable here since "InfobarSeekActions" is
                # one of the contexts above. pauseService/unPauseService
                # fire on the dedicated Pause/Play keys; playpauseService
                # covers remotes that only have one combined key.
                "pauseService":     self._pause_only,
                "unPauseService":   self._resume_only,
                "playpauseService": self._toggle_pause,
            }, -1)
        self.onLayoutFinish.append(self._schedule_resume_check)

        # Detects natural end-of-playback so this closes cleanly on
        # its own, the same way Stop/cancel/tv already do, rather than
        # sitting frozen on the last frame waiting for the user to
        # press Exit manually — this screen had no service-event
        # handling of any kind before. Always applies here (unlike
        # E2iPTVPlayer's own identical handler, which is deliberately
        # VOD-only) since a recording played back from disk is always
        # a finite file — there's no Live TV case to accidentally
        # catch a transient buffering blip on.
        self.session.nav.event.append(self.__serviceEvent)
        self.onClose.append(self.__removeServiceEventHandler)

        # Same physical Stop/"X" button fix as E2iPTVPlayer's identical
        # addition — see its own comment for the full reasoning.
        self.__key_stop_handler = _make_key_stop_handler(self._stop)
        eActionMap.getInstance().bindAction("", -0x7FFFFFFF, self.__key_stop_handler)
        self.onClose.append(self.__removeKeyStopHandler)

    def __removeKeyStopHandler(self):
        try:
            eActionMap.getInstance().unbindAction("", self.__key_stop_handler)
        except Exception:
            pass

    def __serviceEvent(self, evt):
        if evt == iPlayableService.evEOF:
            self._stop()

    def __removeServiceEventHandler(self):
        try:
            self.session.nav.event.remove(self.__serviceEvent)
        except Exception:
            pass

    # How far in (and how close to the end) a saved position needs to be
    # to actually offer a resume — a few seconds in, or the last few
    # seconds of the recording, isn't worth prompting for.
    _RESUME_MIN_PTS  = 30 * 90000   # don't offer to resume under 30s in
    _RESUME_TAIL_PTS = 30 * 90000   # treat the last 30s as "finished"

    def _schedule_resume_check(self):
        """onLayoutFinish hook (replaces the old direct self._play()).

        Deferred by a zero-delay reactor.callLater rather than calling
        _maybe_prompt_resume directly: _maybe_prompt_resume can open a
        MessageBox (session.openWithCallback), and onLayoutFinish can
        fire while the OUTER session.open() that created THIS screen
        (from screen2.py's _recordings_play) is still unwinding — before
        this screen is fully registered as the session's current modal
        dialog. Enigma2's Session.open() guards against being called
        from a screen that isn't yet modal, and hits exactly that guard
        in this timing window: 'Modal open are allowed only from a
        screen which is modal!' — confirmed via /tmp/e2iptv_play.log the
        first time the resume prompt actually had something to show
        (it never fired on a file with no saved position, so the first
        two test plays didn't hit it — only reopening a previously
        played file did). A zero-delay callLater runs on the next
        reactor iteration, by which point the outer open() has returned
        and this screen is genuinely modal, so the resume prompt's own
        session.openWithCallback() is safe."""
        reactor.callLater(0, self._maybe_prompt_resume)

    def _maybe_prompt_resume(self):
        """Checks the resume store for this recording's path and, if there's
        a saved position worth resuming, asks before starting playback
        rather than always starting from the beginning. This dialog opens
        before playService() is ever called, so it's a plain screen-over-
        screen MessageBox with no video underneath yet — none of the
        video-overlay compositing issues RecordingHud had apply here."""
        saved = None
        try:
            saved = _get_resume_pts(self._path)
        except Exception:
            import traceback
            self._log("RESUME LOAD ERROR:\n" + traceback.format_exc())
        _resume_log("OPEN %s saved=%s" % (
            os.path.basename(self._path),
            "none" if not saved else "%ds" % (saved // 90000)))
        if saved and saved >= self._RESUME_MIN_PTS:
            self._pending_resume_pts = saved
            total_secs = int(saved // 90000)
            h, rem = divmod(total_secs, 3600)
            m, s = divmod(rem, 60)
            time_str = "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)
            self.session.openWithCallback(
                self._resume_choice_made,
                ResumeChoiceMenu,
                "Resume from %s?" % time_str,
                detail=self._title or "",
            )
        else:
            self._play()

    def _resume_choice_made(self, resume):
        _resume_log("CHOICE resume=%r" % (resume,))
        if not resume:
            self._pending_resume_pts = None
        self._play()

    def _apply_resume_seek(self):
        """Jump to the saved position once the player is ready. Right after
        playService() the seek object usually isn't there yet, so this
        retries for up to ~10 seconds. (It used to clear the saved
        position after the first failed try, so the retry had nothing
        to jump to and the recording always started from the beginning.)"""
        target = self._pending_resume_pts
        if not target:
            return
        try:
            service = self.session.nav.getCurrentService()
            seek = service and service.seek()
        except Exception:
            seek = None
        if not seek:
            self._resume_seek_attempts += 1
            if self._resume_seek_attempts < 33:
                reactor.callLater(0.3, self._apply_resume_seek)
            else:
                _resume_log("SEEK gave up - player never ready (%s)"
                            % os.path.basename(self._path))
                self._pending_resume_pts = None
            return
        self._pending_resume_pts = None
        try:
            seek.seekTo(target)
            self._log("RESUME SEEK applied pts=%r" % (target,))
            _resume_log("SEEK %s to %ds (after %d waits)" % (
                os.path.basename(self._path), target // 90000,
                self._resume_seek_attempts))
            if self._hud:
                self._hud.reveal()
        except Exception:
            import traceback
            _resume_log("SEEK ERROR:\n" + traceback.format_exc())
            return
        # The player can still ignore a jump made this early; check it
        # landed and try again if it didn't.
        reactor.callLater(1.5, self._verify_resume_seek, target, 1)

    def _verify_resume_seek(self, target, attempt):
        seek = None
        try:
            service = self.session.nav.getCurrentService()
            seek = service and service.seek()
            pos = seek.getPlayPosition() if seek else None
            now_pts = pos[1] if pos and not pos[0] else None
        except Exception:
            now_pts = None
        if now_pts is not None and now_pts >= target - 10 * 90000:
            _resume_log("SEEK ok at %ds (try %d)" % (now_pts // 90000, attempt))
            return
        if attempt >= 4 or not seek:
            _resume_log("SEEK gave up, at %s (try %d)" % (
                "?" if now_pts is None else "%ds" % (now_pts // 90000), attempt))
            return
        try:
            seek.seekTo(target)
        except Exception:
            pass
        _resume_log("SEEK retry %d (was at %s)" % (
            attempt + 1, "?" if now_pts is None else "%ds" % (now_pts // 90000)))
        reactor.callLater(1.5, self._verify_resume_seek, target, attempt + 1)

    def _save_resume_state(self):
        """Called from _stop(), before the service is torn down — reads
        the current position and either saves it (meaningfully into the
        recording) or clears any previously saved one (barely started, or
        finished/near the end — no point prompting to resume either
        case)."""
        try:
            service = self.session.nav.getCurrentService()
            seek = service and service.seek()
            if not seek:
                return
            pos = seek.getPlayPosition()
            pos_pts = pos[1] if pos and not pos[0] else None
            if pos_pts is None:
                return
            length = seek.getLength()
            seek_len = length[1] if length and not length[0] and length[1] else 0
            # The plugin's own recordings have no index file, so the box
            # often reports no length or a wrong (too short) one. Only
            # trust a length that is longer than where we are now and at
            # least a minute; otherwise use the length from the
            # recording's details; if neither is usable, never treat the
            # position as "finished" (that used to delete it every time).
            len_pts = 0
            for cand in (seek_len, self._duration_pts or 0):
                if cand and cand > pos_pts and cand >= 60 * 90000:
                    len_pts = cand
                    break
            near_start = pos_pts < self._RESUME_MIN_PTS
            near_end = False
            if len_pts:
                tail = min(self._RESUME_TAIL_PTS, int(len_pts * 0.02))
                near_end = pos_pts >= (len_pts - tail)
            if near_start or near_end:
                _clear_resume_pts(self._path)
            else:
                _set_resume_pts(self._path, pos_pts, len_pts)
            _resume_log("SAVE %s pos=%ds seek_len=%ds dur=%ds -> %s" % (
                os.path.basename(self._path), pos_pts // 90000,
                seek_len // 90000, (self._duration_pts or 0) // 90000,
                "cleared (start)" if near_start else
                "cleared (end)" if near_end else "saved"))
        except Exception:
            import traceback
            self._log("RESUME SAVE ERROR:\n" + traceback.format_exc())

    # Type 1 (eServiceFactoryDVB — the same pipeline live TV and native
    # PVR recordings use) only understands genuine MPEG transport
    # streams. It has full native trickplay built in and is what fixed
    # every freeze/jump we fought in this screen — but feed it a
    # non-TS container (.mkv, .mp4, etc.) and it doesn't fail to seek,
    # it fails to even parse the file, hence an immediate freeze rather
    # than a seek-time one. Only .ts/.m2ts/.stream (real transport
    # streams) get type 1; everything else falls back to 4097
    # (ServiceMP3/gstreamer) — the only backend on this box that can
    # actually open those other containers at all, at the cost of the
    # weaker seeking we moved away from for type-1 files.
    _NATIVE_TS_EXTS = (".ts", ".m2ts", ".stream")

    @classmethod
    def _service_type_for(cls, path):
        ext = os.path.splitext(path)[1].lower()
        return 1 if ext in cls._NATIVE_TS_EXTS else 4097

    def _play(self):
        self._prev_ref = self.session.nav.getCurrentlyPlayingServiceReference()
        service_type = self._service_type_for(self._path)
        self._log("PLAY service_type=%r path=%r" % (service_type, self._path))
        ref = eServiceReference(service_type, 0, self._path)
        ref.setName(self._title)
        self.session.nav.playService(ref)
        # instantiateDialog (not session.open) — persistent overlay
        # dialogs alongside this screen, not modals pushed on top of it.
        # See RecordingHud's docstring for why this replaced plain
        # widgets embedded in this screen's own skin.
        self._hud = self.session.instantiateDialog(
            RecordingHud, self._title, self._channel_name,
            self._description, self._duration_pts)
        self._hud.show()
        self._hud.start()
        # Visible for 5s on open, then auto-hides; OK brings it back
        # (see _toggle_hud).
        self._hud.reveal()
        self._skip_osd = self.session.instantiateDialog(RecordingSkipOsd)
        self._skip_osd.show()
        if self._pending_resume_pts:
            self._resume_seek_attempts = 0
            reactor.callLater(0.3, self._apply_resume_seek)

    def _stop(self):
        # Save (or clear) the resume position before anything else here —
        # needs the live seek object, which stopService() below tears down.
        self._save_resume_state()
        # Guarded: previously, if HUD teardown ever raised here (e.g. a
        # deleteDialog quirk on a particular skin/box), the exception
        # would abort _stop() right there and self.close() below would
        # never run — the screen would just sit on-screen with no
        # visible error, which matches the earlier "cannot seem to
        # close it" report. Tearing both overlays down is best-effort
        # now; the service stop and screen close always happen
        # regardless.
        if self._hud:
            try:
                self._hud.stop()
                self.session.deleteDialog(self._hud)
            except Exception:
                import traceback
                self._log("HUD TEARDOWN ERROR:\n" + traceback.format_exc())
            self._hud = None
        if self._skip_osd:
            try:
                self._skip_osd.stop()
                self.session.deleteDialog(self._skip_osd)
            except Exception:
                import traceback
                self._log("SKIP OSD TEARDOWN ERROR:\n" + traceback.format_exc())
            self._skip_osd = None
        self.session.nav.stopService()
        if self._prev_ref:
            self.session.nav.playService(self._prev_ref)
        self.close()

    def _pause_only(self):
        self._set_paused(True)

    def _resume_only(self):
        self._set_paused(False)

    def _toggle_pause(self):
        self._set_paused(not self._paused)

    def _set_paused(self, paused):
        try:
            service   = self.session.nav.getCurrentService()
            pauseable = service and service.pause()
            if not pauseable:
                return
            if paused:
                pauseable.pause()
            else:
                pauseable.unpause()
            self._paused = paused
            self._log("PAUSE set paused=%r" % (paused,))
        except Exception:
            import traceback
            self._log("PAUSE ERROR:\n" + traceback.format_exc())

    def _toggle_hud(self):
        """OK button — toggles the bottom bar (RecordingHud) open and
        closed directly. Used to open a separate RecordingInfoOverlay
        pop-up here instead; now that the bar itself carries channel/
        title/description, a second overlay showing the same
        information was redundant, and toggling the bar in place is a
        more direct control than opening something else on top of it."""
        try:
            if self._hud:
                self._hud.toggle()
                self._log("HUD toggle — now visible=%r" % (self._hud._visible,))
        except Exception:
            import traceback
            self._log("HUD TOGGLE ERROR:\n" + traceback.format_exc())

    def _skip_back_30(self):     self._seek_relative(-30)
    def _skip_back_120(self):    self._seek_relative(-120)
    def _skip_back_300(self):    self._seek_relative(-300)
    def _skip_forward_30(self):  self._seek_relative(30)
    def _skip_forward_120(self): self._seek_relative(120)
    def _skip_forward_300(self): self._seek_relative(300)
    # << / >> — one rung bigger than 3/6/9's 5-minute top jump.
    def _skip_back_600(self):    self._seek_relative(-600)
    def _skip_forward_600(self): self._seek_relative(600)

    def _seek_relative(self, seconds):
        """Seek directly within the live session — seek.seekRelative(),
        the normal native mechanism. Every previous freeze/jump we
        chased in this screen was seen with service type 4097
        (ServiceMP3/gstreamer); now that _play() opens the recording as
        type 1 (the same DVB pipeline OpenATV's own native Movie Player
        uses, confirmed working on every button on this exact file),
        real in-session seeking should behave the same way it does
        there — no restart, no veil, no jump. Direction convention
        confirmed against InfoBarGenerics.doSeekRelative: 1 = forward,
        -1 = backward."""
        try:
            service = self.session.nav.getCurrentService()
            seek = service and service.seek()
            if not seek:
                self._log("SEEK skipped — no seek object yet (seconds=%r)" % (seconds,))
                return
            direction = 1 if seconds > 0 else -1
            pts = abs(int(seconds * 90000))
            self._log("SEEK seekRelative direction=%r pts=%r (seconds=%r)" %
                       (direction, pts, seconds))
            # Read position BEFORE seeking and compute where we're
            # landing ourselves, rather than re-querying getPlayPosition()
            # right after seekRelative() — the decoder hasn't necessarily
            # caught up yet at that instant, so a live re-query can show
            # stale/pre-seek numbers for a moment. Our own arithmetic is
            # instant and accurate regardless of decoder lag.
            pos = seek.getPlayPosition()
            pos_pts = pos[1] if pos and not pos[0] else 0
            length = seek.getLength()
            len_pts = length[1] if length and not length[0] and length[1] else self._duration_pts
            new_pos_pts = pos_pts + (pts if direction == 1 else -pts)
            new_pos_pts = max(0, new_pos_pts)
            if len_pts:
                new_pos_pts = min(new_pos_pts, len_pts)
            # Same fix as E2iPTVPlayer's own _seek_relative — the
            # actual seek must use this clamped distance, not the raw
            # requested pts, or overshooting past the end freezes
            # playback instead of just stopping there. Zero-distance
            # (already on the boundary) skips the call entirely — see
            # that same method's comment for why.
            clamped_pts = abs(new_pos_pts - pos_pts)
            if clamped_pts > 0:
                seek.seekRelative(direction, clamped_pts)
            self._log("SEEK seekRelative returned OK")
            if self._skip_osd:
                self._skip_osd.flash(new_pos_pts, len_pts, seconds)
        except Exception:
            import traceback
            self._log("SEEK ERROR:\n" + traceback.format_exc())

    def _seek_relative_via_restart(self, seconds):
        """DORMANT FALLBACK — not called anymore. Kept in case type-1
        in-session seeking turns out to freeze too, so we can switch
        _seek_relative back to calling this instead without having to
        re-derive it. Restart-based: stop, replay this file fresh, then
        apply an absolute seekTo() once the new session is up, with a
        veil to mask the visual jump."""
        try:
            service = self.session.nav.getCurrentService()
            seek = service and service.seek()
            current_pts = 0
            if seek:
                pos = seek.getPlayPosition()
                if pos and not pos[0]:
                    current_pts = pos[1]
            target = current_pts + int(seconds * 90000)
            if target < 0:
                target = 0
            self._log("SEEK(restart) request seconds=%r current_pts=%r target=%r" %
                       (seconds, current_pts, target))
            self._seek_token += 1
            token = self._seek_token
            self._show_seek_veil(True)
            reactor.callLater(0.2, self._restart_at, token, target)
        except Exception:
            import traceback
            self._log("SEEK(restart) ERROR:\n" + traceback.format_exc())

    def _restart_at(self, token, target_pts):
        if token != self._seek_token:
            return  # superseded by a newer skip press meanwhile
        self._paused = False
        try:
            self.session.nav.stopService()
        except Exception:
            pass
        try:
            ref = eServiceReference(4097, 0, self._path)
            ref.setName(self._title)
            self.session.nav.playService(ref)
        except Exception:
            import traceback
            self._log("RESTART PLAY ERROR:\n" + traceback.format_exc())
            self._show_seek_veil(False)
            return
        reactor.callLater(0.6, self._apply_pending_seek, token, target_pts)

    def _apply_pending_seek(self, token, target_pts):
        if token != self._seek_token:
            return  # superseded by a newer skip press meanwhile — that
            # newer press's own _restart_at already re-showed the veil
            # and will hide it itself once it finishes, so leave it be.
        try:
            service = self.session.nav.getCurrentService()
            seek = service and service.seek()
            if seek:
                seek.seekTo(target_pts)
                self._log("SEEK applied target=%r" % (target_pts,))
            else:
                self._log("SEEK apply skipped — no seek object yet")
        except Exception:
            import traceback
            self._log("APPLY SEEK ERROR:\n" + traceback.format_exc())
        finally:
            # A short extra beat before revealing so the just-applied
            # seek has actually taken visual effect, rather than
            # dropping the veil on the exact frame the jump happens.
            reactor.callLater(0.3, self._hide_seek_veil)

    def _hide_seek_veil(self):
        self._show_seek_veil(False)

    def _show_seek_veil(self, visible):
        try:
            x = 0 if visible else 3500
            self["seek_veil"].instance.move(ePoint(x, 0))
            self["seek_text"].instance.move(ePoint(x, 0))
        except Exception:
            pass


def _bq_real_provider(provider, channel):
    """My Bouquets: a bouquet channel is played/recorded/listed through
    its own provider (see backend/bouquets.py)."""
    real = getattr(channel, "_bq_provider", None)
    return real if real is not None else provider


# -----------------------------------------------------------------------------
# Live TV player choice (Settings -> Playback)
#
# Live channels used to play only through the box's general media player
# (type 4097, GStreamer), where sound can drift out of step with the
# picture over time - a channel change resets it. The alternatives:
#   1     the box's own TV decoder (the one satellite/cable channels use),
#         which keeps sound and picture locked to the stream's clock
#   5002  ServiceApp's exteplayer3, 5001 ServiceApp's gstplayer - only if
#         the ServiceApp plugin is installed
# Catch-up, Movies, Series and recordings are not affected (they need
# skipping, which the live-only options don't all support).
# -----------------------------------------------------------------------------

def _live_log(msg):
    """Live TV reconnects - channel names only, never stream links."""
    try:
        path = LOG("e2iptv_live.log")
        with open(path, "a") as f:
            f.write("%s  %s\n" % (_time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _live_note(text):
    """A brief note on screen that doesn't take over the remote."""
    try:
        from Tools import Notifications
        from Screens.MessageBox import MessageBox
        Notifications.AddPopup(text, MessageBox.TYPE_INFO, 3, "E2iPTV_live")
    except Exception:
        pass


LIVE_PLAYERS = [
    (4097, "Standard (GStreamer)"),
    (1, "Box TV decoder"),
    (5002, "ServiceApp exteplayer3"),
    (5001, "ServiceApp gstplayer"),
]


def serviceapp_installed():
    return os.path.isdir("/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp")


def available_live_players():
    return [(t, n) for t, n in LIVE_PLAYERS
            if t in (4097, 1) or serviceapp_installed()]


def live_player_type():
    try:
        with open(DATA + "/cache/app_settings.json") as f:
            t = int(json.load(f).get("live_player", 4097))
    except Exception:
        t = 4097
    if t not in [x for x, _n in available_live_players()]:
        t = 4097
    return t


def set_live_player_type(t):
    path = DATA + "/cache/app_settings.json"
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        data = {}
    data["live_player"] = int(t)
    with open(path, "w") as f:
        json.dump(data, f)


def make_live_ref(url, name=""):
    """Service reference for a LIVE channel, using the chosen player."""
    t = live_player_type()
    # The box decoder reads plain http streams only.
    if t == 1 and not url.startswith("http://"):
        t = 4097
    if t == 4097:
        ref = eServiceReference(4097, 0, url)
    else:
        ref = eServiceReference("%d:0:1:0:0:0:0:0:0:0:%s:%s" % (
            t, url.replace(":", "%3a"), (name or "").replace(":", " ")))
    ref.setName(name or "")
    return ref


def build_live_url(provider, channel):
    """Live stream URL for the given channel.

    Stalker portals never hand back a directly-playable URL when the
    channel list itself loads (see live_channel_reader.py's own
    comment) — channel.direct_source is just the portal's internal
    "cmd" reference for it, not a real stream URL at all. Resolving it
    into one is a genuine network call (create_link), done here at
    actual play time rather than for every channel up front — matches
    how a real STB only ever resolves the one channel actually being
    tuned, not the whole list. Xtream needs none of this — its own
    URL is fully predictable from fields already in hand, so this
    stays the same simple, no-network string format for it."""
    provider = _bq_real_provider(provider, channel)  # bouquet channel -> its own provider
    if getattr(provider, "is_bouquet", False):
        return ""           # bouquet channel whose provider is gone
    if provider.source_type == "STALKER":
        from .backend.stalker_client import StalkerClient, StalkerError
        try:
            return StalkerClient(provider).resolve_stream_url(channel.direct_source)
        except StalkerError:
            # Left for the normal "couldn't start playback" handling
            # already in place wherever this is called from — an
            # empty/unplayable result surfaces the same way a dead
            # Xtream URL already does, rather than needing its own,
            # separate error path here.
            return ""
        except Exception as e:
            # Portal unreachable (no internet / DNS, login failing, ...):
            # this used to crash the whole box. Say so instead and leave
            # it to the same "nothing to play" handling as above.
            name = getattr(provider, "name", "") or "the portal"
            _live_log("%s: can't get a link for %s - %s" % (
                name, getattr(channel, "name", "?"), e))
            import threading as _th
            if _th.current_thread() is _th.main_thread():
                _live_note("Can't reach %s - check the box's internet connection" % name)
            return ""

    return "%s/live/%s/%s/%s.ts" % (
        provider.base_url,
        provider.username,
        provider.password,
        channel.stream_id,
    )


def _preread_guides(channels, limit=60):
    """Background thread: read these channels' saved guides from disk
    into memory, so drawing the list doesn't have to (that was the pause
    opening the channel list the first time after a restart)."""
    try:
        store = state.backend.epg_store
    except Exception:
        return
    for ch in list(channels or [])[:limit]:
        try:
            store.get_for(ch)
        except Exception:
            pass


def _slow_note(what, t0, detail=""):
    ms = (_time.time() - t0) * 1000
    if ms > 150:
        try:
            with open(LOG("e2iptv_slow.log"), "a") as f:
                f.write("%s %-22s %5.0f ms  %s\n" % (_time.strftime("%H:%M:%S"), what, ms, detail))
        except Exception:
            pass


def _live_url_matches(provider, channel, url):
    """True if `url` is this channel's live URL — WITHOUT any network
    call. These comparisons run in loops over the whole channel list
    on the GUI thread. For Stalker, build_live_url() is a create_link
    request per channel, and the portal returns a new play_token every
    time, so the comparison could never match anyway — it just froze
    the GUI for minutes (the spinner before VOD playback). Stalker
    therefore never matches here; callers already fall back to
    channel_idx / stream_id where they can."""
    provider = _bq_real_provider(provider, channel)  # bouquet channel -> its own provider
    if not url or getattr(provider, "source_type", "") == "STALKER":
        return False
    try:
        return build_live_url(provider, channel) == url
    except Exception:
        return False


def build_catchup_url(provider, channel, ev):
    """
    Timeshift/catchup URL for a specific EPG event.
    Start exactly at ev.start (no buffer) to avoid playing the previous programme.
    """
    provider = _bq_real_provider(provider, channel)  # bouquet channel -> its own provider
    if getattr(provider, "is_bouquet", False):
        return ""           # bouquet channel whose provider is gone
    # The guide shows times with the provider's EPG offset added (Settings
    # -> provider -> EPG Offset), but the server keeps its recordings by
    # its own clock — so take the offset back off when asking for one.
    # Without this, a provider with a +5 offset was asked for a time five
    # hours after the programme aired (often still in the future).
    offset      = int(getattr(provider, "epg_offset", 0) or 0) * 3600
    start_local = _time.localtime(ev.start - offset)
    dur         = max(1, (ev.end - ev.start) // 60 + 5)
    start_str   = _time.strftime("%Y-%m-%d:%H-%M", start_local)

    url = "%s/timeshift/%s/%s/%d/%s/%s.ts" % (
        provider.base_url,
        provider.username,
        provider.password,
        dur,
        start_str,
        channel.stream_id,
    )
    try:
        with open(LOG("e2iptv_catchup.log"), "a") as _f:
            _f.write("%s %s / %s: programme %s, offset %+dh -> %s\n" % (
                _time.strftime("%H:%M:%S"), getattr(provider, "name", ""),
                getattr(channel, "name", ""),
                _time.strftime("%a %H:%M", _time.localtime(ev.start)),
                offset // 3600, url.replace(str(provider.password), "***")
                      .replace("/%s/" % provider.username, "/***/")))
    except Exception:
        pass
    # Remember which programme this catch-up address is for, so the
    # player's info bar shows it (not whatever is on live right now).
    try:
        from .state import state as _st
        _st.last_catchup = {"url": url, "title": getattr(ev, "title", ""),
                            "start": ev.start, "end": ev.end}
    except Exception:
        pass
    return url


# Helper to show InfoOverlay with programme details
def _show_programme_overlay(session, channel_name, programme_name, start_time, end_time):
    line1 = channel_name
    line2 = programme_name
    if start_time and end_time:
        line3 = "%s  →  %s" % (
            _time.strftime("%H:%M", _time.localtime(start_time)),
            _time.strftime("%H:%M", _time.localtime(end_time))
        )
    else:
        line3 = ""
    session.open(ProgrammeOverlay, line1, line2, line3, 2000)


def watch_from_start(session, provider, channel, ev):
    """Force catchup from programme start regardless of current time."""
    provider = _bq_real_provider(provider, channel)  # bouquet channel -> its own provider
    if getattr(provider, "is_bouquet", False):
        return None           # bouquet channel whose provider is gone
    url   = build_catchup_url(provider, channel, ev)
    title = "%s — %s (from start)" % (channel.name, ev.title)
    session.open(E2iPTVPlayer, url, title)
    _show_programme_overlay(session, channel.name, ev.title, ev.start, ev.end)


def build_series_url(provider, episode):
    """VOD stream URL for a specific series episode.

    Stalker equivalent of build_movie_url's own reasoning —
    episode.direct_source is "<season_cmd>|<episode_number>" (see
    SeriesInfoReader._read_stalker), resolved into an actual url via
    resolve_series_url at play time, same pattern as movies."""
    if provider.source_type == "STALKER":
        from .backend.stalker_client import StalkerClient, StalkerError
        try:
            return StalkerClient(provider).resolve_series_url(episode.direct_source)
        except StalkerError:
            return ""

    return "%s/series/%s/%s/%s.%s" % (
        provider.base_url,
        provider.username,
        provider.password,
        episode.episode_id,
        episode.container_extension or "mp4",
    )


def build_movie_url(provider, movie):
    """VOD stream URL for a specific movie.

    Stalker portals never hand back a directly-playable URL from the
    movie list itself — movie.direct_source is just the portal's
    internal "cmd" reference (see MovieReader._read_stalker), same
    pattern as build_live_url's own channel.direct_source. Resolving
    it into an actual URL is a genuine network call (create_link),
    done here at actual play time rather than for every movie up
    front."""
    if provider.source_type == "STALKER":
        from .backend.stalker_client import StalkerClient, StalkerError
        try:
            return StalkerClient(provider).resolve_vod_url(movie.direct_source)
        except StalkerError:
            # Left for the normal "couldn't start playback" handling
            # already in place wherever this is called from — an
            # empty/unplayable result surfaces the same way a dead
            # Xtream URL already does, rather than needing its own,
            # separate error path here.
            return ""

    return "%s/movie/%s/%s/%s.%s" % (
        provider.base_url,
        provider.username,
        provider.password,
        movie.stream_id,
        movie.container_extension or "mp4",
    )


def play_movie(session, provider, movie):
    """Open the player on a movie. VOD — no channel_idx, no
    live/catchup branching; the movie is always available on demand,
    same as an episode."""
    url = build_movie_url(provider, movie)
    provider_name = getattr(provider, "name", "") or ""
    title = getattr(movie, "name", "") or "Movie"
    if provider_name:
        title = "%s   [%s]" % (title, provider_name)
    description = getattr(movie, "plot", "") or ""

    # Stable across separate url resolutions — see E2iPTVPlayer's own
    # resume_key comment for why this matters specifically for
    # Stalker, where the url itself changes every time.
    resume_key = "movie_%s_%s_%s" % (
        provider.source_type, provider_name, movie.stream_id
    )

    vod_kwargs = {}
    if provider.source_type == "STALKER":
        # See E2iPTVPlayer._play's own comment — this url gets
        # re-resolved fresh right before actual use rather than
        # trusting the one built just above, which can go stale
        # sitting through however long the resume prompt takes.
        vod_kwargs = {
            "vod_provider": provider,
            "vod_cmd": movie.direct_source,
        }

    session.open(
        E2iPTVPlayer, url, title,
        is_vod=True, description=description,
        resume_key=resume_key,
        **vod_kwargs
    )


def download_movie(session, provider, movie):
    """Download a VOD movie to the configured recording path.

    Uses the same movie URL resolution as playback. The actual wget
    runs in the background so the browser remains responsive.
    """
    import threading
    import subprocess as _sp

    def _run():
        try:
            url = build_movie_url(provider, movie)

            if not url:
                reactor.callFromThread(
                    session.open,
                    _E2iBox,
                    "Unable to resolve the movie URL.",
                    type=MessageBox.TYPE_ERROR,
                    timeout=5
                )
                return

            recording_path = getattr(
                state, "recording_path", HDD + "/movie/"
            )

            if not recording_path.endswith("/"):
                recording_path += "/"

            if not os.path.isdir(recording_path):
                os.makedirs(recording_path, exist_ok=True)

            title = getattr(movie, "name", "") or "Movie"

            safe = "".join(
                c if c.isalnum() or c in " -_" else "_"
                for c in title
            ).strip()

            if not safe:
                safe = "Movie"

            extension = getattr(
                movie, "container_extension", None
            ) or "mp4"

            filename = "%s%s.%s" % (
                recording_path,
                safe[:150],
                extension
            )

            if os.path.exists(filename):
                reactor.callFromThread(
                    session.open,
                    _E2iBox,
                    "File already exists:\n\n%s" % filename,
                    type=MessageBox.TYPE_INFO,
                    timeout=5
                )
                return

            proc = _sp.Popen([
                "wget",
                "-q",
                "--no-check-certificate",
                "-O",
                filename,
                url,
            ])

            reactor.callFromThread(
                session.open,
                _E2iBox,
                "Movie download started:\n\n%s" % os.path.basename(filename),
                type=MessageBox.TYPE_INFO,
                timeout=5
            )

            exit_code = proc.wait()

            if exit_code == 0:
                reactor.callFromThread(
                    session.open,
                    _E2iBox,
                    "Movie download complete:\n\n%s" %
                    os.path.basename(filename),
                    type=MessageBox.TYPE_INFO,
                    timeout=6
                )
            else:
                try:
                    if os.path.exists(filename):
                        os.remove(filename)
                except Exception:
                    pass

                reactor.callFromThread(
                    session.open,
                    _E2iBox,
                    "Movie download failed.",
                    type=MessageBox.TYPE_ERROR,
                    timeout=5
                )

        except Exception as e:
            print("[E2iPTV] Movie download failed:", e)

            reactor.callFromThread(
                session.open,
                _E2iBox,
                "Movie download failed.",
                type=MessageBox.TYPE_ERROR,
                timeout=5
            )

    threading.Thread(
        target=_run,
        name="E2iPTV-MovieDownload",
        daemon=True
    ).start()


def play_channel(session, provider, channel, cursor_time=None, epg_store=None):
    """Open the player. Uses catchup if event has ended, otherwise live."""
    provider = _bq_real_provider(provider, channel)  # bouquet channel -> its own provider
    if getattr(provider, "is_bouquet", False):
        return None           # bouquet channel whose provider is gone
    now = int(_time.time())
    ev  = None

    if cursor_time and channel.tv_archive and epg_store:
        events = epg_store.get_for(channel)
        for e in sorted(events, key=lambda e: e.start, reverse=True):
            if e.start <= cursor_time:
                ev = e
                break

    if ev and ev.end <= now:
        url   = build_catchup_url(provider, channel, ev)
        title = "%s — %s" % (channel.name, ev.title)
    else:
        url   = build_live_url(provider, channel)
        title = channel.name

    from .state import state as _state
    ch_idx = next((i for i, c in enumerate(_state.channels or []) if c.stream_id == channel.stream_id), None)
    _state.prev_ch_idx = getattr(_state, "last_ch_idx", None)
    _state.last_ch_idx = ch_idx
    session.open(E2iPTVPlayer, url, title, channel_idx=ch_idx)


def play_episode(session, provider, episode, series_title="",
                  on_close_callback=None):
    """Open the player on a series episode. VOD — no channel_idx, no
    live/catchup branching; the episode is always available on demand.

    on_close_callback is optional and defaults to plain session.open()
    (unchanged for every existing caller) — media_browser.py's
    _episode_ok passes one so the episode list can refresh its watched
    checkmarks (see is_watched/mark_watched above) the moment playback
    ends, rather than only on some later, unrelated re-render."""
    url = build_series_url(provider, episode)
    description = getattr(episode, "plot", "") or ""

    if series_title:
        title = "%s — %s" % (series_title, str(episode))
    else:
        title = str(episode)

    provider_name = getattr(provider, "name", "") or ""
    if provider_name:
        title = "%s   [%s]" % (title, provider_name)

    # duration_secs comes from the series detail fetch (get_series_info
    # — see SeriesInfoReader), same source as the plot/description
    # above. Used as the HUD's progress-bar/time-readout fallback until
    # the live seek object's own getLength() becomes available (or if
    # it never does for this stream — some VOD sources don't expose an
    # accurate one, which is what was leaving Series with no progress
    # bar at all rather than just a brief delay before one appeared).
    duration_secs = getattr(episode, "duration_secs", 0) or 0
    duration_pts = duration_secs * 90000 if duration_secs > 0 else None

    # Stable across separate url resolutions — see E2iPTVPlayer's own
    # resume_key comment for why this matters specifically for
    # Stalker. episode.episode_id is already a stable compound
    # string ("<series_id>:<season>:<episode>" — see
    # SeriesInfoReader._read_stalker) for Stalker episodes.
    resume_key = "episode_%s_%s_%s" % (
        provider.source_type, provider_name, episode.episode_id
    )

    vod_kwargs = {}
    if provider.source_type == "STALKER":
        # See E2iPTVPlayer._play's own comment — this url gets
        # re-resolved fresh right before actual use rather than
        # trusting the one built just above.
        vod_kwargs = {
            "vod_provider": provider,
            "vod_cmd": episode.direct_source,
            "vod_kind": "series",
        }

    if on_close_callback is not None:
        session.openWithCallback(
            on_close_callback,
            E2iPTVPlayer, url, title,
            is_vod=True, description=description,
            duration_pts=duration_pts,
            resume_key=resume_key,
            **vod_kwargs
        )
        return

    session.open(
        E2iPTVPlayer, url, title,
        is_vod=True, description=description,
        duration_pts=duration_pts,
        resume_key=resume_key,
        **vod_kwargs
    )


def record_programme(session, provider, channel, ev, recording_path=None):
    """Record live from now to scheduled end."""
    try:
        from RecordTimer import RecordTimerEntry
        from NavigationInstance import instance as nav_instance
    except ImportError:
        return False

    if recording_path is None:
        from .state import state as _state
        recording_path = getattr(_state, 'recording_path', HDD + '/movie/')

    url   = build_live_url(provider, channel)
    ref   = eServiceReference(4097, 0, url)
    ref.setName(channel.name)

    try:
        timer          = RecordTimerEntry(ref, int(_time.time()), ev.end,
                                          channel.name, ev.title, "")
        timer.dirname  = recording_path
        timer.justplay = False
        nav_instance.RecordTimer.record(timer)
        return True
    except Exception:
        return False


_ACTIVE_RECORDINGS_PATH = DATA + "/cache/active_recordings.json"
_REC_LOG_PATH = LOG("e2iptv_recording.log")


def _rec_log(msg):
    """Diagnostic tracing for the recording lifecycle — appends a
    timestamped line to /tmp/e2iptv_recording.log. Added to help track
    down a recurring native "media in use" MessageBox that is NOT one
    of ours (grepping the whole plugin for that text finds nothing) —
    the working theory is it's triggered either by Enigma2 finding one
    of our wget-written .ts files still open for writing, or by a
    stray native RecordTimer entry left behind by a timer added via
    the box's own Timers menu. This logs our own recording state
    changes so they can be lined up against when the native message
    next appears — check this file straight after it happens."""
    try:
        import time as _t3
        line = "%s  %s\n" % (_t3.strftime("%Y-%m-%d %H:%M:%S"), msg)
        with open(_REC_LOG_PATH, "a") as f:
            f.write(line)
    except Exception:
        pass


def _rec_log_native_state(tag):
    """Snapshot what OpenATV's own native RecordTimer currently thinks
    is recording/scheduled, tagged with `tag` so it lines up against
    our own active_recordings log lines at the same moment. Read-only —
    never touches the native timer list. This is the key piece for
    telling apart "our wget recording is doing this" from "a native
    timer the user added/removed is doing this"."""
    try:
        from NavigationInstance import instance as _nav
        rt = getattr(_nav, "RecordTimer", None)
        if rt is None:
            _rec_log("[%s] native RecordTimer: not available" % tag)
            return
        is_recording = False
        try:
            is_recording = bool(rt.isRecording())
        except Exception:
            pass
        entries = []
        for t in list(getattr(rt, "timer_list", []) or []) + list(getattr(rt, "processed_timers", []) or []):
            try:
                entries.append("name=%r state=%r justplay=%r file=%r" % (
                    getattr(t, "name", "?"),
                    getattr(t, "state", "?"),
                    getattr(t, "justplay", "?"),
                    getattr(t, "Filename", "?"),
                ))
            except Exception:
                pass
        _rec_log("[%s] native RecordTimer isRecording=%r entries=%d %s" %
                  (tag, is_recording, len(entries), entries))
    except Exception as e:
        _rec_log("[%s] native RecordTimer check failed: %r" % (tag, e))



# Sidecar files a recording can have, named "<recording>.ts.<ext>".
_SIDECAR_EXTS = (".json", ".meta", ".cuts", ".ap", ".sc")


def remove_orphan_sidecars(folder, min_age=600):
    """Delete sidecar files (.json, .meta, .cuts, .ap, .sc) in `folder`
    whose recording is no longer there. A scheduled recording writes its
    .json/.meta in advance, named by the planned start time; the actual
    recording is named by the second it really started, so deleting it
    never matched those, and recordings that never ran left them behind
    too. Sidecars of recordings that are scheduled or running now, and
    anything changed in the last `min_age` seconds, are left alone.
    Only this one folder is tidied, not its subfolders. Returns the
    number of files removed."""
    import os as _os
    import time as _t
    from .state import state as _st
    now = _t.time()
    keep = set()
    for rec in (getattr(_st, "active_recordings", {}) or {}).values():
        # Only recordings genuinely still to come or still running —
        # a stale entry left in the list must not protect leftovers.
        if not (isinstance(rec, dict) and rec.get("filename")):
            continue
        if rec.get("status") not in ("scheduled", "active"):
            continue
        end = rec.get("event_end") or rec.get("end") or 0
        try:
            if end and float(end) < now - 3600:
                continue
        except (TypeError, ValueError):
            pass
        keep.add(_os.path.abspath(rec["filename"]))
    removed = 0
    try:
        names = _os.listdir(folder)
    except OSError:
        return 0
    for n in names:
        for ext in _SIDECAR_EXTS:
            if not n.endswith(".ts" + ext):
                continue
            side = _os.path.join(folder, n)
            media = side[:-len(ext)]
            if _os.path.exists(media) or _os.path.abspath(media) in keep:
                break
            try:
                if now - _os.path.getmtime(side) < min_age:
                    break
                _os.remove(side)
                removed += 1
            except OSError:
                pass
            break
    return removed

def _save_active_recordings():
    """Persist active_recordings to disk (minus the unpicklable "proc"
    subprocess handle) every time it changes, so a GUI restart doesn't
    silently lose track of a recording that's still in progress — that
    was the actual cause of a recording becoming un-stoppable and its
    indicator just vanishing after a restart. See
    reconcile_active_recordings(), called once at plugin startup."""
    from .state import state as _state
    import json, os
    try:
        os.makedirs(os.path.dirname(_ACTIVE_RECORDINGS_PATH), exist_ok=True)
        data = {}
        for key, rec in _state.active_recordings.items():
            data[key] = {k: v for k, v in rec.items() if k != "proc"}
        tmp = _ACTIVE_RECORDINGS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _ACTIVE_RECORDINGS_PATH)
        _rec_log("SAVE active_recordings: %d entries: %s" %
                  (len(data), {k: v.get("status") for k, v in data.items()}))
    except Exception as e:
        _rec_log("SAVE active_recordings FAILED: %r" % (e,))


def reconcile_active_recordings():
    """Called once at plugin startup (see state.py's AppState.__init__).
    Restores any recording that was still supposed to be in progress
    when the plugin last stopped running, so the recording indicator
    and Stop Recording option reflect reality instead of just being
    silently empty. A restored entry has no live "proc" handle (that
    died with the old process), so stopping it can delete/keep the
    file but can't forcibly kill an already-orphaned wget — current
    scope only ever records up to the end of what's airing now, so in
    practice this just re-arms the UI for the remainder of that window.
    Anything whose scheduled end has already passed is dropped — it
    should have finished on its own."""
    from .state import state as _state
    import json, os, time as _t
    _rec_log("RECONCILE starting")
    try:
        if os.path.exists(_ACTIVE_RECORDINGS_PATH):
            with open(_ACTIVE_RECORDINGS_PATH, "r") as f:
                data = json.load(f)
            _rec_log("RECONCILE found %d entries on disk: %s" %
                      (len(data), {k: v.get("status") for k, v in data.items() if isinstance(v, dict)}))
            now = int(_t.time())
            kept = {}
            for key, rec in data.items():
                if not isinstance(rec, dict):
                    _rec_log("RECONCILE drop %r — not a dict" % (key,))
                    continue
                if max(rec.get("event_end", 0) or 0, rec.get("end", 0) or 0) > now:
                    kept[key] = rec
                    fname = rec.get("filename")
                    exists = os.path.exists(fname) if fname else None
                    if rec.get("status") == "active" and fname and not exists:
                        _rec_log("RECONCILE keep %r status=%r filename=%r "
                                  "— WARNING file does not exist, likely ORPHAN "
                                  "(wget never started or died before writing)" %
                                  (key, rec.get("status"), fname))
                    else:
                        _rec_log("RECONCILE keep %r status=%r filename=%r exists=%r" %
                                  (key, rec.get("status"), fname, exists))
                else:
                    _rec_log("RECONCILE drop %r status=%r — event_end=%r <= now=%r (expired)" %
                              (key, rec.get("status"), rec.get("event_end", 0), now))
            _state.active_recordings = kept
        else:
            _rec_log("RECONCILE: no active_recordings.json on disk")
    except Exception as e:
        _rec_log("RECONCILE FAILED: %r" % (e,))
    _rec_log_native_state("reconcile")
    _save_active_recordings()
    # Every kept entry lost its recording thread with the old process.
    # Left alone it just sat there: shown green, never recorded, and the
    # scheduler skipped it as "already being recorded" (for repeat rules
    # it even marked that night's showing done). Start each one again.
    for key in list(_state.active_recordings.keys()):
        try:
            _relaunch_recording(key)
        except Exception as e:
            _rec_log("RECONCILE relaunch %r FAILED: %r" % (key, e))


def _relaunch_recording(rec_key):
    """Pick up a recording remembered from before a GUI restart: wait for
    its start if it's still to come, then record (appending to its file)
    until its end. Catch-up downloads can't be resumed part-way, so those
    are dropped (whatever was saved is kept)."""
    import threading
    import time as _t
    from .state import state as _state

    rec = _state.active_recordings.get(rec_key)
    url = rec.get("url") if rec else None
    filename = rec.get("filename") if rec else None
    if not rec or not url or not filename:
        _state.active_recordings.pop(rec_key, None)
        _save_active_recordings()
        return
    label = "%s - %s" % (rec.get("channel", ""), rec.get("title", ""))
    if "/timeshift/" in url:
        _recordings_log("DROPPED   %s  - catch-up download interrupted by a restart "
                        "(the part saved so far is kept)" % label)
        _state.active_recordings.pop(rec_key, None)
        _save_active_recordings()
        return

    begin = int(rec.get("begin") or rec.get("event_start") or _t.time())
    end = int(rec.get("end") or rec.get("event_end") or 0)
    rec.pop("proc", None)

    def _run():
        try:
            wait = begin - _t.time()
            if wait > 0:
                _t.sleep(wait)
            if rec_key not in _state.active_recordings:
                return                              # stopped meanwhile
            _state.active_recordings[rec_key]["status"] = "active"
            _save_active_recordings()
            if wait <= 0:
                _recordings_log("RESUMED   %s  - picked up again after a restart"
                                % label)
            _wget_recording(
                rec_key, filename, url,
                lambda: max(end, _state.active_recordings.get(rec_key, {}).get("event_end", end)),
                label, True, append=True)
        except Exception as e:
            _rec_log("RELAUNCH rec_key=%r EXCEPTION: %r" % (rec_key, e))
        finally:
            try:
                if os.path.exists(filename):
                    _update_native_meta_size(filename)
                    rule_id = rec.get("rule_id")
                    mgr = getattr(_state, "recording_manager", None)
                    if rule_id is not None and mgr is not None:
                        rule = mgr.rule_store.get(rule_id)
                        if rule is not None and getattr(rule, "rule_type", "") == "once":
                            rule.last_run = rule.event_start
                            mgr.save()
            except Exception:
                pass
            _state.active_recordings.pop(rec_key, None)
            _save_active_recordings()

    _rec_log("RELAUNCH rec_key=%r begin=%r end=%r" % (rec_key, begin, end))
    threading.Thread(target=_run, daemon=True).start()


def _update_native_meta_size(filename):
    """Correct the file size line of an existing .meta after recording."""
    try:
        meta = filename + ".meta"
        with open(meta) as f:
            lines = f.read().split("\n")
        if len(lines) > 6:
            lines[6] = str(os.path.getsize(filename))
            with open(meta, "w") as f:
                f.write("\n".join(lines))
    except Exception:
        pass


def _write_native_meta(filename, provider, channel, ev, name, description, begin, filesize=0):
    """Write a native OpenATV .meta sidecar (service ref / name /
    description / recording time / tags / length / filesize, in the
    exact layout OpenATV itself uses — verified directly against a
    real recording's own .meta file on this box) alongside the .json
    sidecar above. This is what makes a recording show up properly —
    title, description, date — when browsed from the box's own native
    Movie Selection screen, instead of just a raw filename.

    There's no real DVB service for an IPTV stream, so the service
    reference is a harmless placeholder (the same one OpenATV itself
    falls back to for an unrecognised .ts, confirmed by inspecting one
    it auto-generated) with the channel name appended the same way a
    real recording's service reference carries its channel name.

    Length (in PTS units) is left at 0 — OpenATV recalculates that
    itself the first time the recording is browsed/played. filesize is
    0 at creation time and gets corrected once the recording actually
    finishes (see the callers)."""
    try:
        chan_name   = channel.name if channel else ""
        service_ref = "1:0:0:0:0:0:0:0:0:0::%s" % chan_name
        title       = (ev.title if ev else name) or name or ""
        desc        = ((ev.description if ev else description) or "").replace("\n", " ").strip()
        tag         = chan_name.replace(" ", "_") if chan_name else ""
        lines = [
            service_ref,
            title,
            desc,
            str(int(begin)),
            tag,
            "0",
            str(int(filesize)),
            "",
            "188",
            "0",
        ]
        with open(filename + ".meta", "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass



# -----------------------------------------------------------------------------
# Live recordings: reconnect when the provider drops the stream
#
# A recording is one wget download of the channel's stream. Providers
# sometimes cut a long-running connection (a server-side limit or a
# network blip); wget then simply exits and, before this, the recording
# stopped there - silently - with the rest of the programme missing.
# Now, until the recording's end time, a dropped stream is reopened after
# a short pause and appended to the same .ts file (MPEG-TS joins cleanly).
# Catch-up downloads have a natural end, so they are never reconnected.
#
# Every start, drop, reconnect and finish goes to an always-on log,
# e2iptv_recordings.log ("Recordings" in Settings -> Status & Logs).
# Channel and title only - never the stream address, which holds the login.
# -----------------------------------------------------------------------------

_WGET_EXIT = {
    0: "stream ended by the server", 1: "error", 3: "file write error",
    4: "network failure", 5: "SSL error", 6: "login refused",
    7: "protocol error", 8: "server error",
}


def _recordings_log(msg):
    try:
        with open(LOG("e2iptv_recordings.log"), "a") as f:
            f.write("%s  %s\n" % (_time.strftime("%d %b %H:%M:%S"), msg))
    except Exception:
        pass


def _mmss(secs):
    secs = int(max(0, secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def _wget_recording(rec_key, filename, url, end_fn, label, reconnect, append=False):
    """Run the wget download for one recording until end_fn() is reached,
    the recording is stopped (its active_recordings entry removed), or -
    when reconnect is False - wget finishes on its own."""
    import subprocess as _sp
    import time as _t
    from .state import state as _state

    import threading as _th
    from .recording.ts_fixer import StreamFixer

    started = _t.time()
    drops = 0
    fails_in_a_row = 0
    first = True
    # One corrector for the whole recording, so a track list that changes
    # at a reconnect is caught too (see recording/ts_fixer.py).
    fixer = [StreamFixer(timeline=True)]
    if append:
        fixer[0].prime_from_file(filename)

    def _pump(proc, out):
        """Copy wget's output into the file through the corrector."""
        fd = proc.stdout.fileno()
        try:
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                if fixer[0] is not None:
                    try:
                        chunk = fixer[0].feed(chunk)
                    except Exception as e:
                        # Never lose recording data over the corrector:
                        # step aside and write the stream unchanged.
                        _recordings_log("NOTE      %s  - sound corrector switched off: %s"
                                        % (label, e))
                        chunk = fixer[0].carry + chunk
                        fixer[0] = None
                out.write(chunk)
            if fixer[0] is not None:
                out.write(fixer[0].flush())
        except Exception as e:
            _rec_log("REC rec_key=%r pump error: %r" % (rec_key, e))
        finally:
            try:
                out.close()
            except Exception:
                pass
            try:
                proc.stdout.close()
            except Exception:
                pass

    _recordings_log("START     %s  (until %s)" % (
        label, _t.strftime("%H:%M", _t.localtime(end_fn()))))
    while True:
        try:
            # append=True (a recording picked up again after a GUI
            # restart) continues the same file instead of starting it over.
            out = open(filename, "ab" if (append or not first) else "wb")
        except Exception as e:
            _recordings_log("FAILED    %s  - cannot write the file: %s" % (label, e))
            raise
        try:
            proc = _sp.Popen(["wget", "-q", "--no-check-certificate",
                              "-O", "-", url], stdout=_sp.PIPE)
        except Exception:
            out.close()
            raise
        pump = _th.Thread(target=_pump, args=(proc, out), daemon=True)
        pump.start()
        first = False
        conn_start = _t.time()
        if rec_key in _state.active_recordings:
            _state.active_recordings[rec_key]["proc"] = proc
        _rec_log("REC rec_key=%r wget started pid=%r -> %r" % (rec_key, proc.pid, filename))

        loops = 0
        while _t.time() < end_fn() and proc.poll() is None:
            _t.sleep(5)
            loops += 1
            if loops % 6 == 0:  # heartbeat roughly every 30s
                try:
                    size = os.path.getsize(filename)
                except Exception:
                    size = -1
                _rec_log("REC rec_key=%r heartbeat pid=%r size=%r" % (rec_key, proc.pid, size))

        if proc.poll() is None:
            # End time reached - normal finish.
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            pump.join(timeout=10)
            break

        pump.join(timeout=10)      # let the last data reach the file
        code = proc.poll()
        if rec_key not in _state.active_recordings:
            break                                   # stopped by the user
        if not reconnect:
            break                                   # catch-up: done
        if _t.time() >= end_fn() - 5:
            break                                   # dropped right at the end

        lasted = _t.time() - conn_start
        fails_in_a_row = 0 if lasted > 60 else fails_in_a_row + 1
        pause = min(30, 3 + 5 * fails_in_a_row)
        drops += 1
        if fails_in_a_row <= 3 or fails_in_a_row % 10 == 0:
            _recordings_log("DROPPED   %s  after %s into the recording (%s) - "
                            "reconnecting in %ds%s" % (
                                label, _mmss(_t.time() - started),
                                _WGET_EXIT.get(code, "exit code %s" % code), pause,
                                ("  [attempt %d failed straight away]" % fails_in_a_row)
                                if fails_in_a_row else ""))
        _t.sleep(pause)
        if rec_key not in _state.active_recordings or _t.time() >= end_fn():
            break

    try:
        size = os.path.getsize(filename)
    except Exception:
        size = 0
    fixes = fixer[0].changes if fixer[0] is not None else 0
    joins = fixer[0].joins if fixer[0] is not None else 0
    _recordings_log("FINISHED  %s  - recorded %s, %.0f MB%s%s%s" % (
        label, _mmss(_t.time() - started), size / 1048576.0,
        (", stream dropped %d time%s and was reconnected" % (drops, "" if drops == 1 else "s"))
        if drops else ", no drops",
        (", sound format changed %d time%s (corrected)" % (fixes, "" if fixes == 1 else "s"))
        if fixes else "",
        (", %d stream restart%s joined smoothly" % (joins, "" if joins == 1 else "s"))
        if joins else ""))


def _create_timer(session,
                  provider,
                  channel,
                  ev,
                  url,
                  begin,
                  end,
                  name,
                  description,
                  recording_path,
                  reconnect=True):
    """Record any stream via wget in a background thread. reconnect=False
    for catch-up downloads (see _wget_recording)."""

    import threading
    import subprocess as _sp
    import time as _t

    from .state import state as _state

    if not recording_path.endswith("/"):
        recording_path += "/"

    label = description if description else name

    safe = "".join(
        c if c.isalnum() or c in " -_" else "_"
        for c in label
    )

    if "/timeshift/" in url:

        stamp = _t.strftime(
            "%Y%m%d_%H%M%S",
            _t.localtime(ev.start)
        )

        filename = "%s%s_CU_%s.ts" % (
            recording_path,
            safe[:40],
            stamp
        )

    else:

        filename = "%s%s_%s.ts" % (
            recording_path,
            safe[:40],
            _t.strftime("%Y%m%d_%H%M%S")
        )

    # Metadata sidecar — the .ts filename only ever encoded the
    # programme title and a timestamp, never the channel, so a
    # recordings browser had no way to show a channel icon/name or a
    # real end time for anything already on disk. Write this once, at
    # creation time, so the Recordings screen can read it back later.
    try:
        import json as _json
        _meta = {
            "provider_id":   provider.provider_id,
            "provider_name": getattr(provider, "name", ""),
            "stream_id":     channel.stream_id,
            "channel_name":  channel.name,
            "title":         ev.title if ev else name,
            "start":         begin,
            "end":           end,
        }
        with open(filename + ".json", "w") as _mf:
            _json.dump(_meta, _mf)
    except Exception:
        pass

    _write_native_meta(filename, provider, channel, ev, name, description, begin)

    delay = max(0, begin - int(_t.time()))

    rec_key = "%s_%s" % (url, begin)

    _rec_log("CREATE_TIMER rec_key=%r filename=%r begin=%r end=%r delay=%r url=%r" %
              (rec_key, filename, begin, end, delay, url))

    _state.active_recordings[rec_key] = {
        "status": "scheduled" if delay > 0 else "active",
        "url": url,
        "provider_id": provider.provider_id,
        "provider_key": getattr(provider, "provider_key", ""),
        "stream_id": channel.stream_id,
        "event_start": ev.start,
        "event_end": ev.end,
        "channel": channel.name,
        "title": ev.title,
        "filename": filename,
        "begin": begin,          # padded window, so a recording can be
        "end": end,              # picked up again after a GUI restart
    }
    _save_active_recordings()

    def _run():

        _rec_log("CREATE_TIMER._run start rec_key=%r delay=%r" % (rec_key, delay))

        if delay > 0:
            _t.sleep(delay)

        if rec_key not in _state.active_recordings:
            _rec_log("CREATE_TIMER._run rec_key=%r vanished from active_recordings "
                      "before wget could start (stopped/cleared while waiting) — aborting" %
                      (rec_key,))
            return

        _state.active_recordings[rec_key]["status"] = "active"
        _save_active_recordings()
        _rec_log_native_state("create_timer-launch")

        try:

            _wget_recording(rec_key, filename, url, lambda: end,
                            "%s - %s" % (channel.name, ev.title), reconnect)

        except Exception as e:
            _rec_log("CREATE_TIMER._run rec_key=%r EXCEPTION: %r" % (rec_key, e))
            raise

        finally:

            try:
                if os.path.exists(filename):
                    _write_native_meta(filename, provider, channel, ev, name,
                                        description, begin,
                                        filesize=os.path.getsize(filename))
                    _rec_log("CREATE_TIMER._run rec_key=%r finished, file exists "
                              "size=%r" % (rec_key, os.path.getsize(filename)))
                else:
                    _rec_log("CREATE_TIMER._run rec_key=%r finished, but %r "
                              "does NOT exist on disk — recording never wrote "
                              "any data" % (rec_key, filename))
            except Exception:
                pass

            _state.active_recordings.pop(rec_key, None)
            _save_active_recordings()
            _rec_log_native_state("create_timer-cleanup")

    threading.Thread(
        target=_run,
        daemon=True
    ).start()

    return rec_key


def _create_timer_direct(session, url, begin, end, name, description,
                         recording_path, provider=None, channel=None, ev=None,
                         trigger=None, rule_id=None):
    """Schedule a future recording via wget — registers in active_recordings for indicators."""
    import threading, time as _t
    from .state import state as _state
    if not recording_path.endswith("/"):
        recording_path += "/"
    safe     = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
    filename = "%s%s_%s.ts" % (recording_path, safe[:40],
                                _t.strftime("%Y%m%d_%H%M%S", _t.localtime(begin)))

    # Metadata sidecar — see the matching comment in _create_timer()
    # for why this exists: the .ts filename alone never carries the
    # channel, so the Recordings screen can't show an icon/name or a
    # real end time without this.
    try:
        import json as _json
        _meta = {
            "provider_id":   provider.provider_id if provider else None,
            "provider_name": getattr(provider, "name", "") if provider else "",
            "stream_id":     channel.stream_id if channel else None,
            "channel_name":  channel.name if channel else None,
            "title":         ev.title if ev else name,
            "start":         begin,
            "end":           end,
        }
        with open(filename + ".json", "w") as _mf:
            _json.dump(_meta, _mf)
    except Exception:
        pass

    _write_native_meta(filename, provider, channel, ev, name, description, begin)

    delay    = max(0, begin - int(_t.time()))
    rec_key  = "%s_%s" % (url, begin)

    _rec_log("CREATE_TIMER_DIRECT rec_key=%r filename=%r begin=%r end=%r "
              "delay=%r url=%r" % (rec_key, filename, begin, end, delay, url))

    _state.active_recordings[rec_key] = {
        "status":      "scheduled",
        "url":         url,
        "provider_id": provider.provider_id if provider else None,
        "provider_key": getattr(provider, "provider_key", "") if provider else "",
        "stream_id":   channel.stream_id    if channel  else None,
        "event_start": ev.start             if ev       else begin,
        "event_end":   ev.end               if ev       else end,
        "channel":     channel.name         if channel  else name,
        "title":       ev.title             if ev       else name,
        "filename":    filename,
        "begin":       begin,    # padded window (see reconcile)
        "end":         end,
        "rule_id":     rule_id,
        "trigger":     trigger,  # "now" for Record-from-Now — see the
        # channel-scoped (not event_start-scoped) lookup in
        # get_ctx_items(), which is what actually makes "Extend +30 min"
        # findable again after the menu closes and reopens: a
        # Record-from-Now event's start is the exact moment it was
        # pressed, which will never exactly match whatever EPG-aligned
        # tile the user is looking at when they reopen the menu.
    }
    _save_active_recordings()
    def _run():
        import subprocess as _sp, time as _t2
        _rec_log("CREATE_TIMER_DIRECT._run start rec_key=%r delay=%r" % (rec_key, delay))
        if delay > 0:
            _t2.sleep(delay)
        if rec_key not in _state.active_recordings:
            _rec_log("CREATE_TIMER_DIRECT._run rec_key=%r vanished from "
                      "active_recordings before wget could start (stopped/cleared "
                      "while waiting) — aborting" % (rec_key,))
            return
        if rec_key in _state.active_recordings:
            _state.active_recordings[rec_key]["status"] = "active"
            _save_active_recordings()
            _rec_log_native_state("create_timer_direct-launch")
        try:
            # Reads event_end from the shared active_recordings dict each
            # time round, so "Extend +30 min" on a running recording works.
            _wget_recording(
                rec_key, filename, url,
                lambda: max(end, _state.active_recordings.get(rec_key, {}).get("event_end", end)),
                "%s - %s" % (channel.name if channel else name,
                             ev.title if ev else name),
                True)
        except Exception as e:
            try:
                open(LOG("e2iptv_rec.log"), "a").write("SCHED ERR:" + str(e) + "\n")
            except Exception:
                pass
            _rec_log("CREATE_TIMER_DIRECT._run rec_key=%r EXCEPTION: %r" % (rec_key, e))
        finally:
            try:
                if os.path.exists(filename):
                    _write_native_meta(filename, provider, channel, ev, name,
                                        description, begin,
                                        filesize=os.path.getsize(filename))
                    _rec_log("CREATE_TIMER_DIRECT._run rec_key=%r finished, "
                              "file exists size=%r" % (rec_key, os.path.getsize(filename)))
                    # Mark the underlying rule as done — ONLY here, once
                    # the recording has genuinely produced output, never
                    # at spawn time. The scheduler's own fallback (see
                    # RecordingManager.add_rule's comment) deliberately
                    # relies on rule.last_run staying unset if this
                    # thread dies before ever actually recording
                    # anything (e.g. a GUI restart before showtime) —
                    # marking it done unconditionally here would defeat
                    # that recovery path entirely. What this fixes is
                    # the opposite, previously-unhandled case: once a
                    # recording HAS completed and this thread's own
                    # active_recordings entry is cleaned up below, the
                    # scheduler's periodic _already_covered check (which
                    # only ever looks at CURRENT active_recordings
                    # entries) has nothing left to match — so if its
                    # next 60s tick happens to land after that cleanup
                    # (most likely after a box restart hours later, per
                    # the actual report this fixes), it would think this
                    # rule had never been handled at all and start a
                    # second, duplicate recording for an event long
                    # since over — which is also why the tile was still
                    # showing green/scheduled the next day even though
                    # the original recording succeeded.
                    if rule_id is not None:
                        try:
                            _mgr = getattr(_state, 'recording_manager', None)
                            if _mgr is not None:
                                _rule = _mgr.rule_store.get(rule_id)
                                if _rule is not None:
                                    _rule.last_run = _rule.event_start
                                    _mgr.save()
                        except Exception:
                            pass
                else:
                    _rec_log("CREATE_TIMER_DIRECT._run rec_key=%r finished, but "
                              "%r does NOT exist on disk — recording never wrote "
                              "any data" % (rec_key, filename))
            except Exception:
                pass
            _state.active_recordings.pop(rec_key, None)
            _save_active_recordings()
            _rec_log_native_state("create_timer_direct-cleanup")
    threading.Thread(target=_run, daemon=True).start()
    return rec_key


def stop_recording(rec_key):
    """Stop an active recording by key."""
    from .state import state as _state
    _rec_log("STOP_RECORDING called rec_key=%r" % (rec_key,))
    rec = _state.active_recordings.get(rec_key)
    if rec:
        proc = rec.get("proc")
        if proc and proc.poll() is None:
            _rec_log("STOP_RECORDING rec_key=%r terminating live proc pid=%r" %
                      (rec_key, proc.pid))
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass
        elif proc is None:
            _rec_log("STOP_RECORDING rec_key=%r has no live proc handle — "
                      "either restored from disk after a restart (can't kill "
                      "an orphaned wget from here) or never started" % (rec_key,))
        else:
            _rec_log("STOP_RECORDING rec_key=%r proc already exited (poll=%r)" %
                      (rec_key, proc.poll()))
        _state.active_recordings.pop(rec_key, None)
        _save_active_recordings()
        return True
    _rec_log("STOP_RECORDING rec_key=%r not found in active_recordings" % (rec_key,))
    return False


def get_active_recording(provider, channel, ev):
    """Find the active-recording key for this channel, if any.

    Matches on provider + channel only, deliberately NOT on the event's
    start/end times. The "Stop Recording" menu option is shown based on
    that same loose match (see _get_ctx_items in both screen1's
    ChannelBrowser and screen3.py) — if this function required an exact
    event-time match too, a recording could silently fail to stop whenever
    the EPG data for that slot shifted even slightly between starting the
    recording and coming back to stop it (e.g. after a background EPG
    refresh), leaving the underlying wget process running and its output
    file open indefinitely. `ev` is accepted for backwards compatibility
    with existing call sites but is no longer used for matching.
    """
    if not provider or not channel:
        return None

    from .state import state as _state

    for rec_key, rec in _state.active_recordings.items():

        if (
            rec.get("provider_id") == provider.provider_id
            and rec.get("stream_id") == channel.stream_id
        ):
            return rec_key

    return None


def get_ctx_items(provider, channel, ev, now=None):
    """The programme's options (below), plus "Guide from..." for the
    channel - where its guide comes from (epg_map.py)."""
    items = _get_ctx_items_base(provider, channel, ev, now)
    real = _bq_real_provider(provider, channel)
    if items and channel is not None and not getattr(real, "is_bouquet", False):
        items = list(items) + [("Guide from...", True, "guide_from")]
    return items


def _get_ctx_items_base(provider, channel, ev, now=None):
    """
    Build the Watch/Record/Download context-menu items for a given
    provider+channel+event.

    THE single place that decides what options are offered for a
    programme. Screen 1's ChannelBrowser, the EPG grid (screen3.py),
    and Live TV's instant-record key all call this — so they can never
    drift out of sync with each other the way the grid and Screen 1
    already did once (get_active_recording's matching bug had to be
    fixed in two separate copies of this logic).

    "Record" is used for anything that captures a live broadcast as
    EPG says it airs. "Manual Recording" is the one deliberately
    simple escape hatch for everything EPG can't be trusted for —
    synthetic "No EPG Available" tiles with no listing at all, and
    real EPG tiles whose listed time doesn't match what's actually
    being broadcast (sports pre-show vs. actual kick-off being the
    main case) — opened as an in-panel date/start/end entry (see
    ChannelBrowser._enter_manual_entry and its screen3.py twin) rather
    than a separate popup screen, specifically to avoid the
    live-video-bleeding-through-a-small-box inconsistency a standalone
    screen had. Available on every tile, current or future, real EPG
    or synthetic — its own start field defaults to "now" but is just
    as adjustable as its end field, so this single option covers what
    used to be two separate ones ("Record from Now" / "Set Start/End
    Time") without the menu clutter of offering both. "Download" is
    used for historical/catchup content — that programme already
    exists in full on the provider's server, nothing is being
    captured as it happens.

    The "Stop Recording" shortcut is scoped to the SPECIFIC event passed
    in (matched on event.start), not just "this channel has some active
    recording" — rec_key's underlying storage already supports more than
    one concurrent recording per channel (e.g. a live capture and a
    historical download use different URLs/keys), so selecting a
    different, unrelated event on an already-recording channel must
    still offer that event's own normal Watch/Record/Download menu
    rather than a confusing "stop the OTHER recording" prompt. A
    manually-scheduled recording gets its own, separate channel-scoped
    check for the same reason a manually-entered start won't usually
    exactly match ev.start for whatever tile the menu is reopened on.
    """
    provider = _bq_real_provider(provider, channel)  # bouquet channel -> its own provider
    if getattr(provider, "is_bouquet", False):
        return []           # bouquet channel whose provider is gone
    _nav_debug(
        "get_ctx_items DUMP: channel=%r(stream_id=%r) ev.start=%r ev.end=%r "
        "ev.title=%r | active_recordings for this stream: %r | "
        "enabled_rules for this stream: %r" % (
            getattr(channel, 'name', None), getattr(channel, 'stream_id', None),
            getattr(ev, 'start', None), getattr(ev, 'end', None),
            getattr(ev, 'title', None),
            [
                (k, {
                    'status': v.get('status'), 'trigger': v.get('trigger'),
                    'event_start': v.get('event_start'), 'event_end': v.get('event_end'),
                    'title': v.get('title'),
                })
                for k, v in state.active_recordings.items()
                if str(v.get('stream_id')) == str(getattr(channel, 'stream_id', None))
            ],
            [
                {
                    'trigger': getattr(r, 'trigger', None),
                    'event_start': getattr(r, 'event_start', None),
                    'event_end': getattr(r, 'event_end', None),
                    'last_run': getattr(r, 'last_run', None),
                    'title': getattr(r, 'event_title', None),
                }
                for r in getattr(state, 'recording_manager', None).enabled_rules()
                if str(getattr(r, 'stream_id', None)) == str(getattr(channel, 'stream_id', None))
            ] if getattr(state, 'recording_manager', None) else []
        )
    )
    if now is None:
        now = int(_time.time())

    archive = bool(getattr(channel, 'tv_archive', False))

    manual_rec_key = None
    manual_rec_status = None
    if provider and channel:
        for key, rec in state.active_recordings.items():
            try:
                if not (rec.get('provider_id') == provider.provider_id
                        and str(rec.get('stream_id')) == str(channel.stream_id)
                        and rec.get('trigger') == 'manual'
                        and rec.get('status') in ('scheduled', 'active')):
                    continue
                # Channel alone isn't enough — a manual recording on
                # this channel could be for an entirely different time
                # window than whatever tile is actually being viewed
                # (e.g. recording now, but looking at tomorrow's
                # schedule on the same channel). Only match if the
                # viewed tile's own time actually overlaps the
                # recording's, or — when there's no tile time at all —
                # if the recording is the one actually active right now.
                rec_start = rec.get('event_start', 0)
                rec_end   = rec.get('event_end', 0)
                if ev is not None:
                    if ev.start < rec_end and ev.end > rec_start:
                        manual_rec_key = key
                        manual_rec_status = rec.get('status')
                        break
                elif rec_start <= now < rec_end:
                    manual_rec_key = key
                    manual_rec_status = rec.get('status')
                    break
            except Exception:
                pass

    def _watch_items():
        # While a programme is recording you can still watch it: live,
        # or from its start when the channel has catch-up.
        if ev is not None and ev.start <= now < ev.end:
            items = []
            if archive:
                items.append(('Watch From Start', True, 'watch_start'))
            items.append(('Watch To End', True, 'watch_end'))
            return items
        return [('Watch', True, 'play')]

    if manual_rec_key:
        if manual_rec_status == 'active':
            return _watch_items() + [
                ('Stop Recording — Delete', True, 'stop_rec_delete'),
                ('Stop Recording — Keep',   True, 'stop_rec_keep'),
            ]
        # Not yet started ("scheduled") — nothing is actually being
        # captured yet, so Stop makes no sense here (previously offered
        # anyway, and its Delete/Keep both silently did nothing useful:
        # they only ever cleared the tracking entry, not the underlying
        # rule, which the background scheduler would then just recreate
        # shortly after — matching the "chose delete, stayed green"
        # report this replaced). Remove Recording is the correct,
        # equivalent option here, same as any other future recording.
        return [('Remove Recording', True, 'remove_manual')]

    rec_key = None
    if provider and channel and ev is not None:
        for key, rec in state.active_recordings.items():
            try:
                if (rec.get('provider_id') == provider.provider_id
                        and str(rec.get('stream_id')) == str(channel.stream_id)
                        and rec.get('event_start') == ev.start):
                    rec_key = key
                    break
            except Exception:
                pass

    if rec_key and state.active_recordings.get(rec_key, {}).get('status') == 'active':
        # Only an actually-active recording gets Stop Recording — this
        # never checked status at all before, so a merely-scheduled
        # (not yet started) EPG-based recording also got Stop Recording
        # here, whose Delete/Keep both silently did nothing useful
        # (matching the identical bug already found and fixed for
        # Manual Recording's own equivalent check above — this was the
        # same thing, just in the older, pre-existing code path normal
        # EPG-triggered recordings go through). A still-scheduled one
        # deliberately falls through instead of returning here, to the
        # normal is_future handling below, which already has its own
        # correct scheduled-recording check via manager.enabled_rules()
        # and offers Remove Recording there instead.
        return _watch_items() + [
            ('Stop Recording — Delete', True, 'stop_rec_delete'),
            ('Stop Recording — Keep',   True, 'stop_rec_keep'),
        ]

    manual_items = [('Manual Recording', True, 'rec_manual')]

    if ev is None:
        return [('Watch', True, 'play')] + manual_items

    is_current = ev.start <= now < ev.end
    is_future  = ev.start > now

    # Programme covered by a repeating recording (Manual Recording ->
    # Repeat): offer to stop the whole repeat.
    repeat_item = []
    if (is_current or is_future) and provider and channel:
        try:
            from .recording.scheduler import repeat_rule_for
            if repeat_rule_for(getattr(state, 'recording_manager', None),
                               provider, channel, ev.start, ev.end):
                repeat_item = [('Remove Repeat Recording', True, 'remove_repeat')]
        except Exception:
            pass
    if repeat_item and is_future:
        return repeat_item

    if is_current and archive:
        return [
            ('Watch From Start',  True, 'watch_start'),
            ('Watch To End',      True, 'watch_end'),
            ('Record From Start', True, 'rec_start'),
            ('Record To End',     True, 'rec_end'),
        ] + manual_items + repeat_item
    elif is_current:
        return [
            ('Watch To End',  True, 'watch_end'),
            ('Record To End', True, 'rec_end'),
        ] + manual_items + repeat_item
    elif is_future:
        # A future programme that already has an EPG recording rule
        # gets Remove Recording instead of Record.
        scheduled = False
        if provider and channel and ev is not None:
            manager = getattr(state, 'recording_manager', None)
            if manager is not None:
                for rule in manager.enabled_rules():
                    if (rule.provider_id == provider.provider_id
                            and str(rule.stream_id) == str(channel.stream_id)
                            and rule.event_start == ev.start
                            and rule.event_end == ev.end
                            and rule.last_run != rule.event_start):
                        scheduled = True
                        break

        if scheduled:
            # Deliberately NOT appending manual_items here — a
            # recording is already scheduled for this exact programme,
            # so offering Manual Recording alongside Remove Recording
            # would just let you schedule a second, overlapping one for
            # the same slot. Matches manual_rec_key's own identical
            # scheduled-case return above, which never appended it
            # either.
            return [('Remove Recording', True, 'remove_future')]

        return [('Record', True, 'rec_future')] + manual_items
    else:  # historical — the whole programme already exists on the
           # provider's server, so this is a Download, not a Record.
        if archive:
            return [('Watch', True, 'play'), ('Download', True, 'rec_catchup')] + manual_items
        return [('Watch', False, 'play')] + manual_items


def execute_ctx_action(session, action, provider, channel, ev,
                        recording_path=None,
                        rec_start_offset=0, rec_end_offset=0,
                        catchup_start_offset=0, catchup_end_offset=0,
                        cursor_time=None, epg_store=None):
    """
    Perform the side effect for an action returned by get_ctx_items().
    Shared by Screen 1, the EPG grid, and Live TV's instant-record so
    there's exactly one place that knows how to start/stop a recording.

    Returns True if a recording started or stopped (callers that show
    a recording indicator should refresh their display on True).
    """
    provider = _bq_real_provider(provider, channel)  # bouquet channel -> its own provider
    if getattr(provider, "is_bouquet", False):
        return None           # bouquet channel whose provider is gone
    if action == 'guide_from':
        from .epg_map import open_guide_from
        open_guide_from(session, provider, channel)
        return None
    if action in ('stop_rec_delete', 'stop_rec_keep'):
        # Channel-scoped check for a manually-scheduled recording FIRST
        # — matching get_ctx_items()'s own manual_rec_key logic exactly,
        # including the time-overlap check (see its docstring/comment
        # for why channel alone isn't enough — it previously matched
        # ANY program on the same channel, including an unrelated
        # future one with nothing actually being recorded).
        rec_key = None
        if provider and channel:
            _now = int(_time.time())
            for key, rec in state.active_recordings.items():
                try:
                    if not (rec.get('provider_id') == provider.provider_id
                            and str(rec.get('stream_id')) == str(channel.stream_id)
                            and rec.get('trigger') == 'manual'
                            and rec.get('status') in ('scheduled', 'active')):
                        continue
                    rec_start = rec.get('event_start', 0)
                    rec_end   = rec.get('event_end', 0)
                    if ev is not None:
                        if ev.start < rec_end and ev.end > rec_start:
                            rec_key = key
                            break
                    elif rec_start <= _now < rec_end:
                        rec_key = key
                        break
                except Exception:
                    pass
        # Scope to the specific event when we have one, same as
        # get_ctx_items() — a channel can have more than one concurrent
        # recording (e.g. a live capture and a historical download both
        # in flight), so falling back to get_active_recording()'s loose
        # channel-only match here could stop the wrong one. Only fall
        # back to the loose match when no event was supplied at all.
        if rec_key is None and provider and channel and ev is not None:
            for key, rec in state.active_recordings.items():
                try:
                    if (rec.get('provider_id') == provider.provider_id
                            and str(rec.get('stream_id')) == str(channel.stream_id)
                            and rec.get('event_start') == ev.start):
                        rec_key = key
                        break
                except Exception:
                    pass
        if rec_key is None and provider and ev is None:
            rec_key = get_active_recording(provider, channel, ev)
        if rec_key:
            filename = None
            try:
                filename = state.active_recordings.get(rec_key, {}).get('filename')
            except Exception:
                pass
            stop_recording(rec_key)
            if action == 'stop_rec_delete' and filename:
                # Same fix already proven in screen2.py's Recordings-list
                # delete (see its own comment for the full reasoning) —
                # delete every file sharing this recording's base
                # filename, not just the exact media file. A fixed
                # suffix list assumes every sidecar is named "<full
                # filename>.<suffix>" (true for .json/.meta/.cuts/.ap/
                # .sc), but Enigma2's own .eit sidecar actually replaces
                # the media extension (e.g. "recording.eit", not
                # "recording.ts.eit"), so a plain single-file os.remove
                # here — the only thing this ever did before — always
                # left it and anything else like it behind.
                try:
                    import os as _os
                    import glob as _glob
                    directory, base_filename = _os.path.split(filename)
                    base = _os.path.splitext(base_filename)[0]
                    pattern = _os.path.join(_glob.escape(directory), _glob.escape(base) + '.*')
                    matched = _glob.glob(pattern)
                    for p in matched:
                        try:
                            _os.remove(p)
                        except Exception:
                            pass
                except Exception:
                    pass
                # Belt-and-braces: also remove the exact original path in
                # case it has no extension at all (so the glob above
                # wouldn't match it) or something above missed it.
                try:
                    if _os.path.exists(filename):
                        _os.remove(filename)
                except Exception:
                    pass
        return True

    if action == 'watch_start' and ev:
        watch_from_start_offset(session, provider, channel, ev, catchup_start_offset)
        return False

    if action in ('watch_end', 'play'):
        play_channel(
            session, provider, channel,
            cursor_time=(cursor_time if cursor_time is not None
                         else (ev.start if ev else None)),
            epg_store=epg_store
        )
        return False

    if action == 'rec_start' and ev:
        record_from_start(session, provider, channel, ev, recording_path,
                           catchup_start_offset, catchup_end_offset)
        return True

    if action == 'rec_end' and ev:
        record_to_end(session, provider, channel, ev, recording_path,
                       rec_start_offset, rec_end_offset)
        return True

    if action == 'remove_repeat' and ev:
        from .state import state as _state
        try:
            from .recording.scheduler import repeat_rule_for
            manager = getattr(_state, 'recording_manager', None)
            rule = repeat_rule_for(manager, provider, channel, ev.start, ev.end)
            if rule is not None:
                manager.remove_rule(rule)
                return True
        except Exception as e:
            _rec_log("remove_repeat failed: %s" % e)
        return False

    if action == 'remove_future' and ev:
        from .state import state as _state

        manager = getattr(_state, 'recording_manager', None)

        if manager is None:
            return False

        removed = False
        for rule in manager.enabled_rules():
            if (rule.provider_id == provider.provider_id
                    and str(rule.stream_id) == str(channel.stream_id)
                    and rule.event_start == ev.start
                    and rule.event_end == ev.end
                    and rule.last_run != rule.event_start):
                manager.remove_rule(rule)
                removed = True
                break

        # Also clear the matching active_recordings tracking entry —
        # add_rule's own immediate-launch design (see its docstring)
        # means even a future-scheduled recording already has a
        # sleeping thread and a "scheduled" entry the moment it's
        # created, same as a manually-scheduled one. This action
        # previously only ever removed the rule, never this — the rule
        # store correctly showed it gone (so re-opening the menu
        # correctly offered Record again), but the tile's own colour
        # reads active_recordings separately, which still had the
        # stale "scheduled" entry, so it kept showing green regardless
        # of any redraw (same gap already found and fixed for
        # Manual Recording's own remove_manual action, just never
        # applied here to this older, pre-existing path).
        if provider and channel:
            for key, rec in _state.active_recordings.items():
                try:
                    if (rec.get('provider_id') == provider.provider_id
                            and str(rec.get('stream_id')) == str(channel.stream_id)
                            and rec.get('event_start') == ev.start
                            and rec.get('status') == 'scheduled'):
                        stop_recording(key)
                        removed = True
                        break
                except Exception:
                    pass

        return removed

    if action == 'remove_manual':
        # Cancels a scheduled (not yet started) manual recording —
        # channel + time-overlap matched, same reasoning as
        # get_ctx_items' own manual_rec_key (a manually-entered start
        # rarely lines up exactly with any EPG-aligned tile). Removes
        # BOTH the underlying rule (so the background scheduler can't
        # recreate it) AND the active_recordings tracking entry (so the
        # still-sleeping thread aborts when it wakes, and the tile
        # stops showing green immediately) — removing only the rule,
        # via manager.remove_rule alone, leaves that thread and tile
        # untouched; removing only the tracking entry, via
        # stop_recording alone, leaves the rule for the scheduler to
        # resurrect shortly after. Both together is what "remove"
        # actually needs to mean here.
        from .state import state as _state

        manager = getattr(_state, 'recording_manager', None)
        if manager is None:
            return False

        _now = int(_time.time())
        removed = False

        for rule in manager.enabled_rules():
            if not (rule.provider_id == provider.provider_id
                    and str(rule.stream_id) == str(channel.stream_id)
                    and getattr(rule, 'trigger', None) == TriggerType.MANUAL):
                continue
            overlaps = (
                ev.start < rule.event_end and ev.end > rule.event_start
                if ev is not None
                else rule.event_start <= _now < rule.event_end
            )
            if overlaps:
                manager.remove_rule(rule)
                removed = True
                break

        rec_key = None
        for key, rec in _state.active_recordings.items():
            try:
                if not (rec.get('provider_id') == provider.provider_id
                        and str(rec.get('stream_id')) == str(channel.stream_id)
                        and rec.get('trigger') == 'manual'
                        and rec.get('status') == 'scheduled'):
                    continue
                rec_start = rec.get('event_start', 0)
                rec_end   = rec.get('event_end', 0)
                overlaps = (
                    ev.start < rec_end and ev.end > rec_start
                    if ev is not None
                    else rec_start <= _now < rec_end
                )
                if overlaps:
                    rec_key = key
                    break
            except Exception:
                pass
        if rec_key:
            stop_recording(rec_key)
            removed = True

        return removed

    if action == 'rec_future' and ev:
        from .state import state as _state

        manager = getattr(_state, 'recording_manager', None)

        if manager is None:
            return False

        rule = manager.create_rule(
            provider,
            channel,
            ev,
            rec_start_offset,
            rec_end_offset,
            [],
            ev.title,
            recording_path
        )

        rule.trigger = "epg"

        return bool(manager.add_rule(rule))

    if action == 'rec_catchup' and ev:
        record_catchup(session, provider, channel, ev, recording_path,
                        catchup_start_offset, catchup_end_offset)
        return True

    return False


def watch_from_start_offset(session, provider, channel, ev, start_offset_mins=0):
    """Play catchup from ev.start minus optional offset (mins)."""
    provider = _bq_real_provider(provider, channel)  # bouquet channel -> its own provider
    if getattr(provider, "is_bouquet", False):
        return None           # bouquet channel whose provider is gone
    import copy as _copy
    ev_adj       = _copy.copy(ev)
    ev_adj.start = ev.start - start_offset_mins * 60
    url          = build_catchup_url(provider, channel, ev_adj)
    title        = "%s — %s (from start)" % (channel.name, ev.title)
    session.open(E2iPTVPlayer, url, title)
    _show_programme_overlay(session, channel.name, ev.title, ev.start, ev.end)


def record_from_start(session, provider, channel, ev,
                      recording_path, start_offset=0, end_offset=0):
    """Record catchup stream from ev.start (minus offset) to ev.end (plus offset)."""
    import copy as _copy
    ev_adj       = _copy.copy(ev)
    ev_adj.start = ev.start - start_offset * 60
    url          = build_catchup_url(provider, channel, ev_adj)
    begin        = int(_time.time())
    end          = ev.end + end_offset * 60
    return _create_timer(
        session,
        provider,
        channel,
        ev,
        url,
        begin,
        end,
        channel.name,
        ev.title,
        recording_path,
        reconnect=False
    )


def record_to_end(session, provider, channel, ev,
                  recording_path, start_offset=0, end_offset=0):
    """Record live stream via wget."""
    url   = build_live_url(provider, channel)
    begin = int(_time.time())
    end   = ev.end + end_offset * 60
    return _create_timer(
        session,
        provider,
        channel,
        ev,
        url,
        begin,
        end,
        channel.name,
        ev.title,
        recording_path
    )


def record_future(session, provider, channel, ev,
                  recording_path, start_offset=0, end_offset=0):
    """Schedule future recording from ev.start (minus offset) to ev.end (plus offset)."""
    url   = build_live_url(provider, channel)
    begin = ev.start - start_offset * 60
    end   = ev.end   + end_offset   * 60
    return _create_timer(
        session,
        provider,
        channel,
        ev,
        url,
        begin,
        end,
        channel.name,
        ev.title,
        recording_path
    )


def record_catchup(session, provider, channel, ev,
                   recording_path, start_offset=0, end_offset=0):
    """Record a historical catchup stream."""

    import copy as _copy

    ev_adj = _copy.copy(ev)
    ev_adj.start = ev.start - start_offset * 60

    url = build_catchup_url(provider, channel, ev_adj)

    begin = int(_time.time())

    duration = (
        (ev.end - ev.start)
        + (start_offset * 60)
        + (end_offset * 60)
    )

    if duration < 60:
        duration = 60

    end = begin + duration

    return _create_timer(
        session,
        provider,
        channel,
        ev,
        url,
        begin,
        end,
        channel.name,
        ev.title,
        recording_path,
        reconnect=False
    )