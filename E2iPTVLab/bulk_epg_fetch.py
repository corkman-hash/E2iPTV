#!/usr/bin/env python3

import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime

try:
    import urllib2
except ImportError:
    import urllib.request as urllib2


PLUGIN_ROOT = "/usr/lib/enigma2/python/Plugins/Extensions"
PYTHON_ROOT = "/usr/lib/enigma2/python"

for _path in (PLUGIN_ROOT, PYTHON_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from E2iPTVLab.paths import HDD, DATA  # data folder / E2iPTV_Data


PLAYLISTS = None
CACHE_DIR = DATA + "/epg"

STREAM_TIMEOUT = 15
WORKER_POLL = 0.25

# Confirmed directly on this exact box: a job with no cap on how long
# one provider can run kept a single process alive for over 21 hours,
# stuck entirely on one large provider, never reaching any other
# provider (Stalker included) for the rest of that entire time. This
# is a hard ceiling on one provider's run, so a large or slow
# provider can't turn the whole night into an unsupervised, endless
# job — it stops cleanly and reports what it got done, and the run
# moves on to the next provider instead of never finishing.
PROVIDER_TIME_BUDGET = 7200

_PROVIDER_LOCK_DIR = DATA + "/epg"


def _provider_lock_path(provider):
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(provider.name))
    return os.path.join(_PROVIDER_LOCK_DIR, ".lock_%s" % safe_name)


