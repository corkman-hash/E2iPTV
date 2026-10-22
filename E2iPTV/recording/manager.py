# =============================================================================
# E2iPTV
# Recording Manager
# =============================================================================

from ..paths import LOG  # data folder / debug logs
from .rule_store import RecordingRuleStore
from .recording_store import RecordingStore

from .rule_reader import RecordingRuleReader
from .rule_writer import RecordingRuleWriter

from .scheduler import RecordingScheduler
from .recorder import Recorder
from .resolver import RecordingResolver

from .rule import RecordingRule
from .status import RuleType

import time

class RecordingManager(object):

    def __init__(self):

        self.rule_store = RecordingRuleStore()

        self.recording_store = RecordingStore()

        self.rule_reader = RecordingRuleReader()
        self.rule_writer = RecordingRuleWriter()

        self.resolver = RecordingResolver(self)

        self.recorder = Recorder(self)

        self.scheduler = RecordingScheduler(self)

    # -------------------------------------------------------------------------

    def load(self):

        self.rule_store.clear()

        for rule in self.rule_reader.read():

            self.rule_store.add(rule)

    # -------------------------------------------------------------------------

    def save(self):

        return self.rule_writer.write(
            self.rule_store.all()
        )

    # -------------------------------------------------------------------------

    def start(self):

        self.load()

        self.scheduler.start()

    # -------------------------------------------------------------------------

    def stop(self):

        self.scheduler.stop()

    # -------------------------------------------------------------------------

    def add_rule(self, rule):

        #
        # Prevent duplicate rules.
        #

        for existing in self.rule_store.all():

            # A repeat rule only clashes with an identical repeat rule
            # (same days and time). An old one-off rule for the same
            # programme (e.g. tonight's episode, already recorded) must
            # not block setting up the repeat — it used to, silently.
            new_type = getattr(rule, "rule_type", RuleType.ONCE)
            old_type = getattr(existing, "rule_type", RuleType.ONCE)
            if new_type != old_type:
                continue
            if new_type == RuleType.CUSTOM and (
                list(existing.days or []) != list(rule.days or []) or
                existing.start_time != rule.start_time or
                existing.duration != rule.duration
            ):
                continue

            if (
                existing.provider_id == rule.provider_id and
                str(existing.stream_id) == str(rule.stream_id) and
                existing.event_start == rule.event_start and
                existing.event_end == rule.event_end
            ):

                try:
                    with open(LOG("e2iptv_rules.log"), "a") as f:
                        f.write(
                            "DUPLICATE RULE IGNORED: %s\n" %
                            rule.name
                        )
                except Exception:
                    pass

                return False

        try:
            with open(LOG("e2iptv_rules.log"), "a") as f:
                f.write(
                    "ADD RULE: %s\n" %
                    rule.name
                )
        except Exception:
            pass

        self.rule_store.add(rule)

        ok = self.save()

        try:
            with open(LOG("e2iptv_rules.log"), "a") as f:
                f.write(
                    "SAVE RESULT: %s\n" %
                    ok
                )
        except Exception:
            pass

        #
        # Launch the actual recording thread immediately, right now,
        # while the app is open and the provider list is guaranteed
        # loaded — rather than relying solely on the background
        # scheduler to resolve the provider hours later at trigger
        # time (when the app may not have been opened since boot).
        # The scheduler still acts as a fallback (e.g. if this thread
        # dies because the GUI restarts before showtime).
        #
        # event_start/event_end on the rule are the raw EPG times;
        # padding is applied here to get the actual record window.
        #

        if ok and rule.rule_type == RuleType.ONCE:

            provider = self.resolver.resolve_provider(
                rule.provider_id
            )

            if provider:

                from ..state import state
                from ..player import (
                    build_live_url,
                    _create_timer_direct
                )

                channel = state.backend.find_live_channel(
                    provider,
                    rule.stream_id
                )

                if channel:

                    padded_begin = rule.event_start - (
                        getattr(rule, "pre_padding", 0) or 0
                    ) * 60

                    padded_end = rule.event_end + (
                        getattr(rule, "post_padding", 0) or 0
                    ) * 60

                    # Resolve the actual EPG event (for its real
                    # description/synopsis) rather than passing ev=None
                    # — RecordingRule only ever stores event_title, not
                    # a synopsis, so without this there's no way to
                    # get one at all. Wrapped defensively: if the
                    # event's since scrolled out of the EPG cache, the
                    # recording must still go ahead regardless — this
                    # is metadata enrichment, not a precondition.
                    try:
                        ev = state.backend.find_epg_event(
                            provider, channel,
                            rule.event_start, rule.event_end
                        )
                    except Exception:
                        ev = None

                    _create_timer_direct(
                        None,
                        build_live_url(provider, channel),
                        padded_begin,
                        padded_end,
                        rule.event_title,
                        ev.description if ev else "",
                        rule.record_path,
                        provider,
                        channel,
                        ev,
                        trigger=rule.trigger,
                        rule_id=rule.rule_id
                    )

        return ok
    # -------------------------------------------------------------------------

    def create_rule(
        self,
        provider,
        channel,
        event,
        pre_padding_mins,
        post_padding_mins,
        repeat_days,
        name,
        record_path
    ):

        rule = RecordingRule()

        rule.name = name

        rule.provider_id = provider.provider_id
        rule.provider_key = getattr(provider, "provider_key", "") or ""
        rule.category_id = channel.category_id
        rule.stream_id = channel.stream_id
        rule.channel_name = channel.name

        rule.record_path = record_path

        # event_start/event_end are the EPG event's OWN raw times —
        # this is the identity the rest of the app (EPG tile colour
        # check, resolver lookup, "remove future" matching, duplicate
        # detection) compares against. Padding must NOT be baked in
        # here, or none of those comparisons will ever match the tile
        # actually shown on the grid.
        rule.event_title = event.title if event else name
        rule.event_start = event.start if event else 0
        rule.event_end   = event.end   if event else 0

        # Padding is stored separately and applied only at the point
        # the scheduler actually triggers/stops the recording.
        rule.pre_padding  = pre_padding_mins
        rule.post_padding = post_padding_mins

        padded_begin = rule.event_start - pre_padding_mins * 60
        padded_end   = rule.event_end   + post_padding_mins * 60

        rule.start_time = time.strftime(
            "%H:%M",
            time.localtime(padded_begin)
        )

        rule.duration = int((padded_end - padded_begin) / 60)

        if any(repeat_days):

            rule.rule_type = RuleType.CUSTOM
            rule.days = list(repeat_days)

        else:

            rule.rule_type = RuleType.ONCE

        return rule

    # -------------------------------------------------------------------------

    def remove_rule(self, rule):

        self.rule_store.remove(rule)

        self.save()

    # -------------------------------------------------------------------------

    def sync_rule_providers(self):
        """Point every rule at the right provider after playlists.txt has
        been reordered. Rules keep the provider's own key and have their
        position number (provider_id) refreshed from it. Older rules made
        before the key was stored get it once, from the saved channel
        lists: the provider whose channel has the rule's stream number AND
        channel name."""
        from ..state import state
        provs = [p for _n, p in (getattr(state, "providers", None) or [])
                 if not getattr(p, "is_bouquet", False)
                 and getattr(p, "provider_key", "")]
        if not provs:
            return
        by_key = dict((str(p.provider_key), p) for p in provs)
        changed = False
        unkeyed = []
        for rule in self.rule_store.all():
            key = str(getattr(rule, "provider_key", "") or "")
            if not key:
                unkeyed.append(rule)
                continue
            p = by_key.get(key)
            if p is not None and rule.provider_id != p.provider_id:
                rule.provider_id = p.provider_id
                changed = True
        if unkeyed:
            found = _keys_from_channel_lists(unkeyed, set(by_key))
            # The full search of every saved channel list (seconds, with
            # many providers) runs in the background; its findings are
            # picked up here on a later tick.
            for rid, keys in _take_background_results().items():
                lst = found.setdefault(rid, [])
                for k in keys:
                    if k in by_key and k not in lst:
                        lst.append(k)
            still = [r for r in unkeyed if not found.get(r.rule_id)]
            if still:
                _start_background_scan(still, set(by_key))
            for rule in unkeyed:
                keys = found.get(rule.rule_id) or []
                if not keys:
                    continue
                # Several providers can carry the same channel under the
                # same number; then keep the one it already points at.
                at_old = [k for k in keys
                          if by_key[k].provider_id == rule.provider_id]
                key = at_old[0] if at_old else sorted(keys)[0]
                rule.provider_key = key
                rule.provider_id = by_key[key].provider_id
                changed = True
        if changed:
            self.save()
        # Recordings running or waiting (also those picked up after a
        # restart) follow their provider the same way.
        try:
            for rec in list((getattr(state, "active_recordings", None) or {}).values()):
                p = by_key.get(str(rec.get("provider_key") or ""))
                if p is not None and rec.get("provider_id") != p.provider_id:
                    rec["provider_id"] = p.provider_id
        except Exception:
            pass

    def get_rule(self, rule_id):

        return self.rule_store.get(rule_id)

    # -------------------------------------------------------------------------

    def rules(self):

        return self.rule_store.all()

    # -------------------------------------------------------------------------

    def enabled_rules(self):

        return self.rule_store.enabled()

    # -------------------------------------------------------------------------

    def recordings(self):

        return self.recording_store.all()

    # -------------------------------------------------------------------------

    def active_recordings(self):

        return self.recording_store.active()

    # -------------------------------------------------------------------------

    def finished_recordings(self):

        return self.recording_store.finished()

    # -------------------------------------------------------------------------

    def finish_recording(
        self,
        rec_key
    ):

        for recording in self.recording_store.active():

            if recording.rec_key == rec_key:

                self.recorder.stop(
                    recording
                )

                return True

        return False



