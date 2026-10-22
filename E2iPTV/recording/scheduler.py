# =============================================================================
# E2iPTV
# Recording Scheduler
# =============================================================================

from ..paths import LOG  # data folder / debug logs
import time

from enigma import eTimer

from .recording import Recording, RecordingMode

def _slow_note(what, t0):
    """One part of the scheduler's tick took long: say which, in the
    slow log (the whole tick is logged as "recording scheduler")."""
    ms = (time.time() - t0) * 1000
    if ms > 150:
        try:
            from ..paths import LOG
            with open(LOG("e2iptv_slow.log"), "a") as f:
                f.write("%s %-22s %5.0f ms\n" % (time.strftime("%H:%M:%S"), what, ms))
        except Exception:
            pass


def _log(text):

    try:

        with open(
            LOG("e2iptv_scheduler.log"),
            "a"
        ) as f:

            f.write(
                "%d %s\n" % (
                    int(time.time()),
                    text
                )
            )

    except Exception:
        pass

class RecordingScheduler(object):

    def __init__(self, manager):

        self.manager = manager

        self.timer = eTimer()

        self.timer.callback.append(
            self._tick
        )

        self.running = False

    # -------------------------------------------------------------------------

    def start(self):

        if self.running:
            return

        self.running = True

        self.timer.startLongTimer(60)

    # -------------------------------------------------------------------------

    def stop(self):

        self.running = False

        self.timer.stop()

    # -------------------------------------------------------------------------

    def _tick(self):

        if not self.running:
            return

        t0 = time.time()
        try:

            self.evaluate()

        finally:

            # Runs on the box's main thread: anything slow here shows as a
            # spinner and holds up key presses - note it in the slow log.
            ms = (time.time() - t0) * 1000
            if ms > 150:
                try:
                    from ..paths import LOG
                    with open(LOG("e2iptv_slow.log"), "a") as f:
                        f.write("%s %-22s %5.0f ms\n" % (
                            time.strftime("%H:%M:%S"), "recording scheduler", ms))
                except Exception:
                    pass
            self.timer.startLongTimer(60)

    # -------------------------------------------------------------------------

    def evaluate(self):

        now = int(time.time())

        # Rules follow their provider if playlists.txt was reordered.
        t0 = time.time()
        try:
            self.manager.sync_rule_providers()
        except Exception as e:
            _log("SYNC PROVIDERS FAILED: %s" % e)
        _slow_note("  rules -> providers", t0)

        _log(
            "TICK %d enabled_rules=%d" % (
                now,
                len(self.manager.enabled_rules())
            )
        )

        #
        # Stop recordings that have reached their end time.
        #

        for recording in self.manager.recorder.active():

            if (
                recording.state == "recording" and
                now >= recording.scheduled_end
            ):

                _log(
                    "STOPPING %s" %
                    recording.channel_name
                )

                self.manager.recorder.stop(
                    recording
                )

        #
        # Evaluate recording rules.
        #

        for rule in self.manager.enabled_rules():

            t0 = time.time()
            self.evaluate_rule(
                rule,
                now
            )
            _slow_note("  rule: %s" % (getattr(rule, "name", "") or "?")[:30], t0)
    # -------------------------------------------------------------------------

    def evaluate_rule(self, rule, now):

        if not rule.enabled:

            _log(
                "SKIP disabled %s" %
                rule.name
            )

            return

        if rule.rule_type == "custom":
            self.evaluate_repeat_rule(rule, now)
            return

        if rule.rule_type != "once":

            _log(
                "SKIP unsupported type %s" %
                rule.rule_type
            )

            return

        if rule.last_run == rule.event_start:

            _log(
                "SKIP already executed %s" %
                rule.name
            )

            return

        # event_start/event_end are the EPG event's raw times; the
        # actual trigger/stop times apply pre/post padding on top.
        trigger_time = rule.event_start - (
            getattr(rule, "pre_padding", 0) or 0
        ) * 60

        stop_time = rule.event_end + (
            getattr(rule, "post_padding", 0) or 0
        ) * 60

        if now < trigger_time:

            _log(
                "WAIT %s start=%d now=%d" % (
                    rule.name,
                    trigger_time,
                    now
                )
            )

            return

        _log(
            "DUE %s" %
            rule.name
        )

        #
        # If the immediate-launch thread (spawned when the rule was
        # created — see RecordingManager.add_rule) already has this
        # exact programme active or scheduled, don't start a second,
        # competing download for it here. Match on the rule's own
        # identity fields rather than a rec_key/URL, since the URL
        # isn't known until the provider/channel are resolved.
        #

        if self._already_covered(rule, trigger_time, stop_time):

            _log(
                "SKIP already covered by immediate-launch thread %s" %
                rule.name
            )

            rule.last_run = rule.event_start

            self.manager.save()

            return

        # Defensive guard, independent of the above: if the event's
        # own stop time has already passed — not just "due now", but
        # genuinely over — there's nothing left to legitimately
        # record, whatever the reason _already_covered came back
        # false (an already-completed recording made before this
        # rule's last_run was ever properly set is exactly the
        # scenario this catches — see _epg_recording_colour's own
        # matching guard in state.py for the display-side half of
        # this same gap). Starting a fresh download for a programme
        # that finished airing would just produce an empty/garbage
        # file, not recover anything.
        if now >= stop_time:

            _log(
                "SKIP event already ended, nothing to record %s" %
                rule.name
            )

            rule.last_run = rule.event_start

            self.manager.save()

            return

        try:

            provider, channel, event = \
                self.manager.resolver.resolve(rule)

        except Exception as e:

            import traceback

            _log(
                "RESOLVE EXCEPTION: %s" %
                str(e)
            )

            _log(
                traceback.format_exc()
            )

            return

        if provider is None:

            _log("RESOLVE FAILED provider")

            return

        if channel is None:

            _log("RESOLVE FAILED channel")

            return

        recording = Recording()

        recording.rule_id = rule.rule_id

        recording.provider_id = rule.provider_id

        recording.stream_id = rule.stream_id

        recording.channel_name = rule.channel_name

        recording.event_title = rule.event_title

        recording.scheduled_start = trigger_time

        recording.scheduled_end = stop_time

        recording.mode = RecordingMode.FUTURE

        _log(
            "STARTING %s" %
            rule.name
        )

        #
        # Start the recording.
        #

        ok = self.manager.recorder.start(
            recording,
            None,               # session (temporary)
            provider,
            channel,
            event,
            rule.record_path
        )

        if not ok:

            _log(
                "RECORDER FAILED %s" %
                rule.name
            )

            return

        #
        # Mark the rule as executed only after a successful start.
        #

        rule.last_run = rule.event_start

        self.manager.save()

        _log(
            "STARTED %s" %
            rule.name
        )

    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Repeating rules (Manual Recording -> Repeat: chosen days of the week)
    # -------------------------------------------------------------------------

    def evaluate_repeat_rule(self, rule, now):
        """Start today's (or a still-running overnight) occurrence of a
        repeating rule once its time comes. rule.last_run holds the start
        of the last occurrence started, so each one runs exactly once."""
        occ = current_occurrence(rule, now)
        if occ is None:
            return
        begin, end = occ
        if rule.last_run == begin:
            return

        _log("DUE (repeat) %s %s" % (rule.name, time.strftime("%a %H:%M", time.localtime(begin))))

        if self._already_covered(rule, begin, end):
            # e.g. a one-off recording of the same episode already set
            rule.last_run = begin
            self.manager.save()
            _log("SKIP (repeat) already being recorded %s" % rule.name)
            return

        try:
            provider, channel, _event = self.manager.resolver.resolve(rule)
        except Exception as e:
            _log("RESOLVE EXCEPTION (repeat): %s" % e)
            return
        if provider is None or channel is None:
            _log("RESOLVE FAILED (repeat) %s" % rule.name)
            return

        event = occurrence_event(rule, channel, begin, end)

        recording = Recording()
        recording.rule_id = rule.rule_id
        recording.provider_id = rule.provider_id
        recording.stream_id = rule.stream_id
        recording.channel_name = rule.channel_name
        recording.event_title = event.title
        recording.scheduled_start = begin
        recording.scheduled_end = end
        recording.mode = RecordingMode.FUTURE

        ok = self.manager.recorder.start(
            recording, None, provider, channel, event, rule.record_path)
        if not ok:
            _log("RECORDER FAILED (repeat) %s" % rule.name)
            return

        rule.last_run = begin
        self.manager.save()
        _log("STARTED (repeat) %s" % rule.name)

    def _already_covered(self, rule, padded_begin, padded_end):
        """True if a recording matching this rule's identity is already
        active or scheduled — i.e. the immediate-launch thread spawned
        at rule-creation time (RecordingManager.add_rule) is already
        handling it, so the scheduler must not start a second one.

        _create_timer_direct() (the immediate-launch path) stores the
        *padded* begin/end under the "event_start"/"event_end" keys
        when it isn't given a real EPG event object — so that's what
        we compare against here, not the rule's raw event_start/end."""

        from ..state import state as _state

        try:

            entries = _state.active_recordings.values()

        except Exception:

            return False

        for entry in entries:

            if not isinstance(entry, dict):
                continue

            # Same rule, or the same channel recording at an overlapping
            # time. The immediate-launch timer stores the programme's OWN
            # start/end when it has the guide event (unpadded), so an
            # exact match on the padded times never matched and the
            # scheduler started a second, duplicate recording.
            if rule.rule_id and entry.get("rule_id") == rule.rule_id:
                return True

            if entry.get("status") not in (None, "scheduled", "active"):
                continue

            if (
                entry.get("provider_id") == rule.provider_id and
                str(entry.get("stream_id")) == str(rule.stream_id)
            ):
                try:
                    e_begin = int(entry.get("event_start") or 0)
                    e_end = int(entry.get("event_end") or 0)
                except Exception:
                    continue
                if e_begin < padded_end and e_end > padded_begin:
                    return True

        return False