def _acquire_provider_lock(provider):
    """False means a run for this same provider is already in
    progress and this call should back off rather than start a
    second, overlapping one on the same provider at the same time.
    A lock file records the owning PID; if that PID is no longer
    alive (a crashed or killed prior run), the lock is stale and gets
    taken over automatically rather than blocking every future run
    forever."""
    path = _provider_lock_path(provider)
    try:
        if os.path.exists(path):
            with open(path) as f:
                old_pid = f.read().strip()
            if old_pid and os.path.exists("/proc/%s" % old_pid):
                return False
        with open(path, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        # Never let lock-file bookkeeping itself be the reason a
        # legitimate run can't start.
        return True


def _release_provider_lock(provider):
    try:
        os.remove(_provider_lock_path(provider))
    except Exception:
        pass


def log(message):
    print(message, flush=True)
    _note_reason(message)
    _note_progress(message)


# -----------------------------------------------------------------------------
# Per-provider summary (epg/last_summary.json) — read by Settings ->
# Status & Logs -> EPG refresh summary. Each provider keeps its own last
# result, so a one-provider manual run only replaces that provider's entry.
# Failure reasons are picked out of the log lines as they're written and
# counted; anything that looks like a login, token or address is removed
# first so the summary never holds account details.
# -----------------------------------------------------------------------------

SUMMARY_FILE = DATA + "/epg/last_summary.json"
_JOB_START = None

_reasons = {}       # provider name -> {reason text: count}
_skipped = {}       # provider name -> why

_REASON_RE = re.compile(
    r"^\[(?P<prov>[^\]]+)\] (?:stream \S+: )?(?P<what>"
    r"FAILED - .*|TIMEOUT - .*|ERROR obtaining live channels.*"
    r"|STALKER BULK INITIALISATION FAILED.*|SKIPPED: .*)$")


def _scrub(text):
    text = re.sub(r"https?://\S+", "(address)", text)
    text = re.sub(r"(?i)\b(username|password|token|mac|key)=[^&\s]+",
                  r"\1=***", text)
    text = re.sub(r"(?i)\b([0-9a-f]{2}:){5}[0-9a-f]{2}\b", "(mac)", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:90]


def _note_reason(message):
    try:
        m = _REASON_RE.match(str(message))
        if not m:
            return
        prov, what = m.group("prov"), m.group("what")
        if what.startswith("SKIPPED: "):
            _skipped[prov] = _scrub(what[9:])
            return
        if what.startswith("FAILED - ") or what.startswith("TIMEOUT - "):
            kind, _, detail = what.partition(" - ")
            what = ("Timed out" if kind == "TIMEOUT" else "Failed") + (
                ": " + detail if detail.strip() else "")
        elif what.startswith("ERROR obtaining"):
            what = "Could not get the channel list:" + what.split(":", 1)[-1]
        else:
            what = "Could not start the bulk guide download:" + what.split(":", 1)[-1]
        what = _scrub(what)
        bucket = _reasons.setdefault(prov, {})
        bucket[what] = bucket.get(what, 0) + 1
    except Exception:
        pass


def _load_summary():
    try:
        with open(SUMMARY_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("providers", {})
            return data
    except Exception:
        pass
    return {"providers": {}}


def _save_summary(data):
    try:
        tmp = SUMMARY_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, SUMMARY_FILE)
    except Exception as ex:
        print("summary write failed: %s" % ex, flush=True)


def _summary_start(job_start):
    data = _load_summary()
    data["run"] = {"started": int(job_start), "pid": os.getpid(),
                   "finished": None, "status": "RUNNING", "done": 0}
    _save_summary(data)


def _provider_entry(r, when):
    reasons = sorted(_reasons.get(r["name"], {}).items(),
                     key=lambda kv: -kv[1])[:5]
    if r["status"] == "SUCCESS":
        label = "OK"
    elif r["refreshed"] > 0:
        label = "PARTIAL"
    else:
        label = "FAILED"
    return {"when": when, "status": label,
            "channels": r["channels"], "refreshed": r["refreshed"],
            "no_data": r["no_data"], "failed": r["failed"],
            "timeout": r["timeout"], "seconds": r["seconds"],
            "reasons": reasons}


def _summary_finish(job_start, provider_results, status, fatal=None):
    data = _load_summary()
    now = int(time.time())
    for r in provider_results:
        # providers already written as they finished keep their own time
        old = data["providers"].get(r["name"]) or {}
        when = old.get("when") if (old.get("when") or 0) >= int(job_start) else now
        data["providers"][r["name"]] = _provider_entry(r, when)
    for name, why in _skipped.items():
        data["providers"][name] = {
            "when": now, "status": "SKIPPED", "reasons": [[why, 1]]}
    data["run"] = {"started": int(job_start), "pid": os.getpid(),
                   "finished": now, "status": status}
    if fatal:
        data["run"]["fatal"] = _scrub(fatal)
    _save_summary(data)


# -- progress while running ---------------------------------------------------
# The summary screen shows which provider is being worked on and roughly
# how far through its channels it is. Channels are counted from the
# per-channel result lines as they're logged (a guide shared by several
# channels counts for all of them); written at most every 20 seconds.

_progress = {}
_PROGRESS_RE = re.compile(
    r"^\[[^\]]+\] (?:stream \S+: (?:SUCCESS|FAILED|TIMEOUT|NO[ _]DATA)"
    r"|EPG ID \S+: SUCCESS - \d+ programmes copied to (?P<n>\d+) streams)")


def _write_progress(force=False):
    if not _progress:
        return
    now = time.time()
    if not force and now - _progress.get("written", 0) < 20:
        return
    _progress["written"] = now
    data = _load_summary()
    run = data.get("run") or {}
    if run.get("pid") != os.getpid():
        return
    run["current"] = {k: _progress[k] for k in
                      ("name", "index", "total", "channels", "done", "started")}
    data["run"] = run
    _save_summary(data)


def _progress_start(name, index, total, channels):
    _progress.clear()
    _progress.update({"name": name, "index": index, "total": total,
                      "channels": channels, "done": 0,
                      "started": int(time.time()), "written": 0})
    _write_progress(force=True)


def _note_progress(message):
    try:
        if not _progress:
            return
        m = _PROGRESS_RE.match(str(message))
        if not m:
            return
        _progress["done"] += int(m.group("n")) if m.group("n") else 1
        _write_progress()
    except Exception:
        pass


def _progress_provider_done(result):
    """Write one provider's result as soon as it finishes."""
    try:
        data = _load_summary()
        now = int(time.time())
        data["providers"][result["name"]] = _provider_entry(result, now)
        for name, why in _skipped.items():
            data["providers"][name] = {
                "when": now, "status": "SKIPPED", "reasons": [[why, 1]]}
        run = data.get("run") or {}
        run.pop("current", None)
        run["done"] = run.get("done", 0) + 1
        data["run"] = run
        _save_summary(data)
    except Exception:
        pass
    _progress.clear()


def get_providers():
    # STAGE 1:
    # The EPG job uses the playlist configured by Settings -> Other -> Playlists.
    # This is the single authoritative EPG provider source — PLAYLISTS
    # itself is never hardcoded (see its own module-level comment):
    # it's always set from the caller's own state.playlists_path, via
    # the --playlists argument, before this is ever called.
    from E2iPTVLab.backend.provider import Provider

    providers = []

    if not PLAYLISTS:
        log(
            "FATAL: no playlists path configured — this must always be "
            "passed via --playlists (see main()'s own argument parsing), "
            "reflecting whatever Settings -> Other -> Playlists is "
            "actually set to. Never hardcoded here."
        )
        return providers

    try:
        with open(PLAYLISTS, "r") as f:
            lines = [line.strip() for line in f if line.strip()]

    except Exception as ex:
        log(
            "FATAL: cannot read configured playlists: %s"
            % ex
        )
        return providers

    for line_number, line in enumerate(lines, 1):

        # A provider whose playlist line starts with #http is disabled.
        if line.startswith("#http"):
            continue

        if not line.startswith("http"):
            continue

        try:
            name = line.split("#")[-1].strip()
            url = line.split("#")[0].strip()

            provider = Provider()

            # This ID is only the current runtime provider ID.
            # It is NOT used as persistent provider identity.
            provider.provider_id = len(providers) + 1
            provider.name = name
            provider.enabled = True

            # Stalker/Ministra portals authenticate by MAC address
            # rather than username/password — same detection this
            # project's own playlist_reader.py uses (mac= rather than
            # get.php/player_api.php), so a line like
            # http://portal.example/c/?mac=00:1A:79:XX:XX:XX#Name is
            # recognised here too. Before this, a Stalker line simply
            # failed the username/password check below and was
            # silently skipped — meaning the nightly EPG job never
            # even saw a Stalker provider at all, regardless of
            # EpgReader itself already supporting one.
            stalker_m = re.search(r'mac=([0-9A-Fa-f:]{17})', url)

            if stalker_m:
                provider.source_type = "STALKER"
                provider.mac = stalker_m.group(1).upper()
                provider.base_url = url.split("?")[0].rstrip("/")
                provider.build_provider_key()
                providers.append(provider)
                continue

            m = re.match(r"(https?://[^/]+)", url)
            u = re.search(r"username=([^&]+)", url)
            p = re.search(r"password=([^&]+)", url)

            if not m or not u or not p:
                log(
                    "[PLAYLIST] invalid entry %d - skipped"
                    % line_number
                )
                continue

            provider.base_url = m.group(1)
            provider.username = u.group(1)
            provider.password = p.group(1)
            provider.build_provider_key()

            providers.append(provider)

        except Exception as ex:
            log(
                "[PLAYLIST] provider entry %d failed to parse: %s"
                % (
                    line_number,
                    ex
                )
            )

    # A provider switched off in Settings -> Playlists -> (provider) ->
    # Enable: No is left out too, the same as a #http line - before, only
    # the # was honoured here and a provider switched off in Settings
    # still got refreshed every night.
    kept = []
    for provider in providers:
        try:
            with open(DATA + "/cache/provider_%s.json"
                      % provider.provider_key) as f:
                cfg = json.load(f)
                if cfg.get("enabled", True) is False:
                    log("[PLAYLIST] %s is switched off in Settings - skipped"
                        % provider.name)
                    continue
                if cfg.get("show_live", True) is False:
                    log("[PLAYLIST] %s has Live TV switched off - no guide needed, skipped"
                        % provider.name)
                    continue
        except Exception:
            pass
        kept.append(provider)
    return kept


# =============================================================================
# LEGACY EPG INPUT — DISABLED
# =============================================================================
# These functions read the UI's /hdd/e2iptv_cache/ch_<provider>_*.json files.
# They are deliberately retained for rollback/cleanup reference only.
# The EPG job must NOT depend on the channel cache being present or complete.
# DELETE this legacy block after the new direct-supplier path has been proven.
# =============================================================================

def get_stream_ids(source_id):

    disabled_cats = set()

    try:
        with open(
            DATA + "/cache/disabled_cats_%d.json" % source_id,
            "r"
        ) as f:
            disabled_cats = set(str(x) for x in json.load(f))
    except Exception:
        pass

    stream_ids = set()

    for path in glob.glob(
        DATA + "/cache/ch_%d_*.json" % source_id
    ):

        try:
            with open(path, "r") as f:
                channels = json.load(f)

            for channel in channels:

                stream_id = str(channel.get("stream_id", ""))
                category_id = str(channel.get("category_id", ""))

                if stream_id and category_id not in disabled_cats:
                    stream_ids.add(stream_id)

        except Exception as ex:
            log(
                "[LEGACY CACHE] source %d: cannot read %s: %s"
                % (source_id, path, ex)
            )

    return sorted(
        stream_ids,
        key=lambda x: int(x) if x.isdigit() else x
    )


def get_stream_epg_map(source_id, stream_ids):

    wanted = set(
        str(x)
        for x in stream_ids
    )

    stream_info = {}

    for path in glob.glob(
        DATA + "/cache/ch_%d_*.json" % source_id
    ):

        try:
            with open(path, "r") as f:
                channels = json.load(f)

        except Exception as ex:
            log(
                "[LEGACY CACHE] source %d: cannot read %s: %s"
                % (
                    source_id,
                    path,
                    ex
                )
            )
            continue

        for channel in channels:

            stream_id = str(
                channel.get("stream_id", "")
            ).strip()

            if not stream_id:
                continue

            if stream_id not in wanted:
                continue

            epg_id = str(
                channel.get(
                    "epg_channel_id",
                    ""
                ) or ""
            ).strip()

            stream_info[stream_id] = {
                "epg_id": epg_id
            }

    return stream_info


# =============================================================================
# NEW EPG INPUT — DIRECT FROM SUPPLIER
# =============================================================================
# This is now the authoritative EPG input. The supplier is asked directly for
# its complete live-stream list. The UI channel cache is not involved.
# The only local file consulted here is disabled_cats_<provider>.json, which is
# a user setting rather than a channel-data cache.
# =============================================================================

def get_supplier_channels(provider, limit=None, category_filter=None):

    # STAGE 1:
    # Provider category visibility is keyed by provider NAME, not the
    # generated playlist provider_id.
    from E2iPTVLab.backend.provider_category_settings import load

    disabled_cats = load(provider)

    if provider.source_type == "STALKER":
        return _get_supplier_channels_stalker(
            provider,
            disabled_cats,
            limit=limit,
            category_filter=category_filter
        )

    # -----------------------------------------------------------------
    # CURRENT CATEGORY-FIRST EPG INPUT
    #
    # Do NOT request the supplier's complete live-stream universe.
    # Obtain the current live categories first, remove our hidden
    # categories, then request streams only from visible categories.
    # -----------------------------------------------------------------

    categories_url = (
        provider.base_url
        + "/player_api.php?username=%s&password=%s"
        "&action=get_live_categories"
        % (
            provider.username,
            provider.password
        )
    )

    try:
        response = urllib2.urlopen(
            categories_url,
            timeout=30
        )

        categories = json.loads(
            response.read()
        )

        if not isinstance(categories, list):
            categories = []

    except Exception as ex:

        log(
            "[%s] CATEGORY FETCH FAILED: %s"
            % (
                provider.name,
                ex
            )
        )

        return []

    visible_categories = []

    for category in categories:

        category_id = str(
            category.get(
                "category_id",
                ""
            )
        ).strip()

        category_name = str(
            category.get(
                "category_name",
                ""
            )
        ).strip()

        if not category_id:
            continue

        if category_id in disabled_cats:
            continue

        visible_categories.append(
            (
                category_id,
                category_name
            )
        )

    if category_filter:
        wanted = category_filter.strip().casefold()
        visible_categories = [
            (cid, cname) for cid, cname in visible_categories
            if cname.strip().casefold() == wanted
        ]

    hidden_category_count = sum(
        1
        for category in categories
        if str(
            category.get(
                "category_id",
                ""
            )
        ).strip() in disabled_cats
    )

    log(
        "[%s] Categories: %d total | %d hidden | %d visible"
        % (
            provider.name,
            len(categories),
            hidden_category_count,
            len(visible_categories)
        )
    )

    channels = []
    seen_stream_ids = set()

    for category_id, category_name in visible_categories:

        url = (
            provider.base_url
            + "/player_api.php?username=%s&password=%s"
            "&action=get_live_streams&category_id=%s"
            % (
                provider.username,
                provider.password,
                category_id
            )
        )

        try:

            response = urllib2.urlopen(
                url,
                timeout=30
            )

            data = json.loads(
                response.read()
            )

            if not isinstance(data, list):
                continue

        except Exception as ex:

            log(
                "[%s] CATEGORY %s (%s): channel fetch failed: %s"
                % (
                    provider.name,
                    category_id,
                    category_name,
                    ex
                )
            )

            continue

        for item in data:

            stream_id = str(
                item.get(
                    "stream_id",
                    ""
                )
            ).strip()

            if not stream_id:
                continue

            if stream_id in seen_stream_ids:
                continue

            seen_stream_ids.add(stream_id)

            item_category_id = str(
                item.get(
                    "category_id",
                    category_id
                )
            ).strip()

            # Defensive check: never allow a hidden category through.
            if item_category_id in disabled_cats:
                continue

            channels.append(
                {
                    "stream_id": stream_id,
                    "category_id": item_category_id,
                    "epg_channel_id": str(
                        item.get(
                            "epg_channel_id",
                            ""
                        ) or ""
                    ).strip(),
                    # Catch-up: only these channels need past programmes.
                    "archive": _flag(item.get("tv_archive"))
                }
            )

    log(
        "[%s] EPG input channels after category filtering: %d"
        % (
            provider.name,
            len(channels)
        )
    )

    return channels


def _get_supplier_channels_stalker(provider, disabled_cats, limit=None, category_filter=None):
    """Stalker equivalent of get_supplier_channels above — same
    category-first approach (genres instead of Xtream categories,
    per-genre channel listing instead of get_live_streams), normalised
    to the exact same {"stream_id", "category_id", "epg_channel_id"}
    shape the rest of this script expects, so nothing downstream of
    this needs to know or care which protocol a given provider uses.
    """

    from E2iPTVLab.backend.stalker_client import StalkerClient, StalkerError

    client = StalkerClient(provider)

    try:
        genres = client.get_genres()
    except StalkerError as ex:
        log(
            "[%s] CATEGORY FETCH FAILED: %s"
            % (provider.name, ex)
        )
        return []

    visible_genres = []

    for genre in genres:
        genre_id = str(genre.get("id", "")).strip()
        genre_title = str(genre.get("title", "")).strip()

        if not genre_id:
            continue

        # The portal's own "All Channels" pseudo-category — every
        # channel in it already appears in one of the real categories
        # below too, so fetching it here is a redundant, extremely
        # slow (often several-thousand-channel) pass that adds
        # nothing: first real test against a live portal spent over
        # an hour stuck paging through exactly this before ever
        # reaching a second, real category.
        if genre_id == "*":
            continue

        if genre_id in disabled_cats:
            continue

        visible_genres.append((genre_id, genre_title))

    if category_filter:
        wanted = category_filter.strip().casefold()
        visible_genres = [
            (gid, gtitle) for gid, gtitle in visible_genres
            if gtitle.strip().casefold() == wanted
        ]

    hidden_genre_count = sum(
        1 for genre in genres
        if str(genre.get("id", "")).strip() in disabled_cats
    )

    log(
        "[%s] Categories: %d total | %d hidden | %d visible"
        % (
            provider.name,
            len(genres),
            hidden_genre_count,
            len(visible_genres)
        )
    )

    channels = []
    seen_stream_ids = set()

    def _fetch_genre(index, genre):
        genre_id, genre_title = genre

        log(
            "[%s] Category %d/%d: %s (id=%s)"
            % (
                provider.name,
                index,
                len(visible_genres),
                genre_title,
                genre_id
            )
        )

        try:
            items = client.get_channels(genre_id, limit=limit)
            return index, genre_id, genre_title, items, None
        except StalkerError as ex:
            return index, genre_id, genre_title, [], ex

    if limit is not None:
        # Preserve the original bounded behaviour exactly:
        # fetch categories sequentially and stop immediately when
        # enough unique channels have been collected.
        for genre_index, genre in enumerate(visible_genres, 1):

            index, genre_id, genre_title, items, error = _fetch_genre(
                genre_index,
                genre
            )

            if error is not None:
                log(
                    "[%s] CATEGORY %s (%s): channel fetch failed: %s"
                    % (
                        provider.name,
                        genre_id,
                        genre_title,
                        error
                    )
                )
                continue

            for item in items:

                stream_id = str(item.get("id", "")).strip()

                if not stream_id:
                    continue

                if stream_id in seen_stream_ids:
                    continue

                seen_stream_ids.add(stream_id)

                item_genre_id = str(
                    item.get("tv_genre_id", genre_id)
                ).strip()

                if item_genre_id in disabled_cats:
                    continue

                channels.append({
                    "stream_id": stream_id,
                    "category_id": item_genre_id,
                    # Deliberately NOT populated from the portal's own
                    # xmltv_id field — confirmed directly on a real
                    # run that grouping Stalker channels together by
                    # a shared xmltv_id causes one channel's real
                    # programme data to appear under other, unrelated
                    # channels across different categories. That
                    # field is only trustworthy for this purpose on
                    # Xtream, where it's a deliberate broadcaster
                    # identity; nothing has confirmed it means the
                    # same thing on this portal type. Leaving this
                    # empty forces every Stalker channel through its
                    # own individual EPG check instead of being
                    # grouped with others.
                    "epg_channel_id": ""
                })

                if len(channels) >= limit:
                    break

            if len(channels) >= limit:
                log(
                    "[%s] Supplier scan limit reached: %d channels"
                    % (
                        provider.name,
                        len(channels)
                    )
                )
                break

    else:
        from concurrent.futures import ThreadPoolExecutor

        # Full provider runs can fetch independent categories in
        # parallel. Results are sorted afterwards so downstream
        # processing remains deterministic.
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(
                    _fetch_genre,
                    index,
                    genre
                )
                for index, genre in enumerate(visible_genres, 1)
            ]

            results = [
                future.result()
                for future in futures
            ]

        results.sort(key=lambda result: result[0])

        for genre_index, genre_id, genre_title, items, error in results:

            if error is not None:
                log(
                    "[%s] CATEGORY %s (%s): channel fetch failed: %s"
                    % (
                        provider.name,
                        genre_id,
                        genre_title,
                        error
                    )
                )
                continue

            for item in items:

                stream_id = str(item.get("id", "")).strip()

                if not stream_id:
                    continue

                if stream_id in seen_stream_ids:
                    continue

                seen_stream_ids.add(stream_id)

                item_genre_id = str(
                    item.get("tv_genre_id", genre_id)
                ).strip()

                if item_genre_id in disabled_cats:
                    continue

                channels.append({
                    "stream_id": stream_id,
                    "category_id": item_genre_id,
                    # See the other branch's identical comment just
                    # above for why this is deliberately empty.
                    "epg_channel_id": ""
                })

    log(
        "[%s] EPG input channels after category filtering: %d"
        % (provider.name, len(channels))
    )

    return channels