_bg = {"running": False, "results": {}, "last": 0.0, "tried": {}}
_RETRY_UNMATCHED = 6 * 3600      # a rule nothing matched: look again after this


def _take_background_results():
    res, _bg["results"] = _bg["results"], {}
    return res


def _start_background_scan(rules, valid_keys):
    """Search every saved channel list for these rules' channels, in a
    background thread - never on the box's main thread (that took ~5 s
    and froze the screen). At most every 30 minutes; a rule it can't
    find is only looked for again after 6 hours."""
    import threading
    import time as _t
    now = _t.time()
    if _bg["running"] or now - _bg["last"] < 1800:
        return
    due = [r for r in rules
           if now - _bg["tried"].get(r.rule_id, 0) >= _RETRY_UNMATCHED]
    if not due:
        return
    _bg["running"] = True
    _bg["last"] = now

    def work():
        try:
            found = _keys_from_channel_lists(due, valid_keys, full=True)
            for r in due:
                if not found.get(r.rule_id):
                    _bg["tried"][r.rule_id] = _t.time()
            merged = dict(_bg["results"])
            merged.update(found)
            _bg["results"] = merged
        except Exception as e:
            print("[E2iPTV] rule channel search:", e)
        finally:
            _bg["running"] = False
    threading.Thread(target=work, name="E2iPTV-RuleScan", daemon=True).start()


