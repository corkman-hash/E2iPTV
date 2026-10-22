# =============================================================================
# E2iPTV — Screen 3 — EPG Grid
# B3: channel list | B5: programme tile grid on shared time axis
# =============================================================================

from . import theme as _T
from .info_box import E2iBox as _E2iBox
from .paths import LOG  # data folder / debug logs
from enigma import eActionMap
from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.GUIComponent import GUIComponent
from Components.MultiContent import MultiContentEntryText, MultiContentEntryPixmapAlphaBlend
from Tools.LoadPixmap import LoadPixmap
from enigma import (
    eTimer, eListbox, eListboxPythonMultiContent,
    gFont, gRGB, ePoint, RT_HALIGN_LEFT, RT_HALIGN_CENTER, RT_VALIGN_CENTER
)

# See screen1.py's identical block for why this exists: without
# BT_SCALE/BT_KEEP_ASPECT_RATIO, MultiContentEntryPixmapAlphaBlend draws
# a picon at its native resolution instead of scaled to fit the box,
# which is why picons here were showing as small cropped fragments.
try:
    from enigma import BT_SCALE, BT_KEEP_ASPECT_RATIO
    PICON_FLAGS = BT_SCALE | BT_KEEP_ASPECT_RATIO
except ImportError:
    PICON_FLAGS = 0
from twisted.internet import reactor
from .state import state, _epg_recording_colour
from .player import (play_channel, watch_from_start, watch_from_start_offset,
                     record_from_start, record_to_end, record_future,
                     record_catchup, stop_recording, get_active_recording,
                     build_live_url, _create_timer_direct,
                     get_ctx_items, execute_ctx_action)
from .backend.epg import Epg
from .recording.status import TriggerType
from .b3common import B3Common
import os
import time

# Lines in the EPG grid's OK menu (was 8, which left no room for some
# options, e.g. Show Hidden Channels on a current programme with
# catch-up). 110 + 12 x 66 = 902px, within the full-height panel.
SMENU_ROWS = 12

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

BG_INT      = _T.PANEL   # dark background / separator
TILE_INT    = _T.RAIL   # normal programme tile
CURSOR_INT  = _T.HI   # selected programme tile (B5 cursor)
B3_TILE_INT = _T.MUTED   # B3 focused row — dark blue
HIST_INT    = _T.HIST   # historical programme tile — teal
NOW_INT     = 0xff0000   # red now-line

REC_WAIT_INT = 0x44cc44   # Green = scheduled
REC_REC_INT  = 0xff4444   # Red = recording

EPG_STEP    = 1800        # 30 minutes
EPG_WINDOW  = 7200        # 2-hour visible window
B3_W        = 620
B5_W        = 1300
ITEM_H      = 66
LIST_Y      = 415
B3_VISIBLE  = 10


# -----------------------------------------------------------------------------
# Listbox component (shared by B3 and B5)
# -----------------------------------------------------------------------------

class IE2iList(GUIComponent):
    GUI_WIDGET = eListbox

    def __init__(self, item_h):
        GUIComponent.__init__(self)
        self.l = eListboxPythonMultiContent()
        self.l.setFont(0, gFont("E2iRegular", 28))
        self.l.setFont(1, gFont("E2iRegular", 24))
        self.l.setItemHeight(item_h)
        self._entries = []

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setWrapAround(False)

    def preWidgetRemove(self, instance):
        pass

    def setList(self, entries):
        self._entries = entries
        self.l.setList(entries)

    def moveToIndex(self, idx):
        if self.instance and self._entries:
            self.instance.moveSelectionTo(
                min(idx, max(0, len(self._entries) - 1))
            )


# -----------------------------------------------------------------------------
# Screen 3
# -----------------------------------------------------------------------------