def bulk_harvest_stalker_epg_generic(provider, stream_ids, days=1):
    """
    Generic native Stalker EPG acquisition.

    Uses the provider's native Stalker per-channel EPG endpoint and
    requests the configured number of forward days.

    No provider name, channel ID or portal URL is hard-coded.

    Returns:
        dict: stream_id -> list of EPG dictionaries
    """

    if getattr(provider, "source_type", "") != "STALKER":
        return {}

    if not stream_ids:
        return {}

    try:
        from E2iPTVLab.backend.stalker_client import StalkerClient
        client = StalkerClient(provider)
    except Exception as ex:
        log(
            "[%s] STALKER EPG CLIENT INIT FAILED: %s"
            % (provider.name, ex)
        )
        return {}

    wanted = set(str(x) for x in stream_ids)
    harvested = {}

    seed_id = next(iter(wanted))

    try:
        raw_items = client.get_epg(
            seed_id,
            days
        )
    except Exception as ex:
        log(
            "[%s] STALKER EPG FAILED on seed %s: %s"
            % (
                provider.name,
                seed_id,
                ex
            )
        )
        return {}

    if not isinstance(raw_items, list):
        log(
            "[%s] STALKER EPG returned invalid data"
            % provider.name
        )
        return {}

    converted = []

    manual_offset = (
        int(getattr(provider, "epg_offset", 0))
        * 3600
    )

    for item in raw_items:

        if not isinstance(item, dict):
            continue

        title = (
            item.get("name", "")
            or item.get("title", "")
        ).strip()

        description = (
            item.get("descr", "")
            or item.get("description", "")
            or ""
        )

        try:
            start = int(
                item.get(
                    "start_timestamp",
                    item.get("time", 0)
                )
            )
        except (TypeError, ValueError):
            start = 0

        try:
            end = int(
                item.get(
                    "stop_timestamp",
                    item.get("time_to", 0)
                )
            )
        except (TypeError, ValueError):
            end = 0

        if start <= 0 or end <= start:
            continue

        start += manual_offset
        end += manual_offset

        converted.append(
            {
                "stream_id": seed_id,
                "title": title,
                "description": description,
                "start": start,
                "end": end,
            }
        )

    if converted:
        converted.sort(
            key=lambda item: item["start"]
        )
        harvested[seed_id] = converted

    log(
        "[%s] STALKER EPG seed %s: %d programme(s), %d day(s)"
        % (
            provider.name,
            seed_id,
            len(converted),
            days
        )
    )

    log(
        "[%s] STALKER EPG complete: %d channel(s) harvested"
        % (
            provider.name,
            len(harvested)
        )
    )

    return harvested

