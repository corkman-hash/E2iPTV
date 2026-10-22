# =============================================================================
# E2iPTV — what's set to record (Recordings -> "Scheduled recordings")
#
# One list of everything still to come or recording now:
#   - one-off recordings (from the grid's Record or Manual Recording),
#   - repeat recordings (Manual Recording -> Repeat), shown at their next
#     occurrence with their days,
#   - recordings running or waiting that have no rule (Record from now).
# A one-off recording usually has both a rule and a waiting/running
# timer; they're matched up and shown once.
# =============================================================================

import time

_DAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def days_text(days):
    days = [bool(d) for d in (days or [])][:7]
    if len(days) < 7 or not any(days):
        return ""
    if all(days):
        return "Every day"
    if days == [False, True, True, True, True, True, False]:
        return "Mon-Fri"
    if days == [True, False, False, False, False, False, True]:
        return "Sat-Sun"
    on = [i for i, d in enumerate(days) if d]
    # a single run of consecutive days, e.g. Mon-Thu
    if len(on) > 2 and on == list(range(on[0], on[-1] + 1)):
        return "%s-%s" % (_DAY_SHORT[on[0]], _DAY_SHORT[on[-1]])
    return " ".join(_DAY_SHORT[i] for i in on)


def _provider_names(state):
    names = {}
    for name, p in getattr(state, "providers", None) or []:
        if getattr(p, "is_bouquet", False):
            continue
        names[getattr(p, "provider_id", None)] = name
    return names


def _overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and a_end > b_start


def scheduled_items(state):
    """Everything still to record, soonest first. Each item is a dict:
    kind ("once" | "repeat" | "manual"), status ("active" | "scheduled"),
    title, channel, provider, start, end, days (repeat only), rule
    (or None), rec_key (or None)."""
    now = int(time.time())
    try:
        mgr = getattr(state, "recording_manager", None)
        if mgr is not None:
            mgr.sync_rule_providers()     # follow a reordered playlists.txt
    except Exception:
        pass
    names = _provider_names(state)
    items = []

    # Waiting / running timers.
    timers = []
    for key, rec in list((getattr(state, "active_recordings", None) or {}).items()):
        if not isinstance(rec, dict):
            continue
        if rec.get("status") not in ("scheduled", "active"):
            continue
        try:
            s, e = int(rec.get("event_start") or 0), int(rec.get("event_end") or 0)
        except Exception:
            continue
        if e and e <= now and rec.get("status") != "active":
            continue
        timers.append([key, rec, s, e, False])     # last = matched to a rule

    def match_timer(provider_id, stream_id, s, e, rule_id=None):
        for t in timers:
            key, rec, ts, te, used = t
            if used:
                continue
            if rule_id and rec.get("rule_id") == rule_id:
                t[4] = True
                return t
            if (rec.get("provider_id") == provider_id
                    and str(rec.get("stream_id")) == str(stream_id)
                    and _overlaps(ts, te or ts + 60, s, e)):
                t[4] = True
                return t
        return None

    manager = getattr(state, "recording_manager", None)
    rules = []
    try:
        rules = manager.enabled_rules() if manager is not None else []
    except Exception:
        rules = []

    for rule in rules:
        rtype = getattr(rule, "rule_type", "once")
        pre = int(getattr(rule, "pre_padding", 0) or 0) * 60
        post = int(getattr(rule, "post_padding", 0) or 0) * 60
        title = getattr(rule, "event_title", "") or getattr(rule, "name", "")
        base = {
            "title": title,
            "channel": getattr(rule, "channel_name", "") or "",
            "provider": names.get(rule.provider_id, ""),
            "rule": rule,
            "rec_key": None,
            "days": None,
        }
        if rtype == "custom":
            try:
                from .scheduler import occurrences
                occ = [o for o in occurrences(rule, now - 86400, now + 8 * 86400)
                       if o[1] > now]
            except Exception:
                occ = []
            if not occ:
                continue
            s, e = occ[0]
            t = match_timer(rule.provider_id, rule.stream_id, s, e, rule.rule_id)
            item = dict(base, kind="repeat", start=s, end=e,
                        days=list(getattr(rule, "days", []) or []),
                        status="active" if (t and t[1].get("status") == "active")
                        or (s <= now < e and t is not None) else "scheduled")
            if t:
                item["rec_key"] = t[0]
            items.append(item)
            continue
        if rtype != "once":
            continue
        s = int(getattr(rule, "event_start", 0) or 0) - pre
        e = int(getattr(rule, "event_end", 0) or 0) + post
        if not e or e <= now:
            continue
        t = match_timer(rule.provider_id, rule.stream_id, s, e, rule.rule_id)
        if (getattr(rule, "last_run", 0) == getattr(rule, "event_start", -1)
                and t is None):
            continue                    # already recorded
        item = dict(base, kind="once", start=s, end=e,
                    status=t[1].get("status") if t else "scheduled")
        if t:
            item["rec_key"] = t[0]
        items.append(item)

    # Timers with no rule: Record from now / leftovers.
    for key, rec, s, e, used in timers:
        if used:
            continue
        items.append({
            "kind": "manual",
            "status": rec.get("status"),
            "title": rec.get("title") or rec.get("channel") or "Recording",
            "channel": rec.get("channel") or "",
            "provider": names.get(rec.get("provider_id"), ""),
            "start": s, "end": e, "days": None,
            "rule": None, "rec_key": key,
        })

    items.sort(key=lambda i: (i["status"] != "active", i["start"] or 0))
    return items


def cancel(state, item, what):
    """what: "cancel" (a one-off not started yet), "stop_now" (stop the
    recording running now, keep any repeat), "stop_repeat" (remove the
    repeat rule). Returns True if something changed."""
    from ..player import stop_recording
    manager = getattr(state, "recording_manager", None)
    rule = item.get("rule")
    changed = False
    if what in ("cancel", "stop_now") and item.get("rec_key"):
        changed = stop_recording(item["rec_key"]) or changed
    if what == "cancel" and rule is not None and manager is not None:
        manager.remove_rule(rule)
        changed = True
    if what == "stop_now" and rule is not None and manager is not None:
        # Don't let the scheduler start it again straight away.
        if getattr(rule, "rule_type", "once") == "once":
            rule.last_run = rule.event_start
        else:
            rule.last_run = item.get("start") or rule.last_run
        manager.save()
        changed = True
    if what == "stop_repeat" and rule is not None and manager is not None:
        manager.remove_rule(rule)
        changed = True
    return changed