class Screen3(B3Common, Screen):

    skin = (
        '<screen name="E2iPTV_S3" position="0,0" size="1920,1080" flags="wfNoBorder">'
        '<eLabel position="0,0" size="1920,1080" backgroundColor="E2iPanel" zPosition="0"/>'

        # PiG top left
        '<widget source="session.VideoPicture" render="Pig"'
        ' position="50,20" size="560,315"'
        ' backgroundColor="#ff000000" transparent="1" zPosition="10"/>'
        '<eLabel position="40,10" size="560,315"'
        ' backgroundColor="transparent" zPosition="11"/>'

        # Info panel top right
        '<widget name="info_title" position="630,10" size="1280,54"'
        ' font="E2iRegular;40" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
        ' valign="center" zPosition="5"/>'
        '<widget name="info_times" position="630,68" size="640,36"'
        ' font="E2iRegular;28" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
        ' valign="center" zPosition="5"/>'
        '<widget name="info_prov" position="1280,68" size="630,36"'
        ' font="E2iRegular;26" foregroundColor="#aaaaaa" backgroundColor="E2iPanel"'
        ' valign="center" halign="right" zPosition="5"/>'
        '<widget name="info_desc" position="630,108" size="1280,165"'
        ' font="E2iRegular;26" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
        ' valign="top" zPosition="5"/>'

        # Date/time over B3, time markers over B5
        '<widget name="b5_date" position="0,381" size="620,34"'
        ' font="E2iRegular;24" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
        ' valign="center" halign="center" zPosition="3"/>'
        '<widget name="b5_mark0" position="620,381"  size="325,34"'
        ' font="E2iRegular;24" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
        ' valign="center" halign="left" zPosition="3"/>'
        '<widget name="b5_mark1" position="945,381"  size="325,34"'
        ' font="E2iRegular;24" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
        ' valign="center" halign="left" zPosition="3"/>'
        '<widget name="b5_mark2" position="1270,381" size="325,34"'
        ' font="E2iRegular;24" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
        ' valign="center" halign="left" zPosition="3"/>'
        '<widget name="b5_mark3" position="1595,381" size="325,34"'
        ' font="E2iRegular;24" foregroundColor="#FFFFFF" backgroundColor="E2iPanel"'
        ' valign="center" halign="left" zPosition="3"/>'

        # B3 channel list
        '<widget name="b3_list" position="0,415" size="620,660"'
        ' zPosition="3" backgroundColor="E2iPanel"/>'

        # B5 EPG grid
        '<widget name="b5_list" position="620,415" size="1300,660"'
        ' zPosition="3" backgroundColor="E2iPanel"/>'

        # Side recording menu — same position/approach as screen2 settings overlay
        '<widget name="smenu_bg"  position="3500,0" size="568,1080"'
        ' backgroundColor="E2iRail" zPosition="20"/>'
        '<widget name="smenu_hdr" position="3500,30" size="528,60"'
        ' font="E2iRegular;32" foregroundColor="#FFFFFF"'
        ' backgroundColor="E2iRail" halign="left" valign="center" zPosition="22"/>'
        '<widget name="smenu_sel" position="3500,110" size="528,66" cornerRadius="14"'
        ' backgroundColor="E2iHi" zPosition="21"/>'
        + "".join(
            '<widget name="smenu_t%d" position="3500,110" size="528,66"'
            ' font="E2iRegular;28" foregroundColor="#FFFFFF"'
            ' halign="left" valign="center" transparent="1" zPosition="22"/>' % i
            for i in range(SMENU_ROWS)
        ) +

        '</screen>'
    )

    def __init__(self, session):
        # Temporary diagnostic (see matching entry in screen1._on_epg):
        # confirms Screen3 construction is actually reached at all, and
        # whether it gets past Screen.__init__ / skin binding on this
        # image before anything else in this constructor can throw.
        # Safe to remove once the "b2 -> full EPG grid" transition is
        # confirmed working on OpenBH.
        try:
            with open(LOG("e2iptv_epg_key.log"), "a") as _lg:
                _lg.write("%s Screen3.__init__ entered, channels=%d\n" %
                          (time.strftime("%Y-%m-%d %H:%M:%S"), len(state.channels or [])))
        except Exception:
            pass
        Screen.__init__(self, session)
        try:
            with open(LOG("e2iptv_epg_key.log"), "a") as _lg:
                _lg.write("%s Screen3.__init__ past Screen.__init__\n" %
                          time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass

        # Navigation state
        # Always start a newly opened category at the first channel.
        self._focused        = 0
        self._scroll         = 0
        self._is_historical  = False   # only True after explicit left-nav into past

        # Time state — cursor fixed at now, only left/right moves it
        now = int(time.time())
        self._time_base    = now
        self._cursor_time  = now
        self._window_start = now

        # Register widgets
        self["info_title"] = Label("")
        self["info_times"] = Label("")
        self["info_prov"]  = Label("")
        self["info_desc"]  = Label("")
        self["b5_date"]    = Label("")
        for i in range(4):
            self["b5_mark%d" % i] = Label("")
        self["b3_list"]   = IE2iList(ITEM_H)
        self["b5_list"]   = IE2iList(ITEM_H)
        self["smenu_bg"]  = Label("")
        self["smenu_hdr"] = Label("")
        self["smenu_sel"] = Label("")
        for i in range(SMENU_ROWS):
            self["smenu_t%d" % i] = Label("")
        self._menu_active    = False
        self._menu_sel       = 0
        self._custom_active  = False
        self._custom_sel     = 0
        self._custom_start   = 0
        self._custom_end     = 0

        self._repeat_active  = False
        self._repeat_sel     = 0
        self._repeat_days    = [
            False, False, False, False,
            False, False, False
        ]

        self._last_event_id = None
        self._last_now_slot = None

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions",
             "InfobarEPGActions", "EPGSelectActions",
             "InfobarActions", "InfobarInstantRecord",
             "NumberActions"],
            {
                "cancel":        self._on_cancel,
                "ok":            self._on_ok,
                "up":            self._on_up,
                "down":          self._on_down,
                "left":          self._on_left,
                "right":         self._on_right,
                "epg":           self._on_epg_key,
                "showEPG":       self._on_epg_key,
                "EPGPressed":    self._on_epg_key,
                "showEventInfo": self._on_epg_key,
                # OpenBH-safe equivalent of the EPG key above (jump to
                # now / close if already there) — the EPG key itself is
                # intercepted before it ever reaches this ActionMap on
                # that image (see screen1._on_epg's comments), and 0 is
                # both untouched by that interception and the standard
                # "jump to now" convention in most native Enigma2 EPG
                # grids, so it's not an arbitrary substitute.
                "0":             self._on_epg_key,
                # 5 twice quickly: show/hide Private categories. The grid
                # has no category list of its own - the change shows in
                # Live TV's list when you go back to it.
                "5":             self._private_key,
                # Record key is a second way in to the same Watch/Record/
                # Download menu OK already opens — the Main EPG Grid is
                # one of the confirmed recording-trigger points, so it
                # gets its own dedicated button too, behaving identically.
                "record":        self._on_ok,
                "instantRecord": self._on_ok,
            },
            -1
        )

        try:
            with open(LOG("e2iptv_epg_key.log"), "a") as _lg:
                _lg.write("%s Screen3.__init__ completed (end of constructor)\n" %
                          time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass
        self.onLayoutFinish.append(self._on_ready)
        self.onExecBegin.append(self._goto_check)

        # Menu: the icon rail over the grid (rail_menu.py). Caught by its
        # raw key code - the image takes the Menu key before any named
        # action reaches a plugin screen (same as the player).
        self._menu_key_handler = self._on_raw_menu_key
        eActionMap.getInstance().bindAction("", -0x7FFFFFFF, self._menu_key_handler)
        self.onClose.append(self._unbind_menu_key)
        self.onShow.append(self._on_shown)

        self._epg_debounce = eTimer()
        self._epg_debounce.callback.append(self._epg_debounce_fired)

        self._refresh_timer = eTimer()
        self._refresh_timer.callback.append(self._refresh_tick)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _on_shown(self):
        """Called when screen3 becomes visible — syncs focus and time from state."""
        try:
            with open(LOG("e2iptv_livetv_tune_debug.log"), "a") as _f:
                _f.write(
                    "%s _on_shown: open_prov=%r(%r) channels_len=%r "
                    "self._focused=%r state.b5_focused=%r "
                    "first_channel=%r\n" % (
                        time.strftime("%H:%M:%S"),
                        getattr(state.open_prov, "name", None),
                        getattr(state.open_prov, "provider_id", None),
                        len(state.channels) if state.channels else 0,
                        self._focused, getattr(state, "b5_focused", None),
                        getattr(state.channels[0], "name", None)
                        if state.channels else None
                    )
                )
        except Exception:
            pass
        if not state.channels:
            return
        # Update focused channel if state changed (e.g. browser selected new channel)
        new_focused = getattr(state, 'b5_focused', self._focused)
        if new_focused != self._focused and 0 <= new_focused < len(state.channels):
            self._focused = new_focused
            self._clamp_scroll()
        # Reset to current time if cursor is stale (more than 10 minutes old)
        now = int(time.time())
        if self._cursor_time < now - 600:
            self._cursor_time    = now
            self._window_start   = (now // 1800) * 1800 - 1800
            self._is_historical  = False
        self._refresh_time_bar()
        self._b3_refresh()
        self._b5_refresh()
        self._update_info()

    def _on_ready(self):
        try:
            with open(LOG("e2iptv_epg_key.log"), "a") as _lg:
                _lg.write("%s Screen3._on_ready fired (onLayoutFinish reached)\n" %
                          time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass
        self._refresh_time_bar()
        self._b3_refresh()
        self._b5_refresh()
        self._update_info()
        self._fetch_visible_epg()

        self._last_now_slot = int(time.time()) // 30

        if state.channels:
            ch = state.channels[self._focused]
            ev = self._find_event(
                self._get_events(ch),
                self._cursor_time
            )

            self._last_event_id = (
                (ev.start, ev.end, ev.title)
                if ev else None
            )

        self._refresh_timer.start(30000, True)
    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_cancel(self):
        if getattr(self, "_move_active", False):
            self._finish_move(save=False)   # Exit = put it back
            return
        if self._repeat_active:
            self._hide_repeat_panel()
            return

        if self._custom_active:
            self._hide_custom()
            self._show_ctx()   # back to recording menu
            return
        if self._menu_active:
            self._hide_ctx()
            return
        # Exit no longer leaves the grid (use the EPG key to go back to
        # the categories). It still closes menus and cancels a move.
        return

    def _on_ok(self):
        if getattr(self, "_move_active", False):
            self._finish_move(save=True)    # OK = drop it here
            return
        if self._repeat_active:
            self._repeat_ok()
            return

        if self._custom_active:
            self._custom_ok()
            return

        if self._menu_active:
            self._ctx_execute()
            return
        if not state.channels or not state.open_prov:
            return
        self._show_ctx()

    def _on_up(self):
        if getattr(self, "_move_active", False):
            self._move_step(-1)
            return

        if self._repeat_active:
            if self._repeat_sel > 0:
                self._repeat_sel -= 1
                self._repeat_refresh()
            return

        if self._custom_active:
            if self._custom_sel > 0:
                self._custom_sel -= 1
                self._custom_refresh()
            return
        if self._menu_active:
            if self._menu_sel > 0:
                self._menu_sel -= 1
                self._ctx_refresh()
            return
        self._step_channel(-1)

    def _on_down(self):
        if getattr(self, "_move_active", False):
            self._move_step(+1)
            return

        if self._repeat_active:
            if self._repeat_sel < 7:
                self._repeat_sel += 1
                self._repeat_refresh()
            return

        if self._custom_active:
            if self._custom_sel < 4:
                self._custom_sel += 1
                self._custom_refresh()
            return
        if self._menu_active:
            n = len(self._get_ctx_items())
            if self._menu_sel < n - 1:
                self._menu_sel += 1
                self._ctx_refresh()
            return
        self._step_channel(+1)

    def _step_channel(self, delta):
        """Move the highlight one channel up/down, wrapping round from the
        bottom of the list to the top and vice versa (long categories).
        While browsing catch-up, channels without catch-up are skipped,
        as before."""
        n = len(state.channels)
        if n == 0:
            return
        new_focused = self._focused
        for _ in range(n):
            new_focused = (new_focused + delta) % n
            if not self._is_historical or state.channels[new_focused].tv_archive:
                break
        else:
            # No catch-up channels to skip to (e.g. a provider without
            # catch-up): just move to the next channel as normal.
            new_focused = (self._focused + delta) % n
        if new_focused == self._focused:
            return
        self._focused = new_focused
        self._clamp_scroll()
        self._save_state()
        self._b3_refresh()
        self._b5_refresh()
        self._update_info()
        self._fetch_visible_epg()

    def _on_right(self):
        if getattr(self, "_move_active", False):
            return
        if self._repeat_active:
            return

        if self._custom_active:
            self._custom_change(+1)
            return
        if not state.channels:
            return
        ch = state.channels[self._focused]
        # Windowed events (real + synthetic) plus future real events beyond window
        windowed = self._get_events(ch, self._window_start, self._window_start + EPG_WINDOW)
        all_real = self._get_events(ch)
        future   = [e for e in all_real if e.start >= self._window_start + EPG_WINDOW]
        events   = sorted(windowed + future, key=lambda e: e.start)
        for ev in events:
            if ev.start > self._cursor_time:
                self._cursor_time = ev.start
                if self._cursor_time >= self._time_base:
                    self._is_historical = False
                self._ensure_cursor_visible()
                self._refresh_time_bar()
                self._b5_refresh()
                self._update_info()
                return
        self._cursor_time += EPG_STEP
        if self._cursor_time >= self._time_base:
            self._is_historical = False
        self._ensure_cursor_visible()
        self._refresh_time_bar()
        self._b5_refresh()
        self._update_info()

    def _private_key(self):
        from .backend.private import key_pressed
        key_pressed(self.session)

    _KEY_MENU = 139

    def _unbind_menu_key(self):
        try:
            eActionMap.getInstance().unbindAction("", self._menu_key_handler)
        except Exception:
            pass

    _KEY_EPG = 365

    def _on_raw_menu_key(self, key, flag):
        if key not in (self._KEY_MENU, self._KEY_EPG):
            return 0
        # Plugin closed (Exit / Close): leave Menu and EPG to the box.
        if not getattr(state, "plugin_open", False):
            return 0
        try:
            if self.session.current_dialog is not self:
                return 0
        except Exception:
            return 0
        if key == self._KEY_EPG:
            # Caught by its key code too: OpenViX (like OpenBH) doesn't
            # hand the EPG key to plugin screens by name. Taken here, the
            # named "epg" actions below never fire as well, so it can't
            # act twice on images that do.
            if flag == 0:
                reactor.callLater(0, self._on_epg_key)
            return 1
        if flag == 0:
            reactor.callLater(0, self._open_rail_menu)
        return 1

    def _open_rail_menu(self):
        try:
            from .rail_menu import RailMenu
            self.session.openWithCallback(self._rail_menu_closed, RailMenu, "EPG Grid")
        except Exception as e:
            print("[E2iPTV] Menu pop-up:", e)

    def _rail_menu_closed(self, choice=None):
        """Go to the picked section: step back to Live TV, which opens
        it (goto.py) - Live TV goes full screen, EPG Grid stays here."""
        if not choice or choice[0] == "EPG Grid":
            return
        from .goto import request
        state.live_choice = "full" if choice[0] == "Live TV" else None
        request(choice[1])
        state.b5_focused      = self._focused
        state.b5_scroll       = self._scroll
        state.b5_window_start = self._window_start
        state.b5_cursor_time  = self._cursor_time
        self.close(True)

    def _goto_check(self):
        # Each time the grid is the screen in use (opened, or back from
        # the full-screen picture): open the plugin here next time - but
        # not when it's only being passed through on the way to Exit or
        # another section (Menu rail on the full-screen picture), which
        # used to overwrite "Live TV" on the way out.
        from .goto import peek
        if peek() is None:
            try:
                from .b1common import save_b1_last_choice
                save_b1_last_choice("EPG Grid")
            except Exception:
                pass
        self._goto_check_pending()

    def _goto_check_pending(self):
        """Became the active screen again (e.g. the player above closed):
        if the player's Menu asked for another section, step back to Live
        TV, which opens it (goto.py)."""
        # EPG Grid picked from the player's Menu rail and the grid was
        # already underneath: just stay here.
        if getattr(state, "live_choice", None) == "grid":
            state.live_choice = None
        from .goto import peek
        if peek() is None:
            return
        state.b5_focused      = self._focused
        state.b5_scroll       = self._scroll
        state.b5_window_start = self._window_start
        state.b5_cursor_time  = self._cursor_time
        reactor.callLater(0, self.close, True)

    def _on_epg_key(self):
        """EPG key: snap to current if not there, return to B2 if already at current."""
        if getattr(self, "_move_active", False):
            return
        import time as _t
        now = int(_t.time())
        if not state.channels:
            return
        ch     = state.channels[self._focused]
        # Use windowed events (includes synthetic tiles) to detect current position
        events = self._get_events(ch, self._window_start, self._window_start + EPG_WINDOW)
        ev     = self._find_event(events, self._cursor_time)
        at_current = ev and ev.start <= now < ev.end
        # No guide data for this channel: nothing to be "on", so treat
        # the cursor being at the current time as on-now. Otherwise 0/EPG
        # would just keep re-centring and, with Exit no longer closing
        # the grid, there'd be no way back to the categories.
        if not ev and abs(self._cursor_time - now) < EPG_STEP:
            at_current = True
        if at_current:
            state.b5_focused      = self._focused
            state.b5_scroll       = self._scroll
            state.b5_window_start = self._window_start
            state.b5_cursor_time  = self._cursor_time
            self.close(True)
        else:
            self._cursor_time    = now
            self._is_historical  = False
            self._window_start   = (now // EPG_STEP) * EPG_STEP - EPG_STEP
            self._ensure_cursor_visible()
            self._refresh_time_bar()
            self._b5_refresh()
            self._update_info()

    def _on_left(self):
        if getattr(self, "_move_active", False):
            return
        if self._repeat_active:
            return

        if self._custom_active:
            self._custom_change(-1)
            return
        if not state.channels:
            return

        ch = state.channels[self._focused]
        # Get windowed events (real + synthetic gap fill) for current window
        windowed = self._get_events(ch, self._window_start, self._window_start + EPG_WINDOW)
        # Also get real historical events before the window
        all_real = self._get_events(ch)
        historical = [e for e in all_real if e.end <= self._window_start]
        # Merge: historical real events + current window with synthetics
        events = sorted(historical + windowed, key=lambda e: e.start)

        prev = None
        for ev in events:
            if ev.start < self._cursor_time:
                prev = ev
        if prev:
            self._cursor_time = prev.start
            # Catch-up browsing only once the highlighted programme has
            # actually finished. A programme still on now started in the
            # past too, but it's live — treating it as catch-up limited
            # Up/Down to catch-up channels, which on a provider with none
            # (seen on one provider) meant Up/Down did nothing at all.
            if prev.end <= int(time.time()):
                self._is_historical = True
            self._ensure_cursor_visible()
            self._refresh_time_bar()
            self._b5_refresh()
            self._update_info()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    SMENU_X = 1352
    SMENU_W = 568
    SMENU_ITEM_Y0 = 110
    SMENU_ITEM_H  = 66

    def _show_ctx(self):
        if not state.channels:
            return
        self._menu_active = True
        self._menu_sel    = 0
        ch   = state.channels[self._focused]
        hdr  = ch.name
        try:
            self["smenu_hdr"].setText("  " + hdr)
            self["smenu_bg"].instance.move(ePoint(self.SMENU_X, 0))
            self["smenu_hdr"].instance.move(ePoint(self.SMENU_X + 20, 30))
        except Exception:
            pass
        self._ctx_refresh()

    def _hide_ctx(self):
        self._menu_active = False
        try:
            self["smenu_bg"].instance.move(ePoint(3500, 0))
            self["smenu_hdr"].instance.move(ePoint(3500, 0))
            self["smenu_sel"].instance.move(ePoint(3500, 0))
            for i in range(SMENU_ROWS):
                self["smenu_t%d" % i].instance.move(ePoint(3500, 0))
        except Exception:
            pass

    def _get_ctx_items(self):
        """Delegates to the module-level get_ctx_items() in player.py —
        the single shared source for this menu's content, also used by
        Screen 1's ChannelBrowser and Live TV's instant-record. Custom
        Record (scheduling) is dropped here for now too, to match —
        it's deferred along with the rest of the scheduling feature."""
        if not state.channels:
            return []
        ch = state.channels[self._focused]
        ev = self._find_event(state.backend.epg_store.get_for(ch), self._cursor_time)
        items = list(get_ctx_items(state.open_prov, ch, ev))
        # "Guide from..." goes last, after the channel's own options
        guide = [i for i in items if i[2] == "guide_from"]
        items = [i for i in items if i[2] != "guide_from"]
        items = self._ctx_channel_items(items, ch)
        if guide and len(items) < SMENU_ROWS:
            items.append(guide[0])
        return items

    def _ctx_channel_items(self, items, ch):
        in_bouquet = getattr(state.open_prov, "is_bouquet", False)
        if in_bouquet and len(items) < SMENU_ROWS:
            items.append(("Remove from Bouquet", True, "bouquet_remove"))
        if in_bouquet and len(items) < SMENU_ROWS:
            items.append(("Delete Bouquet", True, "bouquet_delete"))
        if len(items) < SMENU_ROWS and not getattr(ch, "_bq_missing", False):
            items.append(("Copy to Bouquet", True, "bouquet_copy"))
        if in_bouquet:
            # Inside a bouquet: Move Channel only (Remove from Bouquet
            # does the job of hiding there).
            if len(items) < SMENU_ROWS and len(state.channels) > 1:
                items.append(("Move Channel", True, "move_channel"))
            return items
        # Reorder this category (saved to its channel order file). It's
        # about the channel, not the programme, so it's offered whatever
        # programme is highlighted — past, current or future.
        if len(items) < SMENU_ROWS and len(state.channels) > 1:
            items.append(("Move Channel", True, "move_channel"))
        if len(items) < SMENU_ROWS and len(state.channels) > 1:
            items.append(("Hide Channel", True, "hide_channel"))
        n_hidden = len(self._hidden_list())
        if n_hidden and len(items) < SMENU_ROWS:
            items.append(("Show Hidden Channels (%d)" % n_hidden, True, "show_hidden"))
        return items

    def _hidden_list(self):
        """This category's hidden channels, read once and remembered
        (the menu is redrawn on every key press, so no disk reads here)."""
        key = (getattr(state.open_prov, "provider_key", None),
               getattr(state.sel_cat, "category_id", None))
        cache = getattr(self, "_hidden_cache", None)
        if cache is None or cache[0] != key:
            try:
                from .backend.channel_order import hidden as _hidden
                rows = _hidden(state.open_prov, state.sel_cat)
            except Exception:
                rows = []
            self._hidden_cache = cache = (key, rows)
        return cache[1]

    def _ctx_refresh(self):
        items = self._get_ctx_items()
        x     = self.SMENU_X + 20
        y0    = self.SMENU_ITEM_Y0
        ih    = self.SMENU_ITEM_H
        # Hide all slots first
        for i in range(SMENU_ROWS):
            try:
                self["smenu_t%d" % i].instance.move(ePoint(3500, 0))
            except Exception:
                pass
        # Show items
        for i, (label, active, action) in enumerate(items):
            y = y0 + i * ih
            try:
                self["smenu_t%d" % i].setText("  " + label)
                self["smenu_t%d" % i].instance.move(ePoint(x, y))
                col = gRGB(0x000000) if i == self._menu_sel else \
                      gRGB(0x888888) if not active else gRGB(0xffffff)
                self["smenu_t%d" % i].instance.setForegroundColor(col)
            except Exception:
                pass
        try:
            sy = y0 + self._menu_sel * ih
            self["smenu_sel"].instance.move(ePoint(x - 5, sy))
        except Exception:
            pass

    def _ctx_execute(self):
        """Delegates to the module-level execute_ctx_action() in
        player.py — the same shared executor Screen 1 and Live TV's
        instant-record use, so a fix here is a fix everywhere."""
        items = self._get_ctx_items()
        if self._menu_sel >= len(items):
            self._hide_ctx()
            return
        label, active, action = items[self._menu_sel]
        if not active:
            return
        self._hide_ctx()
        ch = state.channels[self._focused]
        ev = self._find_event(
                state.backend.epg_store.get_for(ch), self._cursor_time)
        p  = state.open_prov

        try:
            with open(LOG("e2iptv_livetv_tune_debug.log"), "a") as _f:
                _f.write(
                    "%s _ctx_execute: action=%r open_prov=%r(%r) "
                    "focused_idx=%r channels_len=%r channel=%r(stream_id=%r) "
                    "b5_focused=%r\n" % (
                        time.strftime("%H:%M:%S"), action,
                        getattr(p, "name", None), getattr(p, "provider_id", None),
                        self._focused, len(state.channels),
                        getattr(ch, "name", None), getattr(ch, "stream_id", None),
                        getattr(state, "b5_focused", None)
                    )
                )
        except Exception:
            pass

        if action == "move_channel":
            self._start_move()
            return

        if action == "bouquet_copy":
            self._bouquet_choose(ch)
            return

        if action == "bouquet_remove":
            self._bouquet_confirm_remove(ch)
            return

        if action == "bouquet_delete":
            self._bouquet_confirm_delete()
            return

        if action == "hide_channel":
            self._confirm_hide(ch)
            return

        if action == "show_hidden":
            self._show_hidden_choice()
            return

        if action == "rec_manual":
            self._show_custom(ev)
            return

        execute_ctx_action(
            self.session, action, p, ch, ev,
            recording_path=state.recording_path,
            rec_start_offset=getattr(state, 'rec_start_offset', 0),
            rec_end_offset=getattr(state, 'rec_end_offset', 0),
            catchup_start_offset=getattr(state, 'catchup_start_offset', 0),
            catchup_end_offset=getattr(state, 'catchup_end_offset', 0),
            cursor_time=self._cursor_time,
            epg_store=state.backend.epg_store,
        )
        self._b5_refresh()
        self._update_info()

    # ------------------------------------------------------------------
    # Custom Record panel
    # ------------------------------------------------------------------

    _DAY_NAMES = [
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
    ]

    def _show_custom(self, ev):
        """Open the Manual Recording panel — 3 fields (Date/Start/End),
        OK always schedules regardless of which field has focus, same
        interaction model as ChannelBrowser's identical panel in
        player.py (that pair should always behave identically — same
        feature, just reached from two different screens). Defaults:
        the tile's own real EPG start/end if it has them — "real"
        meaning not the synthetic 30-min "No EPG Available" filler
        (see _get_events above), which would be a misleading default
        to prefill here of all places — falling back to "now, rounded
        to the nearest 5 minutes" / "+1 hour" otherwise, so just
        pressing OK immediately reproduces what used to be a separate
        "Record from Now" option, no extra step needed for that
        common case."""
        self._hide_ctx()
        self._custom_active = True
        self._custom_sel    = 0

        now = int(time.time())
        default_start = now
        default_end   = now + 3600
        if ev is not None and getattr(ev, 'title', '') != 'No EPG Available':
            if getattr(ev, 'start', 0):
                default_start = ev.start
            if getattr(ev, 'end', 0):
                default_end = ev.end
        else:
            default_start = (now // 300) * 300

        self._custom_start = default_start
        self._custom_end   = default_end
        self._repeat_days  = [False] * 7
        self._custom_title = (ev.title if ev is not None
                              and getattr(ev, 'title', '') not in ('', 'No EPG Available')
                              else None)
        if self._custom_end <= self._custom_start:
            self._custom_end = self._custom_start + 3600

        # Show smenu panel
        try:
            self["smenu_hdr"].setText("  Manual Recording")
            self["smenu_bg"].instance.move(ePoint(self.SMENU_X, 0))
            self["smenu_hdr"].instance.move(ePoint(self.SMENU_X + 20, 30))
        except Exception:
            pass
        self._custom_refresh()

    def _hide_custom(self):
        self._custom_active = False
        try:
            self["smenu_bg"].instance.move(ePoint(3500, 0))
            self["smenu_hdr"].instance.move(ePoint(3500, 0))
            self["smenu_sel"].instance.move(ePoint(3500, 0))
            for i in range(SMENU_ROWS):
                self["smenu_t%d" % i].instance.move(ePoint(3500, 0))
        except Exception:
            pass


    def _show_repeat_panel(self):

        self._repeat_active = True
        self._repeat_sel = 0

        try:
            self["smenu_hdr"].setText("  Repeat")
        except Exception:
            pass

        self._repeat_refresh()

    def _hide_repeat_panel(self):

        self._repeat_active = False
        try:
            self["smenu_hdr"].setText("  Manual Recording")
        except Exception:
            pass
        self._custom_refresh()

    def _repeat_refresh(self):

        x  = self.SMENU_X + 20
        y0 = self.SMENU_ITEM_Y0
        ih = self.SMENU_ITEM_H

        rows = []

        for i, day in enumerate(self._DAY_NAMES):

            mark = u"\u2713" if self._repeat_days[i] else u"\u2610"
            rows.append("%s %s" % (mark, day))

        rows.append("[ Done ]")
        while len(rows) < SMENU_ROWS:
            rows.append("")

        for i in range(SMENU_ROWS):

            try:

                if i < len(rows):

                    self["smenu_t%d" % i].setText("  " + rows[i])
                    self["smenu_t%d" % i].instance.move(
                        ePoint(x, y0 + i * ih)
                    )

                    colour = gRGB(0x000000) if i == self._repeat_sel else gRGB(0xffffff)

                    if i == 7:
                        colour = gRGB(0x44ff44) if i != self._repeat_sel else gRGB(0x000000)

                    self["smenu_t%d" % i].instance.setForegroundColor(colour)

                else:

                    self["smenu_t%d" % i].instance.move(ePoint(3500, 0))

            except Exception:
                pass

        try:

            self["smenu_sel"].instance.move(
                ePoint(x - 5, y0 + self._repeat_sel * ih)
            )

        except Exception:
            pass


    def _repeat_ok(self):

        if self._repeat_sel == 7:

            # Done: back to Manual Recording with the cursor on
            # [ Record ], so the next OK actually sets it up.
            self._custom_sel = 4
            self._hide_repeat_panel()
            return

        self._repeat_days[self._repeat_sel] = \
            not self._repeat_days[self._repeat_sel]

        self._repeat_refresh()


    def _custom_refresh(self):
        x  = self.SMENU_X + 20
        y0 = self.SMENU_ITEM_Y0
        ih = self.SMENU_ITEM_H
        rows = [
            "Date    %s" % time.strftime("%a %d %b", time.localtime(self._custom_start)),
            "Start   %s" % time.strftime("%H:%M", time.localtime(self._custom_start)),
            "End     %s" % time.strftime("%H:%M", time.localtime(self._custom_end)),
            "Repeat  %s" % self._repeat_summary(),
            "[ Record ]",
        ]

        for i in range(SMENU_ROWS):
            try:
                self["smenu_t%d" % i].instance.move(ePoint(3500, 0))
            except Exception:
                pass

        for i, label in enumerate(rows):
            y = y0 + i * ih
            try:
                self["smenu_t%d" % i].setText("  " + label)
                self["smenu_t%d" % i].instance.move(ePoint(x, y))
                col = gRGB(0x000000) if i == self._custom_sel else gRGB(0xffffff)
                self["smenu_t%d" % i].instance.setForegroundColor(col)
            except Exception:
                pass
        try:
            hy = y0 + self._custom_sel * ih
            self["smenu_sel"].instance.move(ePoint(x - 5, hy))
        except Exception:
            pass

    def _custom_change(self, direction):
        """Left/right adjusts the currently selected field. Same
        increments as ChannelBrowser's identical panel in player.py —
        that pair should always behave identically."""
        sel = self._custom_sel
        if sel == 0:   # Date ± 1 day (moves both start and end together)
            self._custom_start += direction * 86400
            self._custom_end   += direction * 86400
        elif sel == 1: # Start ± 15 mins
            self._custom_start += direction * 900
        elif sel == 2: # End ± 15 mins
            self._custom_end += direction * 900
        self._custom_refresh()

    def _custom_ok(self):
        """OK in the Manual Recording panel: on the Repeat row it opens
        the day picker; on any other row it schedules the recording."""
        if self._custom_sel == 3:
            self._show_repeat_panel()
            return
        self._custom_schedule()

    _SHORT_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    def _repeat_phrase(self, days):
        if all(days):
            return "every day"
        if days == [False, True, True, True, True, True, False]:
            return "Monday to Friday"
        names = [n for n, on in zip(self._DAY_NAMES, days) if on]
        return "every " + (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0])

    def _repeat_summary(self):
        days = getattr(self, "_repeat_days", [False] * 7)
        if not any(days):
            return "Never  (OK to choose days)"
        if all(days):
            return "Every day"
        if days == [False, True, True, True, True, True, False]:
            return "Mon - Fri"
        return " ".join(d for d, on in zip(self._SHORT_DAYS, days) if on)

    def _custom_schedule(self):
        """Schedule the manual recording and close the panel. Uses the
        same create_rule/add_rule pipeline as every other recording —
        NOT a direct _create_timer_direct call (which is what this
        used before being reunified with player.py's identical panel)
        — that path never persisted a rule to the rule store at all,
        so it couldn't survive a restart the way every other recording
        in this app can and must."""
        if self._custom_end <= self._custom_start:
            # End before start makes no sense to schedule — just
            # ignore the press rather than silently create a
            # zero/negative-length rule.
            return
        if not state.channels or not state.open_prov:
            return

        ch = state.channels[self._focused]

        manager = getattr(state, 'recording_manager', None)
        if manager is None:
            self._hide_custom()
            return

        repeat = list(getattr(self, "_repeat_days", [False] * 7))
        title = getattr(self, "_custom_title", None) or ch.name

        custom_ev = Epg()
        custom_ev.stream_id   = ch.stream_id
        custom_ev.title       = title
        custom_ev.description = ""
        custom_ev.start       = self._custom_start
        custom_ev.end         = self._custom_end

        # A bouquet channel is recorded under its own provider.
        rec_prov = getattr(ch, "_bq_provider", None) or state.open_prov

        # A repeat recording gets the Settings recording offsets (start
        # earlier / end later, in case the programme moves a little);
        # a one-off Manual Recording records exactly the times set.
        if any(repeat):
            pre = int(getattr(state, "rec_start_offset", 0) or 0)
            post = int(getattr(state, "rec_end_offset", 0) or 0)
        else:
            pre = post = 0
        rule = manager.create_rule(
            rec_prov, ch, custom_ev,
            pre, post, repeat, title, state.recording_path
        )
        rule.trigger = TriggerType.MANUAL

        added = manager.add_rule(rule)

        self._hide_custom()
        self._b5_refresh()
        self._update_info()

        try:
            with open(LOG("e2iptv_rules.log"), "a") as _f:
                _f.write("%s GRID RULE type=%s days=%s start=%s dur=%s prov=%s sid=%s added=%s\n" % (
                    time.strftime("%d/%m %H:%M:%S"), rule.rule_type, rule.days,
                    rule.start_time, rule.duration, rule.provider_id,
                    rule.stream_id, added))
        except Exception:
            pass

        if any(repeat) and not added:
            from Screens.MessageBox import MessageBox
            self.session.open(
                _E2iBox,
                "This repeat recording is already set up.",
                MessageBox.TYPE_INFO, timeout=6)
        if any(repeat) and added:
            from Screens.MessageBox import MessageBox
            self.session.open(
                _E2iBox,
                "%s will record %s, %s - %s, on %s.\n\nTo stop it, press OK "
                "on any of its programmes in the grid and choose Remove "
                "Repeat Recording." % (
                    title, self._repeat_phrase(repeat),
                    time.strftime("%H:%M", time.localtime(self._custom_start - pre * 60)),
                    time.strftime("%H:%M", time.localtime(self._custom_end + post * 60)),
                    ch.name),
                MessageBox.TYPE_INFO, timeout=8)

    # -----------------------------------------------------------------
    # Move Channel (OK menu) — reorders this category and saves it to
    # the category's channel order file (backend/channel_order.py), the
    # same file that can be edited on a computer.
    # -----------------------------------------------------------------

    def _start_move(self):
        if not state.channels:
            return
        self._move_active = True
        self._move_orig = list(state.channels)
        self._move_orig_focus = self._focused
        self._moving_stream_id = str(state.channels[self._focused].stream_id)
        self._b3_refresh()
        self._b5_refresh()
        self._update_info()

    def _move_step(self, delta):
        i = self._focused
        j = i + delta
        if not (0 <= j < len(state.channels)):
            return
        chans = state.channels
        chans[i], chans[j] = chans[j], chans[i]
        self._focused = j
        self._clamp_scroll()
        self._b3_refresh()
        self._b5_refresh()
        self._update_info()
        self._fetch_visible_epg()

    def _finish_move(self, save):
        if save:
            try:
                if getattr(state.open_prov, "is_bouquet", False):
                    # My Bouquets: the bouquet file itself holds the order.
                    from .backend.bouquets import reorder as _reorder_bouquet
                    _reorder_bouquet(getattr(state.sel_cat, "category_id", ""),
                                     state.channels)
                else:
                    from .backend.channel_order import save as _save_order
                    _save_order(state.open_prov, state.sel_cat, state.channels)
            except Exception as e:
                print("[E2iPTV] Move Channel: could not save order:", e)
        else:
            state.channels[:] = self._move_orig
            self._focused = self._move_orig_focus
        self._move_active = False
        self._moving_stream_id = None
        self._move_orig = None
        self._clamp_scroll()
        self._save_state()
        self._b3_refresh()
        self._b5_refresh()
        self._update_info()
        self._fetch_visible_epg()

    # -----------------------------------------------------------------
    # Hide Channel (OK menu) — marks the channel hidden in the category's
    # order file. It's shown again by removing the - from its line there.
    # -----------------------------------------------------------------

    # -----------------------------------------------------------------
    # My Bouquets (backend/bouquets.py) — Copy to Bouquet / Remove
    # -----------------------------------------------------------------

    _NEW_BOUQUET = "__new_bouquet__"

    def _bouquet_choose(self, channel):
        from .side_menu import SideMenu
        from .backend.bouquets import list_bouquets
        self._bq_channel = channel
        choices = [(name, name) for name in list_bouquets()]
        choices.append(("New bouquet...", self._NEW_BOUQUET))
        self.session.openWithCallback(
            self._bouquet_chosen, SideMenu,
            "Copy to which bouquet?", choices, detail=channel.name)

    def _bouquet_chosen(self, choice):
        if not choice:
            return
        name = choice[1]
        if name == self._NEW_BOUQUET:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            self.session.openWithCallback(
                self._bouquet_named, VirtualKeyBoard,
                title="Name for the new bouquet", text="")
            return
        self._bouquet_add(name)

    def _bouquet_named(self, text):
        if not text or not text.strip():
            return
        from .backend.bouquets import create_bouquet
        name = create_bouquet(text.strip())
        if name:
            self._bouquet_add(name)

    def _bouquet_add(self, name):
        from Screens.MessageBox import MessageBox
        from .backend.bouquets import add_channel, ensure_in_state
        channel = getattr(self, "_bq_channel", None)
        if channel is None:
            return
        try:
            added = add_channel(name, state.open_prov, channel)
            ensure_in_state(state)      # first bouquet: show My Bouquets in B2
        except Exception as e:
            print("[E2iPTV] Copy to Bouquet failed:", e)
            added = None
        if added is None:
            msg = "Couldn't copy %s." % channel.name
        elif added:
            msg = "%s copied to %s." % (channel.name, name)
        else:
            msg = "%s is already in %s." % (channel.name, name)
        self.session.open(_E2iBox, msg, MessageBox.TYPE_INFO, timeout=3)

    def _bouquet_confirm_remove(self, channel):
        from Screens.MessageBox import MessageBox
        self._bq_channel = channel
        self.session.openWithCallback(
            self._bouquet_remove_confirmed, _E2iBox,
            "Remove %s from %s?\n\nIt stays in its provider's own lists."
            % (channel.name, getattr(state.sel_cat, "category_name", "this bouquet")),
            MessageBox.TYPE_YESNO, default=False)

    def _bouquet_remove_confirmed(self, answer):
        channel = getattr(self, "_bq_channel", None)
        if not answer or channel is None or channel not in state.channels:
            return
        try:
            from .backend.bouquets import remove_channel
            remove_channel(getattr(state.sel_cat, "category_id", ""), channel)
        except Exception as e:
            print("[E2iPTV] Remove from Bouquet failed:", e)
            return
        self._drop_from_list(channel)

    def _bouquet_confirm_delete(self):
        from Screens.MessageBox import MessageBox
        name = getattr(state.sel_cat, "category_name", "")
        self.session.openWithCallback(
            self._bouquet_delete_confirmed, _E2iBox,
            "Delete the bouquet %s?\n\nIts channels stay in their providers' "
            "own lists." % name,
            MessageBox.TYPE_YESNO, default=False)

    def _bouquet_delete_confirmed(self, answer):
        if not answer:
            return
        try:
            from .backend.bouquets import delete_bouquet
            delete_bouquet(getattr(state.sel_cat, "category_id", ""))
        except Exception as e:
            print("[E2iPTV] Delete Bouquet failed:", e)
            return
        # Nothing left to show here — back to Live TV, which drops the
        # bouquet from B2 (see screen1._refresh_b2_after_settings).
        state.channels = []
        state.sel_cat = None
        state.b5_focused = 0
        state.b5_scroll = 0
        self._refresh_timer.stop()
        self.close(True)

    def _drop_from_list(self, channel):
        idx = state.channels.index(channel)
        del state.channels[idx]
        self._focused = min(idx, len(state.channels) - 1) if state.channels else 0
        self._clamp_scroll()
        self._save_state()
        self._b3_refresh()
        self._b5_refresh()
        self._update_info()
        self._fetch_visible_epg()

    # -----------------------------------------------------------------
    # Show Hidden Channels (OK menu) — pick a hidden channel to show it
    # again; it goes back to its old place in the category.
    # -----------------------------------------------------------------

    _SHOW_ALL = "__show_all__"

    def _show_hidden_choice(self):
        from .side_menu import SideMenu
        rows = self._hidden_list()
        if not rows:
            return
        choices = [(name or cid, cid) for cid, name in rows]
        if len(rows) > 1:
            choices.append(("Show all %d" % len(rows), self._SHOW_ALL))
        self.session.openWithCallback(
            self._show_hidden_chosen, SideMenu,
            "Show hidden channel", choices,
            detail="%d hidden in this category" % len(rows))

    def _show_hidden_chosen(self, choice):
        if not choice:
            return
        rows = self._hidden_list()
        ids = [cid for cid, _n in rows] if choice[1] == self._SHOW_ALL else [choice[1]]
        try:
            from .backend.channel_order import unhide
            unhide(state.open_prov, state.sel_cat, ids)
        except Exception as e:
            print("[E2iPTV] Show Hidden Channels failed:", e)
            return
        self._hidden_cache = None
        # Reload this category's list (from memory) so the order file —
        # now with those channels shown — is applied again.
        try:
            chans = state.backend.get_live_channels(state.open_prov, state.sel_cat)
        except Exception:
            chans = None
        if chans:
            state.channels = list(chans)
            self._focused = next(
                (i for i, c in enumerate(state.channels) if str(c.stream_id) == ids[0]),
                min(self._focused, len(state.channels) - 1))
        self._clamp_scroll()
        self._save_state()
        self._b3_refresh()
        self._b5_refresh()
        self._update_info()
        self._fetch_visible_epg()

    def _confirm_hide(self, channel):
        from Screens.MessageBox import MessageBox
        self._hide_target = channel
        self.session.openWithCallback(
            self._hide_confirmed, _E2iBox,
            "Hide %s?\n\nTo show it again, use Show Hidden Channels in this "
            "menu (or remove the - from its line in the order file)."
            % channel.name,
            MessageBox.TYPE_YESNO, default=False)

    def _hide_confirmed(self, answer):
        channel = getattr(self, "_hide_target", None)
        self._hide_target = None
        if not answer or channel is None or channel not in state.channels:
            return
        try:
            from .backend.channel_order import hide as _hide_channel
            _hide_channel(state.open_prov, state.sel_cat, channel)
            self._hidden_cache = None
        except Exception as e:
            print("[E2iPTV] Hide Channel failed:", e)
            return
        idx = state.channels.index(channel)
        del state.channels[idx]
        if not state.channels:
            self._focused = 0
        else:
            self._focused = min(idx, len(state.channels) - 1)
        self._clamp_scroll()
        self._save_state()
        self._b3_refresh()
        self._b5_refresh()
        self._update_info()
        self._fetch_visible_epg()

    def _clamp_scroll(self):
        n = len(state.channels)
        self._scroll = min(
            max(0, self._focused - 4),
            max(0, n - B3_VISIBLE)
        )

    def _save_state(self):
        state.b5_focused = self._focused
        state.b5_scroll  = self._scroll

    def _ensure_cursor_visible(self):
        if self._cursor_time < self._window_start:
            self._window_start = self._cursor_time
        elif self._cursor_time >= self._window_start + EPG_WINDOW:
            self._window_start = self._cursor_time - EPG_WINDOW + EPG_STEP

    def _find_event(self, events, when):
        for ev in events:
            if ev.start <= when < ev.end:
                return ev
        return None

    def _get_events(self, channel, ws=None, we=None):
        """Return real EPG events padded with 30-min synthetic tiles for any gaps."""
        events = state.backend.epg_store.get_for(channel)

        # No window constraint — return raw events for navigation
        if ws is None or we is None:
            return events if events else []

        in_window = [e for e in events if e.end > ws and e.start < we]

        def _dummy(t_start, t_end):
            e             = Epg()
            e.stream_id   = channel.stream_id
            e.title       = "No EPG Available"
            e.description = ""
            e.start       = t_start
            e.end         = t_end
            return e

        def _fill_gap(gap_start, gap_end, out):
            t = gap_start
            while t < gap_end:
                next_b   = ((t // 1800) + 1) * 1800
                slot_end = min(next_b, gap_end)
                out.append(_dummy(t, slot_end))
                t = slot_end

        if not in_window:
            result = []
            _fill_gap(ws, we, result)
            return result

        real   = sorted(in_window, key=lambda e: e.start)
        result = []
        last_end = ws
        for ev in real:
            if ev.start > last_end + 60:
                _fill_gap(last_end, ev.start, result)
            result.append(ev)
            last_end = max(last_end, ev.end)
        if last_end < we:
            _fill_gap(last_end, we, result)
        return result

    def _clean_events(self, events):
        """Sort events and discard any that overlap with the previous one.
        Handles bad provider data where duplicate/overlapping entries exist."""
        cleaned  = []
        last_end = 0
        for ev in sorted(events, key=lambda e: e.start):
            if ev.start >= last_end:
                cleaned.append(ev)
                last_end = ev.end
        return cleaned

    # ------------------------------------------------------------------
    # EPG fetch
    # ------------------------------------------------------------------

    def _fetch_visible_epg(self):
        if not state.open_prov or not state.channels:
            return
        vis_start = self._scroll
        vis_end   = min(len(state.channels), vis_start + B3_VISIBLE)
        for ch in state.channels[vis_start:vis_end]:
            # See screen1.py's _prefetch_epg for why "already
            # prefetched" alone isn't trusted — only counts if that
            # data is actually still there right now.
            if ch.stream_id in state.epg_prefetched:
                if state.backend.epg_store.has_for(ch):
                    continue
                state.epg_prefetched.discard(ch.stream_id)
            state.epg_prefetched.add(ch.stream_id)
            reactor.callInThread(
                self._fetch_epg_thread, state.open_prov, ch)

    def _fetch_epg_thread(self, provider, channel):
        try:
            state.backend.get_epg(provider, channel)
        except Exception:
            pass
        reactor.callFromThread(self._apply_epg)

    def _apply_epg(self):
        # Debounce — only refresh once visible channels all have data or after 500ms
        vis_end = min(len(state.channels), self._scroll + B3_VISIBLE)
        all_ready = all(
            state.backend.epg_store.has_for(state.channels[i])
            for i in range(self._scroll, vis_end)
        ) if state.channels else True
        if all_ready:
            self._epg_debounce.stop()
            self._b5_refresh()
            self._update_info()
        else:
            self._epg_debounce.stop()
            self._epg_debounce.start(500, True)

    def _epg_debounce_fired(self):
        self._b5_refresh()
        self._update_info()

    def _refresh_tick(self):
        """Periodic refresh while Screen3 is open."""

        now = int(time.time())

        slot = now // 30

        refresh_all = False

        if slot != self._last_now_slot:
            self._last_now_slot = slot
            refresh_all = True

        if state.channels:

            try:
                ch = state.channels[self._focused]
                ev = self._find_event(
                    self._get_events(ch),
                    self._cursor_time
                )

                event_id = (
                    ev.start,
                    ev.end,
                    ev.title
                ) if ev else None

                if event_id != self._last_event_id:
                    self._last_event_id = event_id
                    refresh_all = True

            except Exception:
                refresh_all = True

        if refresh_all:
            self._refresh_time_bar()
            self._b5_refresh()
            self._update_info()

        self._refresh_timer.start(30000, True)

    # ------------------------------------------------------------------
    # Time bar
    # ------------------------------------------------------------------

    def _refresh_time_bar(self):
        ws = self._window_start
        we = ws + EPG_WINDOW
        self["b5_date"].setText(
            time.strftime("%a %d %b  %H:%M", time.localtime(ws)) +
            " - " +
            time.strftime("%H:%M", time.localtime(we))
        )
        for i in range(4):
            t = ws + i * EPG_STEP
            self["b5_mark%d" % i].setText(
                time.strftime("%H:%M", time.localtime(t))
            )

    # ------------------------------------------------------------------
    # B3 — channel list with picons
    # ------------------------------------------------------------------

    def _b3_refresh(self):
        channels = state.channels
        vis_start, vis_end = self._b3_window(
            len(channels), self._scroll, B3_VISIBLE
        )

        entries = self._b3_build_entries(
            channels, vis_start, vis_end, self._focused,
            item_w=B3_W, item_h=ITEM_H,
            focus_colour=B3_TILE_INT, base_colour=BG_INT,
        )

        self["b3_list"].setList(entries)
        self["b3_list"].moveToIndex(self._focused - self._scroll)

    # ------------------------------------------------------------------
    # B5 — EPG tile grid
    # All channels share the same window_start / cursor_time
    # ------------------------------------------------------------------

    def _b5_refresh(self):

        try:
            with open(LOG("e2iptv_match.log"), "a") as f:
                f.write("B5_REFRESH\n")
        except Exception:
            pass

        channels = state.channels

        if not channels:
            self["b5_list"].setList([])
            return

        ws     = self._window_start
        we     = ws + EPG_WINDOW
        now    = int(time.time())
        cursor = self._cursor_time
        pps    = float(B5_W) / EPG_WINDOW

        vis_start = self._scroll
        vis_end   = min(len(channels), vis_start + B3_VISIBLE)
        entries   = []

        for idx, ch in enumerate(channels[vis_start:vis_end]):
            abs_idx    = vis_start + idx
            is_focused = abs_idx == self._focused
            events     = self._get_events(ch, ws, we)

            entry = [None]

            # Row background — dark, forms separator between tiles
            entry.append(MultiContentEntryText(
                pos=(0, 0), size=(B5_W, ITEM_H),
                font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                text="", color=0xffffff, color_sel=0xffffff,
                backcolor=BG_INT, backcolor_sel=BG_INT))

            cursor_found = False
            last_x2      = 0   # track right edge to prevent visual tile overlap

            for ev in sorted(events, key=lambda e: e.start):
                # Skip events outside window
                if ev.end <= ws or ev.start >= we:
                    continue

                x1 = max(last_x2, max(0,    int((ev.start - ws) * pps)))
                x2 = min(B5_W,              int((ev.end   - ws) * pps))
                w  = x2 - x1
                if w < 6:
                    last_x2 = max(last_x2, x2)
                    continue

                last_x2 = x2

                is_cursor   = is_focused and not cursor_found and ev.start <= cursor < ev.end
                is_historic = ev.end <= now

                # Recording state for THIS EPG tile only.
                # Scheduled = green, active = red.
                # Once the recording finishes it is removed from
                # active_recordings, so the tile returns to its normal colour.
                rec_colour = _epg_recording_colour(
                    state, ch.stream_id, ev.start, ev.end, channel=ch
                )

                # Recording colour has priority over focus and historical
                # state. While a recording is scheduled/active the tile must
                # remain green/red even when focused. Once the recording entry
                # disappears from active_recordings, normal historical/focus
                # colouring resumes automatically.
                if rec_colour:
                    tile_bg = rec_colour
                    txt_col = 0xffffff
                    if is_cursor:
                        cursor_found = True
                elif is_cursor:
                    cursor_found = True
                    tile_bg = CURSOR_INT
                    txt_col = 0x000000
                elif is_historic:
                    tile_bg = HIST_INT
                    txt_col = 0xffffff
                else:
                    tile_bg = TILE_INT
                    txt_col = 0xffffff

                # Tile background
                entry.append(MultiContentEntryText(
                    pos=(x1 + 2, 2), size=(w - 4, ITEM_H - 4),
                    font=1,
                    flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text="",
                    color=txt_col,
                    color_sel=txt_col,
                    backcolor=tile_bg,
                    backcolor_sel=tile_bg,
                    cornerRadius=12))

                if w > 10:

                    entry.append(MultiContentEntryText(
                        pos=(x1 + 24, 4),
                        size=(max(1, w - 40), ITEM_H - 8),
                        font=1,
                        flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                        text=ev.title,
                        color=txt_col,
                        color_sel=txt_col,
                        backcolor=tile_bg,
                        backcolor_sel=tile_bg))

            # Now line — drawn last, always on top
            now_x = int((now - ws) * pps)
            if 0 <= now_x < B5_W:
                entry.append(MultiContentEntryText(
                    pos=(now_x, 0), size=(3, ITEM_H),
                    font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text="", color=NOW_INT, color_sel=NOW_INT,
                    backcolor=NOW_INT, backcolor_sel=NOW_INT))

            entries.append(entry)

        # Set list — never call moveToIndex on B5
        self["b5_list"].setList(entries)

    # ------------------------------------------------------------------
    # Info panel
    # ------------------------------------------------------------------

    def _update_info(self):
        if not state.channels:
            return
        if getattr(self, "_move_active", False):
            try:
                self["info_title"].setText(
                    "Moving %s:  Up/Down to move,  OK to save,  Exit to cancel"
                    % state.channels[self._focused].name)
            except Exception:
                pass
            return
        if not (0 <= self._focused < len(state.channels)):
            # state.channels can be replaced with a shorter list at any
            # time (switching provider/category elsewhere) — this
            # screen's own _focused index isn't automatically clamped
            # to match, and the periodic 30s refresh timer calls this
            # independent of any user action that would normally catch
            # it, so a stale, now out-of-range index could sit here
            # until the timer's next tick crashed on it. Clamping here
            # is enough to stop the crash; _on_shown already handles
            # properly re-syncing this from state.b5_focused whenever
            # the screen becomes visible again.
            self._focused = max(0, min(self._focused, len(state.channels) - 1))
        ch     = state.channels[self._focused]
        events = self._get_events(ch)
        ev     = self._find_event(events, self._cursor_time)

        if ev:
            dur   = max(0, (ev.end - ev.start) // 60)
            start = time.strftime("%H:%M", time.localtime(ev.start))
            end   = time.strftime("%H:%M", time.localtime(ev.end))
            self["info_title"].setText(ev.title)
            # Check if THIS specific event is recording \u2014 scoped to
            # event.start, not just provider+channel (see the matching
            # tile-badge fix above for why the loose get_active_recording()
            # match is wrong for a per-event display like this one).
            indicator = ""
            colour = 0xffffff

            # Bouquet channels are recorded under their own provider.
            _rec_prov = getattr(ch, "_bq_provider", None) or state.open_prov
            if _rec_prov:

                for _rk, _rec in state.active_recordings.items():

                    if (_rec.get("provider_id") == _rec_prov.provider_id
                            and _rec.get("stream_id") == ch.stream_id
                            and _rec.get("event_start") == ev.start):

                        if _rec.get("status") == "active":
                            indicator = "   \u25cf\u25cf\u25cf REC"
                            colour = REC_REC_INT        # Red = recording
                        elif _rec.get("status") == "scheduled":
                            indicator = "   \u25cf\u25cf\u25cf SCHEDULED"
                            colour = REC_WAIT_INT        # Green = scheduled
                        break


            self["info_times"].setText(
                "%s - %s  %d min%s" %
                (
                    start,
                    end,
                    dur,
                    indicator
                )
            )

            try:

                from enigma import gRGB as _g

                self["info_times"].instance.setForegroundColor(
                    _g(colour)
                )

            except Exception:
                pass

            self["info_desc"].setText(ev.description)
        else:
            self["info_title"].setText(ch.name)
            self["info_times"].setText("")
            self["info_desc"].setText("")

        prov_name = state.open_prov.name if state.open_prov else ""
        cat_name  = (
            state.categories[state.sel_cat_idx][1].get("name", "")
            if state.categories else ""
        )
        self["info_prov"].setText(
            "%s  \xb7  %s" % (prov_name, cat_name))