_last_save_error = ""   # why the last save_entries() call failed


def save_entries(path, entries):

    global _last_save_error
    _last_save_error = ""

    if not entries:
        _last_save_error = "nothing to save"
        return False

    data = []

    for entry in entries:
        data.append({
            "stream_id": str(entry.get("stream_id", "")),
            "title": entry.get("title", ""),
            "description": entry.get("description", ""),
            "start": int(entry.get("start", 0)),
            "end": int(entry.get("end", 0))
        })

    tmp_path = path + ".tmp"

    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, path)
        return True

    except Exception as ex:

        _last_save_error = (getattr(ex, "strerror", None) or str(ex))
        log(
            "[CACHE] write failed %s: %s"
            % (path, ex)
        )

        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

        return False


# =============================================================================
# RICHNESS CHECK
#
# Neither bulk XMLTV nor the per-stream Xtream call takes a date-range
# parameter on our side — both just return whatever the source happens to
# hold. Most of the time XMLTV is the richer, cheaper source, so it's tried
# first. But some providers' own XMLTV feeds only publish a day or so each
# side of "now" (some providers do this), while their Xtream panel's
# get_simple_data_table has always given a much wider window (7 days back
# and forward). When that happens we don't want the mere presence of a thin
# XMLTV feed to permanently starve the wider per-stream call of a chance to
# run — so if what we already have doesn't reach MIN_EPG_DAYS_EACH_WAY in
# both directions, we also fetch per-stream and merge the two.
# =============================================================================

MIN_EPG_DAYS_EACH_WAY = 3


def _epg_entry_start(entry):
    try:
        return int(entry.start) if hasattr(entry, "start") else int(entry.get("start", 0))
    except Exception:
        return 0


def _epg_entry_end(entry):
    try:
        return int(entry.end) if hasattr(entry, "end") else int(entry.get("end", 0))
    except Exception:
        return 0


def _flag(value):
    try:
        return bool(int(value or 0))
    except (TypeError, ValueError):
        return False


def _start_of_today():
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def _trim_past(entries):
    """Drop programmes that ended before the start of today — for
    channels without catch-up, where past programmes can't be played."""
    cut = _start_of_today()
    return [e for e in entries if int(e.get("end", 0) or 0) > cut]


def _epg_is_thin(entries, now, min_days=MIN_EPG_DAYS_EACH_WAY, need_past=True):
    """True when entries don't reach min_days into the past AND min_days
    into the future relative to now. need_past=False (channels without
    catch-up) only checks the future: those channels can never have days
    of playable history, so demanding it just caused pointless top-up
    requests every night."""

    starts = [_epg_entry_start(e) for e in entries]
    starts = [s for s in starts if s]

    if not starts:
        log(
            "[EPG RANGE] empty entries -> THIN"
        )
        return True

    earliest = min(starts)
    latest = max(starts)

    days_back = (now - earliest) / 86400.0
    days_forward = (latest - now) / 86400.0

    thin = (
        (need_past and days_back < min_days)
        or days_forward < min_days
    )

    log(
        "[EPG RANGE] %d programmes | back=%.2fd | forward=%.2fd | "
        "required=%dd | %s"
        % (
            len(entries),
            days_back,
            days_forward,
            min_days,
            "THIN" if thin else "RICH"
        )
    )

    return thin


def _epg_merge_entries(primary, extra):
    """Union of two entry lists (each item either an Epg object or a dict —
    both forms occur here), deduped by (start, end). primary's version wins
    for any slot both cover; extra only fills in the days primary lacks."""
    seen = {}

    for e in primary:
        seen[(_epg_entry_start(e), _epg_entry_end(e))] = e

    for e in extra:
        key = (_epg_entry_start(e), _epg_entry_end(e))
        if key not in seen:
            seen[key] = e

    return sorted(seen.values(), key=_epg_entry_start)


def worker_main(source_id, stream_id, result_path):

    result = {
        "status": "FAILED",
        "stream_id": str(stream_id),
        "entries": [],
        "error": ""
    }

    try:
        from E2iPTVLab.backend.epg_reader import EpgReader

        providers = get_providers()

        provider = None

        for item in providers:
            if item.provider_id == source_id:
                provider = item
                break

        if provider is None:
            raise RuntimeError(
                "provider %d not found"
                % source_id
            )

        class Channel(object):
            pass

        channel = Channel()
        channel.source_id = source_id
        channel.stream_id = str(stream_id)

        reader = EpgReader()

        entries = reader.read(provider, channel)

        if not entries:
            result["status"] = "NO_DATA"
        else:
            result["status"] = "SUCCESS"

            for entry in entries:
                result["entries"].append({
                    "stream_id": str(entry.stream_id),
                    "title": entry.title,
                    "description": entry.description,
                    "start": int(entry.start),
                    "end": int(entry.end)
                })

    except Exception as ex:

        result["status"] = "FAILED"
        result["error"] = (
            "%s: %s"
            % (type(ex).__name__, ex)
        )

    try:
        with open(result_path, "w") as f:
            json.dump(result, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())

    except Exception:
        pass

    return 0


def fetch_stream_stalker_inprocess(client, reader, provider, stream_id):
    """The Stalker equivalent of run_stream, but reusing one already
    -handshaked StalkerClient across every channel instead of paying
    for a brand new process + brand new handshake each time (run_
    stream's own subprocess-per-channel approach). Returns the exact
    same {"status", "entries", ...} shape run_stream/worker_main
    produce, so every caller downstream of this needs no changes —
    it can't tell the difference."""
    from E2iPTVLab.backend.stalker_client import StalkerError

    result = {
        "status": "FAILED",
        "stream_id": str(stream_id),
        "entries": [],
        "error": ""
    }

    try:
        items = client.get_epg(str(stream_id), days=4)
        entries = reader._build_stalker_entries(provider, str(stream_id), items)

        if not entries:
            result["status"] = "NO_DATA"
        else:
            result["status"] = "SUCCESS"
            for entry in entries:
                result["entries"].append({
                    "stream_id": str(entry.stream_id),
                    "title": entry.title,
                    "description": entry.description,
                    "start": int(entry.start),
                    "end": int(entry.end)
                })

    except StalkerError as ex:
        result["status"] = "FAILED"
        result["error"] = "StalkerError: %s" % ex
    except Exception as ex:
        result["status"] = "FAILED"
        result["error"] = "%s: %s" % (type(ex).__name__, ex)

    return result


def _wait_if_paused(provider):
    """If this provider recently answered "Too Many Requests", wait until
    its pause is over before asking for the next channel's guide (the
    pause is shared with the screens - see backend/xtream_client.py)."""
    try:
        from E2iPTVLab.backend.xtream_client import _read_wait
        until, _length = _read_wait(provider)
        wait = until - time.time()
        if wait > 0:
            log("[%s] provider asked to slow down - waiting %ds"
                % (provider.name, int(wait) + 1))
            time.sleep(min(wait, 300) + 1)
    except Exception:
        pass


