# =============================================================================
# E2iPTV
# Shared Application State
# Instantiated once, imported by all screens.
# =============================================================================

from .paths import HDD, LOG  # data folder / debug logs
from .paths import DATA  # E2iPTV_Data folder
from .backend.backend import Backend
from .backend.playlist_reader import load_providers
from .recording.manager import RecordingManager


def _nav_debug(msg):
    """Temporary diagnostic — traces which key-handling methods fire,
    in what order, with what state, across screen1.py/screen2.py's
    Live TV <-back navigation. Writes to a plain file, same safe
    pattern as every other debug log this session: nothing about the
    box touched to read it back, just `cat
    /tmp/e2iptv_livetv_nav_debug.log`."""
    try:
        import time as _time
        with open(LOG("e2iptv_livetv_nav_debug.log"), "a") as f:
            f.write("%s %s\n" % (_time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _epg_recording_colour(app_state, stream_id, event_start, event_end=None,
                          channel=None):
    provider = getattr(app_state, "open_prov", None)
    # My Bouquets: a bouquet channel is recorded under its OWN provider,
    # so match recordings against that, not the "My Bouquets" entry.
    real = getattr(channel, "_bq_provider", None)
    if real is not None:
        provider = real
    if provider is None:
        return None
    provider_id = getattr(provider, "provider_id", None)

    # Channel-scoped check for a manually-scheduled recording FIRST —
    # same reasoning as get_ctx_items/execute_ctx_action in player.py:
    # a manually-entered start is rarely any EPG-aligned time, so it
    # won't reliably exactly-match event_start for whatever tile is
    # actually being coloured (real or synthetic). Channel alone isn't
    # enough though — also requires the tile's own time to actually
    # overlap the recording's, or every tile on the channel would show
    # as recording, not just the one actually being recorded.
    import time as _t
    _now = int(_t.time())
    for _rk, _rec in getattr(app_state, "active_recordings", {}).items():
        if not (_rec.get("provider_id") == provider_id
                and str(_rec.get("stream_id")) == str(stream_id)
                and _rec.get("trigger") == "manual"
                and _rec.get("status") in ("scheduled", "active")):
            continue
        rec_start = _rec.get("event_start", 0)
        rec_end   = _rec.get("event_end", 0)
        overlaps = (
            event_start < rec_end and event_end > rec_start
            if event_end is not None
            else rec_start <= event_start < rec_end
        ) if event_start is not None else (rec_start <= _now < rec_end)
        if overlaps:
            if _rec.get("status") == "active":
                return 0xff4444
            return 0x44cc44

    for _rk, _rec in getattr(app_state, "active_recordings", {}).items():
        if (_rec.get("provider_id") == provider_id
                and str(_rec.get("stream_id")) == str(stream_id)
                and _rec.get("event_start") == event_start):
            if _rec.get("status") == "active":
                return 0xff4444
            if _rec.get("status") == "scheduled":
                return 0x44cc44
            break

    manager = getattr(app_state, "recording_manager", None)
    if manager is not None:
        for _rule in manager.rule_store.enabled():
            if (_rule.provider_id == provider_id
                    and str(_rule.stream_id) == str(stream_id)
                    and _rule.event_start == event_start
                    and (event_end is None or _rule.event_end == event_end)
                    and _rule.last_run != _rule.event_start):
                # Defensive guard, independent of last_run: a rule
                # whose event has already ended has nothing left to
                # schedule, whatever state last_run is actually in —
                # green here means "will record in future", which
                # can't be true of a past event. Catches exactly the
                # case that was actually reported: a completed
                # recording's own active_recordings entry is already
                # cleaned up (both checks above correctly find
                # nothing), but the underlying rule's last_run was
                # never set (e.g. a recording that ran before the fix
                # ensuring _create_timer_direct's own thread sets it
                # on genuine completion — see that fix's own comment),
                # so this fallback alone kept the tile green
                # indefinitely with nothing else to ever clear it.
                _rule_end = _rule.event_end if event_end is None else event_end
                if _rule_end is not None and _rule_end <= _now:
                    continue
                return 0x44cc44

        # Repeating rules (Manual Recording -> Repeat): green on every
        # future programme one of its occurrences covers.
        if event_start is not None:
            try:
                from .recording.scheduler import occurrences, core_window
                _ev_end = event_end if event_end is not None else event_start + 60
                for _rule in manager.rule_store.enabled():
                    if (getattr(_rule, "rule_type", "") != "custom"
                            or _rule.provider_id != provider_id
                            or str(_rule.stream_id) != str(stream_id)):
                        continue
                    for _b, _e in occurrences(_rule, event_start - 86400, _ev_end):
                        # Times actually chosen (offsets left out), so the
                        # programmes either side don't turn green too.
                        _b, _e = core_window(_rule, _b, _e)
                        if _b < _ev_end and _e > event_start and _e > _now:
                            # Red while that occurrence is actually recording.
                            if _b <= _now:
                                for _rec in getattr(app_state, "active_recordings", {}).values():
                                    if (_rec.get("provider_id") == provider_id
                                            and str(_rec.get("stream_id")) == str(stream_id)
                                            and _rec.get("status") == "active"):
                                        return 0xff4444
                            return 0x44cc44
            except Exception as _e:
                global _REPEAT_ERR_LOGGED
                if not _REPEAT_ERR_LOGGED:
                    _REPEAT_ERR_LOGGED = True
                    try:
                        from .paths import LOG as _LOG
                        import traceback as _tb
                        with open(_LOG("e2iptv_rules.log"), "a") as _f:
                            _f.write("REPEAT MARKER ERROR: %s\n%s\n"
                                     % (_e, _tb.format_exc()))
                    except Exception:
                        pass

    return None


_REPEAT_ERR_LOGGED = False



class AppState(object):

    def __init__(self):

        # Backend — single instance, keeps internal stores warm
        self.backend        = Backend()

        # Recording framework
        self.recording_manager = RecordingManager()
        self.recording_manager.start()

        # Providers — loaded once on first open
        self.providers      = []       # list of (name, Provider)

        # Current B2 position
        self.open_prov_idx  = None     # which provider is expanded
        self.open_prov      = None     # the Provider object

        # Current category selection
        self.categories     = []       # list of (label, data) as used by B2
        self.sel_cat_idx    = 0

        # Current channel list — populated when a category is selected
        self.channels       = []       # list of LiveChannel objects

        # B2 display state — shared so screen2 can mirror it
        # Load persisted settings
        import json, os
        _settings_path = DATA + '/cache/settings.json'
        _settings = {}
        try:
            if os.path.exists(_settings_path):
                _settings = json.load(open(_settings_path))
        except: pass
        self.recording_path = _settings.get('recording_path', HDD + '/movie/')
        # Default on a fresh install — always present on every box,
        # unlike /hdd which may not exist or be mounted on every
        # image/slot. The user can always repoint this via Settings ->
        # Other -> Playlists (see screen2.py's own _other_open_browser),
        # same as recording_path above — this is only ever the
        # starting value before that's ever been touched.
        self.playlists_path       = "/etc/enigma2/E2iPTV/playlists.txt"
        self.rec_start_offset     = 0   # mins before scheduled start
        self.rec_end_offset       = 0   # mins after scheduled end
        self.catchup_start_offset = 0   # mins before catchup start
        self.catchup_end_offset   = 0   # mins after catchup end
        # key=(stream_id, ev_start) value={"status":"active"|"scheduled","end":ts}
        self.active_recordings    = {}
        self.b2_sel         = 0
        # 1 = "Live TV" in B1_LABELS (b1common.py). This is only the
        # in-memory fallback for the lifetime of this process; plugin.py
        # overwrites it with the persisted last choice (see
        # b1common.load_b1_last_choice()) before the first screen opens.
        self.b1_sel         = 1

        # Series B2 navigation memory.
        # These are session state values so Series can reopen on the
        # provider/category last used during the current plugin session.
        self.series_provider_idx = None
        self.series_category_id  = None

        # EPG / Screen 3 state
        self.b5_focused      = 0
        self.last_ch_idx     = None
        self.prev_ch_idx     = None
        self.last_ch_idx     = None
        self.prev_ch_idx     = None
        self.b5_scroll       = 0
        self.b5_window_start = 0
        self.b5_cursor_time  = 0
        self.b5_historical   = False
        self.epg_prefetched  = set()   # stream_ids fetched or in-flight globally

    def load_providers(self):

        #
        # Load the backend provider store.
        #

        # Apply the saved playlists path before the initial provider load.
        # The /etc/enigma2/E2iPTV/playlists.txt value above remains the default.
        try:
            import json
            with open(DATA + "/cache/app_settings.json", "r") as _f:
                _app = json.load(_f)
            if "playlists_path" in _app:
                self.playlists_path = _app["playlists_path"]
        except Exception:
            pass

        # Fresh install: create the playlist from the example (no working
        # providers in it), and never fail to open over a missing or
        # unreadable playlist — an empty provider list is shown instead.
        try:
            from .paths import ensure_playlist
            self.playlist_created = ensure_playlist(self.playlists_path)
        except Exception:
            self.playlist_created = False
        try:
            self.backend.load_providers(self.playlists_path)
        except Exception as e:
            print("[E2iPTV] could not read playlist %s: %s" % (self.playlists_path, e))
            self.playlist_error = str(e)

        #
        # Mirror the backend provider objects into the UI state.
        #

        raw = self.backend.providers()

        self.providers = [
            (p.name, p)
            for p in raw
        ]

        # "My Bouquets" (backend/bouquets.py), once a bouquet exists.
        try:
            from .backend.bouquets import ensure_in_state
            ensure_in_state(self)
        except Exception:
            pass

        return self.providers


# Single shared instance — import this, not the class
state = AppState()