def _keys_from_channel_lists(rules, valid_keys, full=False):
    """{rule_id: [provider_key, ...]} for rules whose channel (stream number
    and name) appears in a provider's saved channel lists
    (cache/ch_<provider_key>_<category>.json). Without `full`, only each
    rule's own category list for each provider (a dozen small files - fine
    on the main thread); `full` reads every saved list (background only)."""
    import json
    import os
    from ..paths import DATA
    wanted = {}
    for r in rules:
        wanted.setdefault((str(r.stream_id),
                           str(r.channel_name or "").strip().casefold()),
                          []).append(r.rule_id)
    out = {}
    folder = DATA + "/cache"
    if full:
        try:
            names = [n for n in os.listdir(folder)
                     if n.startswith("ch_") and n.endswith(".json")]
        except OSError:
            names = []
    else:
        names = []
        for r in rules:
            cat = str(getattr(r, "category_id", "") or "").replace("/", "_")
            if cat:
                names.extend("ch_%s_%s.json" % (k, cat) for k in valid_keys)
        names = [n for n in set(names) if os.path.exists(os.path.join(folder, n))]
    for n in names:
        key = n[3:-5].split("_", 1)[0]
        if key not in valid_keys:
            continue
        try:
            with open(os.path.join(folder, n)) as f:
                entries = json.load(f)
        except Exception:
            continue
        for e in entries or []:
            k = (str(e.get("stream_id", "")),
                 str(e.get("name", "")).strip().casefold())
            for rid in wanted.get(k, ()):
                lst = out.setdefault(rid, [])
                if key not in lst:
                    lst.append(key)
    return out