def run_stream(source_id, stream_id, result_path):

    try:
        os.remove(result_path)
    except Exception:
        pass

    command = [
        sys.executable,
        os.path.abspath(__file__),
        "--worker",
        "--source",
        str(source_id),
        "--stream",
        str(stream_id),
        "--result",
        result_path,
        "--playlists",
        PLAYLISTS
    ]

    env = os.environ.copy()

    plugin_root = (
        "/usr/lib/enigma2/python/Plugins/Extensions"
    )

    python_root = "/usr/lib/enigma2/python"

    existing = env.get("PYTHONPATH", "")

    paths = [
        plugin_root,
        python_root
    ]

    if existing:
        paths.append(existing)

    env["PYTHONPATH"] = ":".join(paths)

    try:

        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            close_fds=True
        )

    except Exception as ex:

        return {
            "status": "FAILED",
            "error": (
                "worker start failed: %s"
                % ex
            ),
            "entries": []
        }

    deadline = time.time() + STREAM_TIMEOUT

    while process.poll() is None:

        if time.time() >= deadline:

            try:
                process.kill()
            except Exception:
                pass

            try:
                process.wait()
            except Exception:
                pass

            return {
                "status": "TIMEOUT",
                "error": (
                    "worker exceeded %d second timeout"
                    % STREAM_TIMEOUT
                ),
                "entries": []
            }

        time.sleep(WORKER_POLL)

    try:
        with open(result_path, "r") as f:
            result = json.load(f)

        if not isinstance(result, dict):
            raise ValueError("invalid worker result")

        return result

    except Exception as ex:

        return {
            "status": "FAILED",
            "error": (
                "worker exited without valid result: %s"
                % ex
            ),
            "entries": []
        }