# -----------------------------------------------------------------------------
# Repeating-rule helpers (also used by the EPG grid to show and remove them)
# -----------------------------------------------------------------------------

def _sunday_index(t):
    """0 = Sunday ... 6 = Saturday, matching the Repeat panel's order."""
    return (time.localtime(t).tm_wday + 1) % 7


def occurrences(rule, day_start, day_end):
    """(begin, end) of every occurrence of a repeating rule that starts
    between day_start and day_end, padding included, never before the
    first date chosen when the rule was made."""
    days = list(getattr(rule, "days", []) or [])
    if not any(days):
        return []
    try:
        hh, mm = [int(x) for x in str(rule.start_time).split(":")[:2]]
    except Exception:
        return []
    dur = int(getattr(rule, "duration", 60) or 60) * 60
    first = int(getattr(rule, "event_start", 0) or 0) - int(getattr(rule, "pre_padding", 0) or 0) * 60
    out = []
    t = time.localtime(day_start - 86400)
    midnight = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))
    while midnight <= day_end:
        lt = time.localtime(midnight)
        begin = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1)))
        if (days[_sunday_index(begin)] and day_start <= begin <= day_end
                and begin >= first - 60):
            out.append((begin, begin + dur))
        nxt = time.localtime(midnight + 26 * 3600)      # next day (DST-safe)
        midnight = int(time.mktime((nxt.tm_year, nxt.tm_mon, nxt.tm_mday, 0, 0, 0, 0, 0, -1)))
    return out