def main():

    job_start = time.time()

    parser = argparse.ArgumentParser()

    # LEGACY PROVIDER INDEX — retained temporarily for compatibility.
    # Provider selection for manual runs must use the provider name.
    parser.add_argument(
        "--source",
        type=int,
        default=None
    )

    parser.add_argument(
        "--provider",
        default=None
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None
    )

    parser.add_argument(
        "--category",
        default=None,
        help=(
            "Restrict this run to a single category/genre, matched "
            "by its exact display title (case-insensitive) — e.g. "
            "\"UK| GENERAL\". Omit to process every visible category, "
            "same as before."
        )
    )

    parser.add_argument(
        "--worker",
        action="store_true"
    )

    parser.add_argument(
        "--stream",
        default=None
    )

    parser.add_argument(
        "--result",
        default=None
    )

    parser.add_argument(
        "--playlists",
        default=None,
        help=(
            "Path to the playlists file — always the caller's own "
            "state.playlists_path (Settings -> Other -> Playlists), "
            "never hardcoded here. Required."
        )
    )

    args = parser.parse_args()

    global PLAYLISTS
    if args.playlists:
        PLAYLISTS = args.playlists
    elif not PLAYLISTS:
        log(
            "FATAL: --playlists was not provided — this script never "
            "assumes a default location on its own; the caller must "
            "always pass the actual, currently-configured path."
        )
        return 1

    if args.worker:

        if (
            args.source is None or
            args.stream is None or
            not args.result
        ):
            return 2

        return worker_main(
            args.source,
            args.stream,
            args.result
        )

    _summary_start(job_start)
    global _JOB_START
    _JOB_START = job_start

    log("===== EPG NIGHTLY JOB START =====")
    log(
        "Start: %s"
        % datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    log(
        "Per-stream hard timeout: %d seconds"
        % STREAM_TIMEOUT
    )

    os.makedirs(CACHE_DIR, exist_ok=True)

    providers = get_providers()

    # Keep the complete active provider list available for
    # cross-provider XMLTV fallback. The "providers" list may
    # subsequently be restricted by --provider or --source.
    all_providers = list(providers)

    if not providers:
        log("FATAL: no providers found")
        log("===== EPG NIGHTLY JOB END =====")
        _summary_finish(job_start, [], "FAILED", "No providers found")
        return 1

    if args.provider is not None:

        wanted_provider = args.provider.strip().casefold()

        providers = [
            p for p in providers
            if str(p.name).strip().casefold() == wanted_provider
        ]

        if not providers:
            log(
                "FATAL: provider '%s' not found in current active playlists"
                % args.provider
            )
            return 1

    elif args.source is not None:

        # LEGACY PROVIDER INDEX — do not use for normal operation.
        providers = [
            p for p in providers
            if p.provider_id == args.source
        ]

        if not providers:
            log(
                "FATAL: legacy provider index %d not found"
                % args.source
            )
            return 1

    log(
        "Providers loaded: %d"
        % len(providers)
    )

    total_refreshed = 0
    total_no_data = 0
    total_failed = 0
    total_timeout = 0
    total_channels = 0
    total_api_fetches = 0
    total_shared_streams = 0

    provider_results = []

    for provider in providers:

        provider_start = time.time()

        if not _acquire_provider_lock(provider):
            log(
                "[%s] SKIPPED: another EPG run for this provider is "
                "already in progress"
                % provider.name
            )
            continue

        source_id = provider.provider_id
        provider_key = provider.provider_key

        # NEW DIRECT-SUPPLIER PATH
        # The EPG job no longer depends on the UI channel cache.
        supplier_failed = False

        try:
            supplier_channels = get_supplier_channels(
                provider,
                limit=args.limit,
                category_filter=args.category
            )

            stream_ids = [
                str(ch["stream_id"])
                for ch in supplier_channels
                if ch.get("stream_id")
            ]

            stream_info = {
                str(ch["stream_id"]): {
                    "epg_id": str(
                        ch.get("epg_channel_id", "")
                        or ""
                    ),
                    # Stalker catch-up can't be played yet, so Stalker
                    # channels never need past programmes.
                    "archive": bool(ch.get("archive", False))
                }
                for ch in supplier_channels
                if ch.get("stream_id")
            }

        except Exception as ex:
            supplier_failed = True
            stream_ids = []
            stream_info = {}
            log(
                "[%s] ERROR obtaining live channels directly from supplier: %s"
                % (provider.name, ex)
            )

        supplier_channel_count = len(stream_ids)

        if args.limit is not None:
            stream_ids = stream_ids[:max(0, args.limit)]
            stream_info = {
                sid: stream_info[sid]
                for sid in stream_ids
                if sid in stream_info
            }

        channel_count = len(stream_ids)
        total_channels += channel_count

        log("")
        log(
            "========== PROVIDER %d/%d: %s =========="
            % (
                len(provider_results) + 1,
                len(providers),
                provider.name
            )
        )
        _progress_start(provider.name, len(provider_results) + 1,
                        len(providers), channel_count)

        log(
            "[%s] Channels to process: %d"
            % (
                provider.name,
                channel_count
            )
        )

        log(
            "[%s] Supplier live channels: %d; enabled for EPG: %d"
            % (
                provider.name,
                supplier_channel_count,
                channel_count
            )
        )

        groups = {}
        individual_streams = []

        for stream_id in stream_ids:

            info = stream_info.get(
                str(stream_id),
                {}
            )

            epg_id = str(
                info.get("epg_id", "") or ""
            ).strip()

            if epg_id:

                groups.setdefault(
                    epg_id,
                    []
                ).append(
                    str(stream_id)
                )

            else:

                individual_streams.append(
                    str(stream_id)
                )

        fetch_jobs = []

        for epg_id, members in groups.items():

            fetch_jobs.append({
                "epg_id": epg_id,
                "streams": members,
                "representative": members[0]
            })

        for stream_id in individual_streams:

            fetch_jobs.append({
                "epg_id": "",
                "streams": [stream_id],
                "representative": stream_id
            })

        fetch_jobs.sort(
            key=lambda job: (
                int(job["representative"])
                if job["representative"].isdigit()
                else job["representative"]
            )
        )

        # ---------------------------------------------------------
        # NATIVE STALKER BULK EPG
        #
        # Attempt the provider's own multi-channel EPG once before
        # falling back to individual per-stream requests.
        #
        # This is deliberately based only on source_type. No provider
        # name, URL or channel ID is special-cased.
        #
        # XMLTV/Rytec remain higher priority below. The bulk data is
        # only used when those sources provide nothing.
        # ---------------------------------------------------------

        stalker_bulk_entries = {}

        if provider.source_type == "STALKER" and stream_ids:

            try:

                stalker_bulk_entries = (
                    bulk_harvest_stalker_epg_generic(
                        provider,
                        stream_ids,
                        days=4
                    )
                )

                if not isinstance(
                    stalker_bulk_entries,
                    dict
                ):
                    stalker_bulk_entries = {}

            except Exception as ex:

                stalker_bulk_entries = {}

                log(
                    "[%s] STALKER BULK INITIALISATION FAILED: %s"
                    % (
                        provider.name,
                        ex
                    )
                )

        api_fetch_count = len(fetch_jobs)

        shared_stream_count = sum(
            max(0, len(job["streams"]) - 1)
            for job in fetch_jobs
            if job["epg_id"]
        )

        total_api_fetches += api_fetch_count
        total_shared_streams += shared_stream_count

        log(
            "[%s] Unique EPG fetch groups: %d"
            % (
                provider.name,
                api_fetch_count
            )
        )

        log(
            "[%s] API fetches avoided by EPG ID sharing: %d"
            % (
                provider.name,
                shared_stream_count
            )
        )

        refreshed = 0
        no_data = 0
        failed = 0
        timeout = 0

        # =============================================================
        # BULK XMLTV
        #
        # Fetch the provider XMLTV feed once.
        # Match its channel IDs against the supplier epg_channel_id.
        # Every stream sharing an EPG ID receives the same programme
        # data. Existing per-stream EpgReader remains the fallback
        # when bulk XMLTV has no data.
        # =============================================================

        # ---------------------------------------------------------
        # BULK XMLTV / RYTEC
        #
        # Load the current provider's XMLTV immediately.
        #
        # Other active providers are loaded lazily, only when a
        # requested EPG ID is not present in the current provider.
        #
        # Rytec is also loaded lazily, only when a requested EPG ID
        # has a Rytec mapping and no XMLTV source has supplied data.
        # ---------------------------------------------------------

        provider_xmltv_pool = {}
        provider_xmltv = {}
        rytec_xmltv = {}
        rytec_loaded = False
        xmltv_reader = None

        if channel_count > 0:

            try:

                from E2iPTVLab.backend.xmltv_reader import XmltvReader

                xmltv_reader = XmltvReader()

                fetch_started = time.time()

                try:

                    data = xmltv_reader.fetch(
                        provider
                    )

                    if not isinstance(data, dict):
                        data = {}

                    provider_xmltv_pool[
                        provider.provider_id
                    ] = data

                    provider_xmltv = data

                    log(
                        "[%s] Bulk XMLTV: %d channel IDs loaded | %.1fs"
                        % (
                            provider.name,
                            len(data),
                            time.time() - fetch_started
                        )
                    )

                except Exception as ex:

                    provider_xmltv_pool[
                        provider.provider_id
                    ] = {}

                    provider_xmltv = {}

                    log(
                        "[%s] Bulk XMLTV FETCH FAILED after %.1fs: %s"
                        % (
                            provider.name,
                            time.time() - fetch_started,
                            ex
                        )
                    )

            except Exception as ex:

                provider_xmltv_pool = {}
                provider_xmltv = {}
                xmltv_reader = None

                log(
                    "[%s] Bulk XMLTV INITIALISATION FAILED: %s"
                    % (
                        provider.name,
                        ex
                    )
                )

        else:

            log(
                "[%s] NO CHANNELS - skipping XMLTV/Rytec acquisition"
                % provider.name
            )

        if not stream_ids:

            log(
                "[%s] NO CHANNELS"
                % provider.name
            )

        else:

            stalker_worker_results = {}

            stalker_client = None
            stalker_reader = None

            if provider.source_type == "STALKER":
                # One session, reused for every channel this provider
                # needs the slow per-channel path for — see
                # fetch_stream_stalker_inprocess's own docstring for
                # why this is faster than run_stream's subprocess
                # -per-channel approach.
                from E2iPTVLab.backend.stalker_client import StalkerClient
                from E2iPTVLab.backend.epg_reader import EpgReader
                stalker_client = StalkerClient(provider)
                stalker_reader = EpgReader()

            for number, job in enumerate(
                fetch_jobs,
                1
            ):

                if time.time() - provider_start > PROVIDER_TIME_BUDGET:
                    log(
                        "[%s] TIME BUDGET EXCEEDED (%ds) — stopping "
                        "early at %d/%d, rest left for next run"
                        % (
                            provider.name,
                            PROVIDER_TIME_BUDGET,
                            number - 1,
                            len(fetch_jobs)
                        )
                    )
                    break

                representative = job["representative"]
                epg_id = job["epg_id"]
                members = job["streams"]

                entries = []

                # -----------------------------------------------------
                # FIRST: provider XMLTV
                # -----------------------------------------------------

                if epg_id:

                    # ---------------------------------------------
                    # FIRST: this provider's own XMLTV
                    # ---------------------------------------------

                    entries = provider_xmltv.get(
                        epg_id.casefold(),
                        []
                    )

                    native_stalker_bulk_available = bool(
                        provider.source_type == "STALKER"
                        and stalker_bulk_entries.get(
                            str(representative),
                            []
                        )
                    )

                    if entries:

                        log(
                            "[%s] EPG group %d/%d: EPG ID %s: BULK XMLTV LOCAL - %d programmes -> %d streams"
                            % (
                                provider.name,
                                number,
                                api_fetch_count,
                                epg_id,
                                len(entries),
                                len(members)
                            )
                        )

                    elif (
                        not native_stalker_bulk_available
                        and provider.source_type != "STALKER"
                    ):

                        # -----------------------------------------
                        # SECOND: another ACTIVE E2iPTV provider
                        # -----------------------------------------
                        #
                        # Only use this when the current provider
                        # has no XMLTV data for this EPG ID and
                        # native Stalker bulk has no data.
                        # -----------------------------------------

                        for fallback_provider in all_providers:

                            if (
                                fallback_provider.provider_id
                                == source_id
                            ):
                                continue

                            fallback_xmltv = (
                                provider_xmltv_pool.get(
                                    fallback_provider.provider_id
                                )
                            )

                            if fallback_xmltv is None:

                                if xmltv_reader is None:
                                    continue

                                fallback_started = time.time()

                                try:

                                    fallback_xmltv = (
                                        xmltv_reader.fetch(
                                            fallback_provider
                                        )
                                    )

                                    if not isinstance(
                                        fallback_xmltv,
                                        dict
                                    ):
                                        fallback_xmltv = {}

                                    provider_xmltv_pool[
                                        fallback_provider.provider_id
                                    ] = fallback_xmltv

                                    log(
                                        "[%s] Bulk XMLTV: %d channel IDs loaded | %.1fs"
                                        % (
                                            fallback_provider.name,
                                            len(fallback_xmltv),
                                            time.time() - fallback_started
                                        )
                                    )

                                except Exception as ex:

                                    fallback_xmltv = {}

                                    provider_xmltv_pool[
                                        fallback_provider.provider_id
                                    ] = {}

                                    log(
                                        "[%s] Bulk XMLTV FETCH FAILED after %.1fs: %s"
                                        % (
                                            fallback_provider.name,
                                            time.time() - fallback_started,
                                            ex
                                        )
                                    )

                            fallback_entries = (
                                fallback_xmltv.get(
                                    epg_id.casefold(),
                                    []
                                )
                            )

                            if not fallback_entries:
                                continue

                            entries = fallback_entries

                            log(
                                "[%s] EPG group %d/%d: EPG ID %s: BULK XMLTV FALLBACK -> %s - %d programmes -> %d streams"
                                % (
                                    provider.name,
                                    number,
                                    api_fetch_count,
                                    epg_id,
                                    fallback_provider.name,
                                    len(entries),
                                    len(members)
                                )
                            )

                            break

                # -----------------------------------------------------
                # THIRD: RYTEC XMLTV
                #
                # Use the existing RYTEC_ID_MAP from xmltv_reader.py.
                #
                # Example:
                #
                #   itvcentral.uk
                #       -> ITV1Central.uk
                #
                # This is deliberately after local and cross-provider
                # XMLTV so a provider's own XMLTV always wins.
                # -----------------------------------------------------

                if (
                    not entries
                    and epg_id
                    and not native_stalker_bulk_available
                    and provider.source_type != "STALKER"
                ):

                    try:

                        from E2iPTVLab.backend.xmltv_reader import RYTEC_ID_MAP

                        rytec_id = RYTEC_ID_MAP.get(
                            epg_id.casefold(),
                            ""
                        )

                        if rytec_id:

                            if not rytec_loaded:

                                if xmltv_reader is not None:

                                    rytec_started = time.time()

                                    try:

                                        rytec_xmltv = (
                                            xmltv_reader.fetch_rytec()
                                        )

                                        if not isinstance(
                                            rytec_xmltv,
                                            dict
                                        ):
                                            rytec_xmltv = {}

                                        log(
                                            "[RYTEC] Bulk XMLTV: %d channel IDs loaded | %.1fs"
                                            % (
                                                len(rytec_xmltv),
                                                time.time() - rytec_started
                                            )
                                        )

                                    except Exception as ex:

                                        rytec_xmltv = {}

                                        log(
                                            "[RYTEC] Bulk XMLTV FETCH FAILED after %.1fs: %s"
                                            % (
                                                time.time() - rytec_started,
                                                ex
                                            )
                                        )

                                rytec_loaded = True

                            rytec_entries = rytec_xmltv.get(
                                rytec_id.casefold(),
                                []
                            )

                            if rytec_entries:

                                entries = rytec_entries

                                log(
                                    "[%s] EPG group %d/%d: EPG ID %s: RYTEC XMLTV -> %s - %d programmes -> %d streams"
                                    % (
                                        provider.name,
                                        number,
                                        api_fetch_count,
                                        epg_id,
                                        rytec_id,
                                        len(entries),
                                        len(members)
                                    )
                                )

                    except Exception as ex:

                        log(
                            "[%s] EPG ID %s: RYTEC lookup failed: %s"
                            % (
                                provider.name,
                                epg_id,
                                ex
                            )
                        )

                # -----------------------------------------------------
                # FOURTH: native Stalker bulk EPG
                #
                # XMLTV and Rytec remain higher priority. If neither
                # supplied data, use the native bulk result for this
                # representative stream before starting an individual
                # worker request.
                # -----------------------------------------------------

                if (
                    not entries
                    and provider.source_type == "STALKER"
                ):

                    bulk_entries = stalker_bulk_entries.get(
                        str(representative),
                        []
                    )

                    if bulk_entries:

                        entries = bulk_entries

                        log(
                            "[%s] EPG group %d/%d: STALKER BULK -> stream %s -> %d programmes -> %d streams"
                            % (
                                provider.name,
                                number,
                                api_fetch_count,
                                representative,
                                len(entries),
                                len(members)
                            )
                        )

                # -----------------------------------------------------
                # FALLBACK: existing per-stream EpgReader
                #
                # Runs when we have nothing yet, OR when what XMLTV/Rytec
                # gave us is thin (doesn't reach MIN_EPG_DAYS_EACH_WAY back
                # and forward — see _epg_is_thin). In the "thin" case we're
                # topping up an already-good set, not starting from
                # scratch, so a timeout/failure/no-data result here just
                # means we keep the thin set rather than losing the group
                # entirely.
                # -----------------------------------------------------

                richer_entries = entries
                augmenting = bool(entries)

                bulk_entries_used = bool(
                    entries
                    and provider.source_type == "STALKER"
                    and str(representative) in stalker_bulk_entries
                )

                group_archive = any(
                    stream_info.get(str(m), {}).get("archive", False)
                    for m in members
                )

                if (
                    not entries
                    or (
                        not bulk_entries_used
                        and _epg_is_thin(entries, time.time(),
                                         need_past=group_archive)
                    )
                ):

                    result_path = (
                        "%s/.worker_%s_%s_%d.json"
                        % (
                            CACHE_DIR,
                            provider_key,
                            representative,
                            os.getpid()
                        )
                    )

                    # -------------------------------------------------
                    # 24-HOUR NO-DATA CACHE
                    #
                    # Only skip a fresh per-stream fetch when the previous
                    # fetch genuinely returned NO DATA. Thin/top-up fetches
                    # are never suppressed.
                    # -------------------------------------------------

                    nodata_marker = (
                        "%s/.nodata_%s_%s"
                        % (
                            CACHE_DIR,
                            provider_key,
                            representative
                        )
                    )

                    if not augmenting:

                        try:

                            if provider.source_type == "STALKER":
                                log(
                                    "[%s] DIAG stream %s: checking marker %s "
                                    "exists=%s"
                                    % (
                                        provider.name,
                                        representative,
                                        nodata_marker,
                                        os.path.exists(nodata_marker)
                                    )
                                )

                            if os.path.exists(nodata_marker):

                                marker_age = (
                                    time.time() -
                                    os.path.getmtime(nodata_marker)
                                )

                                if marker_age < 86400:

                                    no_data += len(members)

                                    if epg_id and len(members) > 1:

                                        log(
                                            "[%s] EPG ID %s: NO DATA CACHE - %d streams"
                                            % (
                                                provider.name,
                                                epg_id,
                                                len(members)
                                            )
                                        )

                                    else:

                                        log(
                                            "[%s] stream %s: NO DATA CACHE"
                                            % (
                                                provider.name,
                                                representative
                                            )
                                        )

                                    continue

                                else:

                                    os.remove(nodata_marker)

                        except Exception:

                            pass

                    if (
                        not augmenting
                        and epg_id
                    ):

                        epgid_marker = (
                            "%s/.nodata_epgid_%s_%s"
                            % (
                                CACHE_DIR,
                                provider_key,
                                epg_id
                            )
                        )

                        try:

                            if os.path.exists(epgid_marker):

                                marker_age = (
                                    time.time()
                                    - os.path.getmtime(
                                        epgid_marker
                                    )
                                )

                                if marker_age < 86400:

                                    no_data += len(members)

                                    log(
                                        "[%s] EPG ID %s: NO DATA CACHE"
                                        % (
                                            provider.name,
                                            epg_id
                                        )
                                    )

                                    continue

                                else:

                                    os.remove(
                                        epgid_marker
                                    )

                        except Exception:

                            pass

                    if augmenting:

                        log(
                            "[%s] EPG group %d/%d: EPG ID %s: THIN (%d programmes) - TOPPING UP stream %s -> %d streams"
                            % (
                                provider.name,
                                number,
                                api_fetch_count,
                                epg_id,
                                len(richer_entries),
                                representative,
                                len(members)
                            )
                        )

                    elif epg_id:

                        log(
                            "[%s] EPG group %d/%d: EPG ID %s: FALLBACK stream %s -> %d streams"
                            % (
                                provider.name,
                                number,
                                api_fetch_count,
                                epg_id,
                                representative,
                                len(members)
                            )
                        )

                    else:

                        log(
                            "[%s] EPG group %d/%d: stream %s: FETCH - no EPG ID"
                            % (
                                provider.name,
                                number,
                                api_fetch_count,
                                representative
                            )
                        )

                    prefetched = (
                        stalker_worker_results.get(
                            str(representative)
                        )
                    )

                    if prefetched is not None:
                        (
                            prefetched_result_path,
                            result
                        ) = prefetched

                        result_path = prefetched_result_path

                    else:
                        if provider.source_type == "STALKER":
                            result = fetch_stream_stalker_inprocess(
                                stalker_client,
                                stalker_reader,
                                provider,
                                representative
                            )
                        else:
                            _wait_if_paused(provider)    # provider said "slow down"
                            result = run_stream(
                                source_id,
                                representative,
                                result_path
                            )

                    status = result.get(
                        "status",
                        "FAILED"
                    )

                    fetched_entries = result.get(
                        "entries",
                        []
                    )

                    if status == "TIMEOUT":

                        if augmenting:

                            entries = richer_entries

                            log(
                                "[%s] stream %s: TOP-UP TIMEOUT - keeping %d thin programmes - %s"
                                % (
                                    provider.name,
                                    representative,
                                    len(entries),
                                    result.get("error", "")
                                )
                            )

                        else:

                            timeout += len(members)
                            total_timeout += len(members)

                            log(
                                "[%s] stream %s: TIMEOUT - %s"
                                % (
                                    provider.name,
                                    representative,
                                    result.get(
                                        "error",
                                        ""
                                    )
                                )
                            )

                            try:
                                if os.path.exists(result_path):
                                    os.remove(result_path)
                            except Exception:
                                pass

                            continue

                    elif status == "FAILED":

                        if augmenting:

                            entries = richer_entries

                            log(
                                "[%s] stream %s: TOP-UP FAILED - keeping %d thin programmes - %s"
                                % (
                                    provider.name,
                                    representative,
                                    len(entries),
                                    result.get("error", "unknown error")
                                )
                            )

                        else:

                            failed += len(members)

                            log(
                                "[%s] stream %s: FAILED - %s"
                                % (
                                    provider.name,
                                    representative,
                                    result.get(
                                        "error",
                                        "unknown error"
                                    )
                                )
                            )

                            try:
                                if os.path.exists(result_path):
                                    os.remove(result_path)
                            except Exception:
                                pass

                            continue

                    elif not fetched_entries:

                        if augmenting:

                            entries = richer_entries

                            log(
                                "[%s] stream %s: TOP-UP NO DATA - keeping %d thin programmes"
                                % (
                                    provider.name,
                                    representative,
                                    len(entries)
                                )
                            )

                        else:

                            no_data += len(members)

                            if epg_id and len(members) > 1:

                                log(
                                    "[%s] EPG ID %s: NO DATA - %d streams"
                                    % (
                                        provider.name,
                                        epg_id,
                                        len(members)
                                    )
                                )

                            else:

                                log(
                                    "[%s] stream %s: NO DATA"
                                    % (
                                        provider.name,
                                        representative
                                    )
                                )

                            try:
                                if os.path.exists(result_path):
                                    os.remove(result_path)
                            except Exception:
                                pass

                            try:
                                with open(nodata_marker, "w") as marker:
                                    marker.write(str(int(time.time())))
                            except Exception:
                                pass

                            if epg_id:

                                epgid_marker = (
                                    "%s/.nodata_epgid_%d_%s"
                                    % (
                                        CACHE_DIR,
                                        source_id,
                                        epg_id
                                    )
                                )

                                try:
                                    with open(
                                        epgid_marker,
                                        "w"
                                    ) as marker:
                                        marker.write(
                                            str(int(time.time()))
                                        )
                                except Exception:
                                    pass

                            continue

                    else:

                        if augmenting:

                            entries = _epg_merge_entries(
                                richer_entries,
                                fetched_entries
                            )

                            log(
                                "[%s] stream %s: TOPPED UP - %d thin + %d per-stream -> %d merged programmes"
                                % (
                                    provider.name,
                                    representative,
                                    len(richer_entries),
                                    len(fetched_entries),
                                    len(entries)
                                )
                            )

                        else:

                            entries = fetched_entries

                    try:
                        if os.path.exists(result_path):
                            os.remove(result_path)
                    except Exception:
                        pass

                # -----------------------------------------------------
                # CACHE PROGRAMMES AGAINST EVERY STREAM IN THE GROUP
                # -----------------------------------------------------

                group_ok = True

                for target_stream_id in members:

                    cache_path = (
                        "%s/%s_%s.json"
                        % (
                            CACHE_DIR,
                            provider_key,
                            target_stream_id
                        )
                    )

                    target_entries = []

                    for entry in entries:

                        if hasattr(entry, "title"):

                            title = entry.title
                            description = entry.description
                            start_time = entry.start
                            end_time = entry.end

                        else:

                            title = entry.get(
                                "title",
                                ""
                            )

                            description = entry.get(
                                "description",
                                ""
                            )

                            start_time = entry.get(
                                "start",
                                0
                            )

                            end_time = entry.get(
                                "end",
                                0
                            )

                        target_entries.append({
                            "stream_id": str(
                                target_stream_id
                            ),
                            "title": title or "",
                            "description": description or "",
                            "start": int(
                                start_time
                            ),
                            "end": int(
                                end_time
                            )
                        })

                    if not stream_info.get(
                            str(target_stream_id), {}).get("archive", False):
                        target_entries = _trim_past(target_entries)

                    if not target_entries:

                        # The provider only sent programmes that have
                        # already finished (dropped above for channels
                        # without catch-up): no current guide, not a fault.
                        group_ok = False
                        no_data += 1

                        log(
                            "[%s] stream %s: NO_DATA - only past programmes"
                            % (
                                provider.name,
                                target_stream_id
                            )
                        )

                    elif save_entries(
                        cache_path,
                        target_entries
                    ):

                        refreshed += 1

                        if epg_id and len(members) > 1:

                            log(
                                "[%s] stream %s: CACHED from EPG ID %s - %d programmes"
                                % (
                                    provider.name,
                                    target_stream_id,
                                    epg_id,
                                    len(target_entries)
                                )
                            )

                    else:

                        group_ok = False
                        failed += 1

                        log(
                            "[%s] stream %s: FAILED - could not save guide: %s"
                            % (
                                provider.name,
                                target_stream_id,
                                _last_save_error or "unknown error"
                            )
                        )

                if epg_id and len(members) > 1:

                    log(
                        "[%s] EPG ID %s: SUCCESS - %d programmes copied to %d streams"
                        % (
                            provider.name,
                            epg_id,
                            len(entries),
                            len(members)
                        )
                    )

                elif group_ok:

                    log(
                        "[%s] stream %s: SUCCESS - %d programmes"
                        % (
                            provider.name,
                            representative,
                            len(entries)
                        )
                    )

        total_refreshed += refreshed
        total_no_data += no_data
        total_failed += failed

        processed = (
            refreshed +
            no_data +
            failed +
            timeout
        )

        reconciliation_gap = (
            channel_count -
            processed
        )

        provider_seconds = int(
            time.time() - provider_start
        )

        provider_status = "SUCCESS"

        if supplier_failed or failed or timeout:
            provider_status = "FAILED"

        provider_results.append({
            "id": source_id,
            "name": provider.name,
            "channels": channel_count,
            "refreshed": refreshed,
            "no_data": no_data,
            "failed": failed,
            "timeout": timeout,
            "processed": processed,
            "reconciliation_gap": reconciliation_gap,
            "seconds": provider_seconds,
            "api_fetches": api_fetch_count,
            "shared_streams": shared_stream_count,
            "status": provider_status
        })

        log(
            "[%s] DONE: channels=%d processed=%d refreshed=%d no_data=%d failed=%d timeout=%d gap=%d duration=%ds"
            % (
                provider.name,
                channel_count,
                processed,
                refreshed,
                no_data,
                failed,
                timeout,
                reconciliation_gap,
                provider_seconds
            )
        )

        _progress_provider_done(provider_results[-1])
        _release_provider_lock(provider)

    duration = int(
        time.time() - job_start
    )

    overall_failed = (
        total_failed +
        total_timeout
    )

    status = (
        "SUCCESS"
        if overall_failed == 0
        else "COMPLETED WITH ERRORS"
    )

    log("")
    log("========================================")
    log("EPG PROVIDER FINAL SUMMARY")
    log("========================================")

    for result in provider_results:

        log(
            "%-20s channels=%-5d processed=%-5d refreshed=%-5d no_data=%-5d failed=%-5d timeout=%-5d gap=%-5d fetches=%-5d saved=%-5d status=%s"
            % (
                result["name"][:20],
                result["channels"],
                result["processed"],
                result["refreshed"],
                result["no_data"],
                result["failed"],
                result["timeout"],
                result["reconciliation_gap"],
                result["api_fetches"],
                result["shared_streams"],
                result["status"]
            )
        )

    log("========================================")
    log(
        "ALL DONE: refreshed=%d no_data=%d failed=%d timeout=%d"
        % (
            total_refreshed,
            total_no_data,
            total_failed,
            total_timeout
        )
    )

    log("Channels attempted: %d" % total_channels)
    log("EPG API fetches: %d" % total_api_fetches)
    log(
        "API fetches avoided by shared EPG IDs: %d"
        % total_shared_streams
    )
    log("Providers attempted: %d" % len(provider_results))
    log("Status: %s" % status)
    log("Duration: %d seconds" % duration)
    log("===== EPG NIGHTLY JOB END =====")
    _summary_finish(job_start, provider_results, status)

    # Stand-in guides for channels whose own guide is empty or patchy
    # (epg_fill.py; Settings -> EPG -> Fill Guide Gaps). Never fatal.
    try:
        import epg_fill
        epg_fill.run(lambda m: (log("[fill] " + m), epg_fill._file_log(m)))
    except Exception as ex:
        log("[fill] FAILED: %s: %s" % (type(ex).__name__, ex))

    return 0 if overall_failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("FATAL: interrupted")
        if _JOB_START:
            _summary_finish(_JOB_START, [], "FAILED", "Interrupted")
        sys.exit(2)
    except Exception as ex:
        log(
            "FATAL BATCH CONTROLLER ERROR: %s: %s"
            % (
                type(ex).__name__,
                ex
            )
        )
        if _JOB_START:
            _summary_finish(_JOB_START, [], "FAILED",
                            "%s: %s" % (type(ex).__name__, ex))
        sys.exit(2)