def core_window(rule, begin, end):
    """An occurrence without its recording offsets - the times actually
    chosen. Used for the grid's green marker, so the programmes just
    before and after aren't marked as well."""
    pre = int(getattr(rule, "pre_padding", 0) or 0) * 60
    post = int(getattr(rule, "post_padding", 0) or 0) * 60
    if end - post <= begin + pre:
        return begin, end
    return begin + pre, end - post


def current_occurrence(rule, now):
    """The occurrence running at `now` (it may have started yesterday)."""
    dur = int(getattr(rule, "duration", 60) or 60) * 60
    for begin, end in occurrences(rule, now - dur - 60, now):
        if begin <= now < end:
            return begin, end
    return None


def occurrence_event(rule, channel, begin, end):
    """An Epg for one occurrence, titled after the programme on at the
    time if the guide has it (else the rule's own title)."""
    from ..backend.epg import Epg
    ev = Epg()
    ev.stream_id = channel.stream_id
    ev.start, ev.end = begin, end
    ev.title = getattr(rule, "event_title", "") or rule.name
    ev.description = ""
    try:
        from ..state import state
        mid = begin + (end - begin) // 2
        for g in state.backend.epg_store.get_for(channel):
            if g.start <= mid < g.end and getattr(g, "title", ""):
                ev.title = g.title
                ev.description = getattr(g, "description", "") or ""
                break
    except Exception:
        pass
    return ev


def repeat_rule_for(manager, provider, channel, ev_start, ev_end):
    """The repeating rule whose occurrence overlaps this programme on
    this channel, or None (used for the grid's green marker and for
    Remove Repeat Recording)."""
    if manager is None or provider is None or channel is None:
        return None
    for rule in manager.enabled_rules():
        if getattr(rule, "rule_type", "") != "custom":
            continue
        if (rule.provider_id != provider.provider_id
                or str(rule.stream_id) != str(channel.stream_id)):
            continue
        for b, e in occurrences(rule, ev_start - 86400, ev_end):
            b, e = core_window(rule, b, e)
            if b < ev_end and e > ev_start:
                return rule
    return None
