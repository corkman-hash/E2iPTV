# =============================================================================
# IE2iPTV Backend
# Stalker / Ministra Portal Client
#
# A Stalker portal authenticates by MAC address rather than username/
# password (see Provider.mac) — the box does a "handshake" that
# mimics a real MAG/Stalker STB (specific Cookie/User-Agent headers a
# genuine set-top box would send), gets back a session token, then
# every further call carries that token. Unlike Xtream Codes, a
# Stalker portal never hands back a directly-playable stream URL up
# front — that's a separate, per-channel "create_link" call, done at
# actual play time (see resolve_stream_url below), not when the
# channel list itself loads.
#
# This deliberately mirrors this project's own, established Xtream
# reader shape (see live_channel_reader.py) as closely as the two
# protocols allow, so the rest of the app — UI, EPG, playback — never
# needs to know or care which protocol a given provider is actually
# using.
# =============================================================================

import json
import threading
import time
import re

try:
    import urllib2
    from urllib2 import Request, urlopen, HTTPError, URLError
except ImportError:
    import urllib.request as urllib2
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

try:
    from urllib import quote as _urlquote
except ImportError:
    from urllib.parse import quote as _urlquote


# Headers deliberately chosen to match a real MAG250 STB's own
# handshake — portals commonly reject anything that doesn't look like
# genuine set-top box firmware, not just anything with a valid MAC.
_USER_AGENT = (
    "Mozilla/5.0 (QtEmbedded; U; Linux; C) "
    "AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 "
    "Safari/533.3"
)
_STB_TYPE = "MAG250"

# How long a handshake token is trusted for before this proactively
# re-does the handshake rather than waiting for a call to actually be
# rejected — portals vary in how long a token actually stays valid, so
# this errs low rather than risk a wave of stale-token failures
# partway through, say, loading a long channel list.
_TOKEN_LIFETIME = 600  # seconds


# Serialises handshakes inside this process. Without it, the EPG
# prefetch threads and the player can all decide the token is stale at
# the same moment and each handshake in turn, every new token
# invalidating the one the previous thread (or the player) just got.
_TOKEN_LOCK = threading.Lock()          # guards _TOKEN_LOCKS only
_TOKEN_LOCKS = {}
_HANDSHAKE_BACKOFF = 60


def _token_lock_for(provider):
    """One lock per portal, so a slow or dead portal only holds up its
    own requests, never every Stalker provider's."""
    key = (getattr(provider, "base_url", ""), getattr(provider, "mac", ""))
    with _TOKEN_LOCK:
        lk = _TOKEN_LOCKS.get(key)
        if lk is None:
            lk = _TOKEN_LOCKS[key] = threading.Lock()
        return lk

# Hard ceiling on EPG pages fetched per channel per day.
_EPG_MAX_PAGES_PER_DAY = 15


class StalkerError(Exception):
    pass


def _host_root(base_url):
    """Strips any path segment (e.g. the /c in
    http://host/c) down to just scheme://host[:port] — see
    handshake's own docstring for why this exists."""
    m = re.match(r"^(https?://[^/]+)", base_url)
    return m.group(1) if m else ""


def _request(url, mac, token=None, timeout=5):
    headers = {
        "User-Agent": _USER_AGENT,
        "X-User-Agent": "Model: %s; Link: WiFi" % _STB_TYPE,
        "Cookie": "mac=%s; stb_lang=en; timezone=Europe/London" % mac,
        "Accept": "*/*",
    }
    if token:
        headers["Authorization"] = "Bearer %s" % token

    req = Request(url, headers=headers)
    resp = urlopen(req, timeout=timeout)
    raw = resp.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return json.loads(raw)


def handshake(provider, timeout=5):
    """Performs the initial handshake, returning a fresh session
    token. Raises StalkerError with a plain-English reason on
    failure — the portal being down, the MAC being rejected, or the
    response not looking like a genuine Stalker portal at all are all
    distinguished here rather than collapsed into one generic
    failure, since which one it is changes what the user actually
    needs to do about it.

    Takes the provider object itself (rather than base_url/mac as
    separate strings) because Stalker portals vary in whether their
    API actually lives under the same path segment as the playlist
    URL (one portal genuinely needs .../c/server/load.php,
    matching its playlist URL's own /c/ — but a real second portal
    tested against this used /c/ only for its playlist URL, with its
    actual API at the host root instead, 404ing under /c/ the exact
    same way an actually-wrong URL would). A single fixed assumption
    either way breaks one portal or the other, so this tries the
    given base_url first and, only on a 404 specifically (not other
    failures — those are genuine problems, not a path mismatch),
    retries at the host root and adopts that as the provider's
    base_url going forward once confirmed, rather than guessing at
    every call after this one too."""

    base_url = provider.base_url
    mac = provider.mac

    url = (
        "%s/server/load.php?type=stb&action=handshake&token=&JsHttpRequest=1-xml"
        % base_url
    )

    def _try_root_fallback(reason):
        root_url = _host_root(base_url)
        if not root_url or root_url == base_url:
            return None
        fallback_url = (
            "%s/server/load.php?type=stb&action=handshake"
            "&token=&JsHttpRequest=1-xml" % root_url
        )
        try:
            result = _request(fallback_url, mac, timeout=timeout)
            # Confirmed working — adopt it for every further call on
            # this provider (categories, channels, create_link), not
            # just this one handshake.
            provider.base_url = root_url
            return result
        except Exception:
            return None

    try:
        data = _request(url, mac, timeout=timeout)
    except HTTPError as e:
        # A 404 and a 200-with-empty/invalid-body (caught below as a
        # ValueError) are both treated as "this exact path doesn't
        # work" and get the same fallback attempt — a portal tested
        # against this returned a clean 404 on one request and an
        # empty 200 body on another for the same wrong path, so
        # treating only the 404 as fallback-worthy left the fallback
        # not even attempted half the time.
        data = _try_root_fallback(e.code)
        if data is None:
            raise StalkerError(
                "Portal returned HTTP %s for %s — check the portal URL"
                % (e.code, url)
            )
    except ValueError:
        data = _try_root_fallback("invalid response")
        if data is None:
            raise StalkerError(
                "Portal did not return valid data for %s — check the "
                "portal URL is correct" % url
            )
    except URLError as e:
        raise StalkerError("Could not reach portal: %s" % e.reason)

    token = data.get("js", {}).get("token")
    if not token:
        raise StalkerError(
            "Portal accepted the connection but rejected this MAC address"
        )

    return token


def get_profile(base_url, mac, token, timeout=5):
    """Registers the device against the token — some portals require
    this immediately after handshake before any content call will
    succeed; others accept content calls without it. Called once per
    fresh handshake defensively either way; failure here is logged by
    the caller but deliberately never treated as fatal on its own,
    since a portal that skips this step entirely is not misbehaving,
    just built differently."""

    url = (
        "%s/server/load.php?type=stb&action=get_profile"
        "&hd=1&ver=ImageDescription:%%200.0.0;ImageDate:%%20Mon%%20Jan%%201%%20000000%%202024;"
        "&num_banks=2&sn=&stb_type=%s&client_type=STB&image_version=218"
        "&video_out=hdmi&device_id=&device_id2=&signature=&auth_second_step=0"
        "&hw_version=1.7-BD-00&not_valid_token=0&metrics=&hw_version_2=&api_signature=262"
        "&JsHttpRequest=1-xml" % (base_url, _STB_TYPE)
    )
    return _request(url, mac, token, timeout=timeout)


class StalkerClient(object):
    """One instance per provider — holds the session token and
    refreshes it transparently. All the actual per-item-type readers
    (live_channel_reader.py, etc.) should go through this rather than
    building their own requests, so the handshake/retry logic only
    ever lives in one place."""

    def __init__(self, provider):
        self.provider = provider

    def _ensure_token(self, force=False, failed_token=None):
        p = self.provider
        # A portal that just failed to log in is not asked again for a
        # minute: every request fails at once instead of each one
        # waiting on another slow handshake in turn.
        failed_at = getattr(p, "_handshake_failed_at", 0)
        if time.time() - failed_at < _HANDSHAKE_BACKOFF and not p.token:
            raise IOError("portal login failed recently - retrying in a minute")
        with _token_lock_for(p):
            if time.time() - getattr(p, "_handshake_failed_at", 0) < _HANDSHAKE_BACKOFF and not p.token:
                raise IOError("portal login failed recently - retrying in a minute")
            age = time.time() - getattr(p, "_token_time", 0)
            if force and failed_token and p.token and p.token != failed_token:
                # Another thread already re-handshook since our call
                # failed — use its token rather than invalidating it
                # with yet another handshake.
                return p.token
            if force or not p.token or age > _TOKEN_LIFETIME:
                try:
                    tok = handshake(p)
                except Exception:
                    tok = None
                if not tok:
                    p.token = None
                    p._handshake_failed_at = time.time()
                    raise IOError("portal login failed")
                p.token = tok
                p._handshake_failed_at = 0
                p._token_time = time.time()
                try:
                    get_profile(p.base_url, p.mac, p.token)
                except Exception:
                    # Deliberately non-fatal — see get_profile's own
                    # docstring for why.
                    pass
            return p.token

    def _call(self, action_url, retry_on_auth_fail=True, retry_on_empty=True,
              retry_on_root=True):
        p = self.provider
        token = self._ensure_token()
        try:
            return _request(action_url, p.mac, token)
        except HTTPError as e:
            if e.code in (401, 403) and retry_on_auth_fail:
                # Token genuinely expired/was revoked mid-session —
                # one forced re-handshake and retry, not an infinite
                # loop if the portal is rejecting this MAC outright.
                self._ensure_token(force=True, failed_token=token)
                return self._call(
                    action_url, retry_on_auth_fail=False,
                    retry_on_empty=retry_on_empty,
                    retry_on_root=retry_on_root
                )
            if e.code == 404 and retry_on_root:
                # Some portals accept the handshake action at the
                # playlist URL's own path but 404 other actions at
                # that exact same path (get_genres, get_channels,
                # etc. each 404ing there while handshake itself
                # didn't) — confirmed against a real portal doing
                # exactly this. handshake()'s own root-URL fallback
                # only ever protects the handshake call itself, so
                # this repeats the same idea here, generically, for
                # every other action too, and keeps the correction if
                # it works — same as handshake() does.
                retried = self._retry_at_root(action_url, token)
                if retried is not None:
                    return retried
            raise StalkerError(
                "Portal returned HTTP %s for %s" % (e.code, action_url)
            )
        except URLError as e:
            raise StalkerError("Could not reach portal: %s" % e.reason)
        except ValueError:
            # Empty/invalid JSON body on an otherwise-200 response —
            # seen intermittently against a real portal on a URL that
            # succeeds most of the time (unlike a genuinely wrong
            # path, which fails the same way every time), so this is
            # treated as transient: one retry before actually giving
            # up, rather than surfacing every isolated blip as a hard
            # failure the way an uncaught ValueError previously did.
            if retry_on_empty:
                return self._call(
                    action_url, retry_on_auth_fail=retry_on_auth_fail,
                    retry_on_empty=False, retry_on_root=retry_on_root
                )
            raise StalkerError(
                "Portal returned an empty/invalid response for %s"
                % action_url
            )

    def _retry_at_root(self, action_url, token):
        """Re-issues action_url against the host-root variant instead
        — see _call's own comment on why this exists. Returns the
        response on success (and adopts the root URL as the
        provider's base_url going forward), or None if the root
        variant doesn't apply or also fails, leaving the caller to
        raise its normal error.

        Both root_url and the retry URL are derived from action_url
        itself — the actual URL that just failed — rather than from
        the provider's current base_url. That field can already have
        been corrected by a different, unrelated call sharing this
        same provider object in the time between action_url being
        built and this retry running (confirmed happening: an EPG
        fetch's own fallback had already fixed base_url by the time a
        get_genres request's older, still-/c/-prefixed action_url
        reached here, making a comparison against the already-fixed
        base_url wrongly look like there was nothing left to fall
        back to, even though action_url itself was still wrong).

        Forces a fresh handshake at the root URL first rather than
        reusing the token argument — confirmed separately that a
        session token is bound to the exact path it was issued from:
        a token from a /c/-path handshake gets silently ignored
        (empty response, not even a clean auth error) when used
        against the host-root path, even though the host-root path
        itself works fine end-to-end with its own, matching token."""

        def _log(msg):
            # Diagnostic logging removed — this fallback path is
            # already confirmed working; kept as a no-op rather than
            # hunting down every call site individually.
            pass

        root_url = _host_root(action_url)
        marker = "/server/load.php"
        marker_index = action_url.find(marker)

        if not root_url or marker_index == -1:
            _log(
                "SKIPPED action_url=%r root_url=%r"
                % (action_url, root_url)
            )
            return None

        retry_url = root_url + action_url[marker_index:]

        if retry_url == action_url:
            # Already at the host root — nothing left to fall back to.
            _log("SKIPPED already at root: action_url=%r" % action_url)
            return None

        original_base_url = self.provider.base_url
        self.provider.base_url = root_url
        try:
            fresh_token = handshake(self.provider)
        except Exception as e:
            _log("HANDSHAKE AT ROOT FAILED root_url=%r error=%r" % (root_url, e))
            self.provider.base_url = original_base_url
            return None

        self.provider.token = fresh_token
        self.provider._token_time = time.time()

        try:
            result = _request(retry_url, self.provider.mac, fresh_token)
            self.provider.base_url = root_url
            _log("SUCCESS retry_url=%r" % retry_url)
            return result
        except Exception as e:
            _log("RETRY REQUEST FAILED retry_url=%r error=%r" % (retry_url, e))
            self.provider.base_url = original_base_url
            return None

    # ------------------------------------------------------------------

    def get_genres(self):
        """Returns the portal's own category/genre list for Live TV.
        Shape: [{"id": "...", "title": "..."}, ...] — "*" is the
        portal's own "All Channels" pseudo-category, included as-is;
        callers decide whether to surface or filter it out, same as
        any other category."""

        url = "%s/server/load.php?type=itv&action=get_genres&JsHttpRequest=1-xml" % self.provider.base_url
        data = self._call(url)
        return data.get("js", []) or []

    def get_channels(self, genre_id, limit=None):
        """Returns channels in one genre. Stalker portals paginate
        this server-side, unlike Xtream's single-response category
        listing. An optional limit allows callers such as the EPG job
        to stop pagination once enough channels have been collected."""

        p = self.provider
        page = 1
        all_items = []
        # Safety cap — protects against a portal reporting a
        # total_items that pagination can never actually reach (seen
        # as an apparent hang partway through a category-by-category
        # scan, with no way to tell a genuine bug here from the portal
        # itself just being slow, without a hard stop somewhere).
        max_pages = 200
        while page <= max_pages:
            url = (
                "%s/server/load.php?type=itv&action=get_ordered_list"
                "&genre=%s&p=%d&JsHttpRequest=1-xml"
                % (p.base_url, genre_id, page)
            )
            data = self._call(url)
            js = data.get("js", {})
            if not isinstance(js, dict):
                # Some portals return "js": [] rather than {"data":
                # [], ...} for a genuinely empty category — treated as
                # zero results rather than raising, since it's a valid
                # (if differently-shaped) way of saying "nothing here".
                js = {}
            items = js.get("data", []) or []
            all_items.extend(items)

            if limit is not None and len(all_items) >= limit:
                return all_items[:limit]

            total = int(js.get("total_items", len(all_items)) or 0)
            if not items or len(all_items) >= total:
                break
            page += 1

        return all_items

    def get_epg_bulk(self, seed_ch_id, days=1):
        """Return the portal's native multi-channel EPG response.

        Stalker get_epg_info returns a mapping of channel ID to that
        channel's programme list. The seed channel identifies the
        request; the portal determines which channels are included in
        the response.

        This is deliberately generic: no provider name, channel ID or
        portal URL is hard-coded here.
        """
        url = (
            "%s/server/load.php?type=itv&action=get_epg_info"
            "&period=%d&ch_id=%s&JsHttpRequest=1-xml"
            % (
                self.provider.base_url,
                days * 24,
                seed_ch_id
            )
        )

        data = self._call(url)

        by_channel = (
            data.get("js", {}).get("data", {})
        )

        if not isinstance(by_channel, dict):
            return {}

        return by_channel

    def get_epg(self, ch_id, days=1):
        """Return the EPG for the requested number of days.

        get_simple_data_table returns one calendar day's EPG.
        The portal uses p=0 for the first page, p=2 for the second,
        p=3 for the third, etc.; p=1 repeats the first page.
        Duplicate programme rows are removed.

        Pagination is always bounded: it stops at the page count
        implied by total_items, at _EPG_MAX_PAGES_PER_DAY, or as soon
        as a page adds nothing new (portals commonly repeat a page for
        out-of-range p, and duplicate rows mean the de-duplicated count
        can never reach total_items).
        """

        from datetime import datetime, timedelta

        days = max(1, int(days or 1))
        all_items = []
        seen = set()

        def fetch_page(date, page):
            url = (
                "%s/server/load.php?type=epg&action=get_simple_data_table"
                "&ch_id=%s&date=%s&p=%d&JsHttpRequest=1-xml"
                % (self.provider.base_url, ch_id, date, page)
            )
            data = self._call(url)
            js = data.get("js", {}) or {}
            if not isinstance(js, dict):
                return [], 0
            return (
                js.get("data", []) or [],
                int(js.get("total_items", 0) or 0)
            )

        def add_new(items, out):
            added = 0
            for item in items:
                key = (
                    str(item.get("id", "")),
                    str(item.get("time", "")),
                    str(item.get("time_to", "")),
                    str(item.get("name", ""))
                )
                if key in seen:
                    continue
                seen.add(key)
                out.append(item)
                added += 1
            return added

        start_date = datetime.now().date()

        for day_offset in range(days):
            date = (start_date + timedelta(days=day_offset)).strftime("%Y-%m-%d")

            page_items, total_items = fetch_page(date, 0)
            if not page_items:
                continue

            day_items = []
            add_new(page_items, day_items)

            per_page = len(page_items)
            pages_needed = (total_items + per_page - 1) // per_page if total_items else 1
            pages_needed = min(pages_needed, _EPG_MAX_PAGES_PER_DAY)

            fetched = 1
            page = 2
            while fetched < pages_needed:
                page_items, _ = fetch_page(date, page)
                fetched += 1
                if not page_items:
                    break
                if add_new(page_items, day_items) == 0:
                    break          # portal is repeating itself
                if len(page_items) < per_page:
                    break          # short page = last page
                page += 1

            all_items.extend(day_items)

        return all_items

    def search_media(self, content_type, term, limit=500):
        """Search Stalker directly through the portal search endpoint."""

        from urllib.parse import quote
        from concurrent.futures import ThreadPoolExecutor, as_completed

        p = self.provider
        media_type = "vod" if content_type == "movies" else "series"
        query = quote(str(term or ""), safe="")

        def fetch_page(page):
            url = (
                "%s/server/load.php?type=%s"
                "&action=get_ordered_list"
                "&search=%s"
                "&p=%d"
                "&JsHttpRequest=1-xml"
                % (
                    p.base_url,
                    media_type,
                    query,
                    page
                )
            )

            data = self._call(url)
            js = data.get("js", {})

            if not isinstance(js, dict):
                js = {}

            items = js.get("data", []) or []
            total = int(
                js.get("total_items", len(items)) or 0
            )

            return page, items, total

        page, first_items, total = fetch_page(1)

        if not first_items:
            return []

        if limit is not None and len(first_items) >= limit:
            return first_items[:limit]

        per_page = len(first_items)

        if total <= per_page:
            return first_items[:limit]

        max_pages = min(
            200,
            (total + per_page - 1) // per_page
        )

        results = {
            1: first_items
        }

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(
                    fetch_page,
                    page_no
                ): page_no
                for page_no in range(2, max_pages + 1)
            }

            for future in as_completed(futures):
                page_no, items, unused_total = future.result()
                results[page_no] = items

        all_items = []

        for page_no in range(1, max_pages + 1):
            all_items.extend(
                results.get(page_no, [])
            )

        if limit is not None:
            return all_items[:limit]

        return all_items

    def get_vod_categories(self):
        """Returns the portal's own category list for movies (VOD),
        same shape as get_genres: [{"id": "...", "title": "..."}, ...].
        Not yet confirmed against a real portal — see get_vod_items
        and resolve_vod_url's own notes."""

        url = "%s/server/load.php?type=vod&action=get_categories&JsHttpRequest=1-xml" % self.provider.base_url
        data = self._call(url)
        return data.get("js", []) or []

    def get_vod_page(self, category_id, page=1):
        """Return one page of movies from a Stalker VOD category."""

        p = self.provider

        url = (
            "%s/server/load.php?type=vod&action=get_ordered_list"
            "&category=%s&p=%d&JsHttpRequest=1-xml"
            % (p.base_url, category_id, page)
        )

        data = self._call(url)
        js = data.get("js", {})
        if not isinstance(js, dict):
            js = {}

        items = js.get("data", []) or []
        total = int(js.get("total_items", len(items)) or 0)

        return items, total

    def get_vod_items(self, category_id, limit=None):
        """Return all movies in one VOD category using parallel pagination.

        The portal returns a fixed number of items per page and exposes
        total_items. Page 1 therefore determines the exact number of
        remaining pages, which can safely be fetched concurrently.
        """

        from concurrent.futures import ThreadPoolExecutor, as_completed

        p = self.provider
        max_pages = 200

        def fetch_page(page):
            url = (
                "%s/server/load.php?type=vod&action=get_ordered_list"
                "&category=%s&p=%d&JsHttpRequest=1-xml"
                % (p.base_url, category_id, page)
            )
            data = self._call(url)
            js = data.get("js", {})
            if not isinstance(js, dict):
                js = {}
            return page, js

        page, first_js = fetch_page(1)
        first_items = first_js.get("data", []) or []
        total = int(first_js.get("total_items", len(first_items)) or 0)

        if limit is not None and len(first_items) >= limit:
            return first_items[:limit]

        if not first_items or total <= len(first_items):
            return first_items[:limit] if limit is not None else first_items

        per_page = len(first_items)
        pages = min(max_pages, (total + per_page - 1) // per_page)

        results = {1: first_items}

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(fetch_page, page_no): page_no
                for page_no in range(2, pages + 1)
            }

            for future in as_completed(futures):
                page_no, js = future.result()
                results[page_no] = js.get("data", []) or []

        all_items = []
        for page_no in range(1, pages + 1):
            all_items.extend(results.get(page_no, []))

        if limit is not None:
            return all_items[:limit]

        return all_items

    def resolve_vod_url(self, cmd):
        """The VOD equivalent of resolve_stream_url — a movie's own
        "cmd" field from get_vod_items is likewise not a directly
        playable URL. Deliberately reuses that same method's cmd-
        encoding approach (spaces only, not a full URL-encode) rather
        than risking the same &/?/: parsing quirk confirmed there."""

        url = (
            "%s/server/load.php?type=vod&action=create_link"
            "&cmd=%s&JsHttpRequest=1-xml"
            % (self.provider.base_url, cmd.replace(" ", "%20"))
        )
        data = self._call(url)
        raw_cmd = data.get("js", {}).get("cmd", "")
        result = raw_cmd
        if result.startswith("ffmpeg "):
            result = result[len("ffmpeg "):]
        return result.strip()

    def get_series_categories(self):
        """Confirmed directly against a real portal — same shape as
        get_vod_categories, just type=series instead of type=vod."""

        url = "%s/server/load.php?type=series&action=get_categories&JsHttpRequest=1-xml" % self.provider.base_url
        data = self._call(url)
        return data.get("js", []) or []

    def get_series_page(self, category_id, page=1):
        """Return one page of Series shows from a Stalker category."""

        p = self.provider

        url = (
            "%s/server/load.php?type=series&action=get_ordered_list"
            "&category=%s&p=%d&JsHttpRequest=1-xml"
            % (p.base_url, category_id, page)
        )

        data = self._call(url)
        js = data.get("js", {})
        if not isinstance(js, dict):
            js = {}

        items = js.get("data", []) or []
        total = int(js.get("total_items", len(items)) or 0)

        return items, total

    def get_series_items(self, category_id, limit=None, search=False):
        """Return every SHOW (not episode) in one series category.

        Normal Series browsing retains the existing 200-page behaviour.
        Search can request a reduced page window so a slow Stalker
        category cannot generate an excessive pagination workload.
        """

        from concurrent.futures import ThreadPoolExecutor, as_completed

        p = self.provider
        max_pages = 20 if search else 200

        def fetch_page(page):
            url = (
                "%s/server/load.php?type=series&action=get_ordered_list"
                "&category=%s&p=%d&JsHttpRequest=1-xml"
                % (p.base_url, category_id, page)
            )
            data = self._call(url)
            js = data.get("js", {})
            if not isinstance(js, dict):
                js = {}
            return page, js

        page, first_js = fetch_page(1)
        first_items = first_js.get("data", []) or []
        total = int(first_js.get("total_items", len(first_items)) or 0)

        if limit is not None and len(first_items) >= limit:
            return first_items[:limit]

        if not first_items or total <= len(first_items):
            return first_items[:limit] if limit is not None else first_items

        per_page = len(first_items)
        pages = min(max_pages, (total + per_page - 1) // per_page)

        results = {1: first_items}

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(fetch_page, page_no): page_no
                for page_no in range(2, pages + 1)
            }

            for future in as_completed(futures):
                page_no, js = future.result()
                results[page_no] = js.get("data", []) or []

        all_items = []
        for page_no in range(1, pages + 1):
            all_items.extend(results.get(page_no, []))

        if limit is not None:
            return all_items[:limit]

        return all_items

    def get_series_seasons(self, series_id):
        """The step between a show and an actual playable episode.
        Confirmed directly against a real portal: type=series&
        action=get_ordered_list&movie_id=<series_id> (not category=)
        returns one item per SEASON, each with its own "series" field
        — a plain list of episode numbers that season has (e.g.
        [1, 2, 3]), and its own "cmd" — needed by resolve_series_url
        alongside one of those episode numbers to get an actual
        playable url. A season's episode list is already complete
        right here; no further per-episode fetch is needed to build
        the episode list itself, only to resolve one to a url at
        actual play time."""

        url = (
            "%s/server/load.php?type=series&action=get_ordered_list"
            "&movie_id=%s&p=1&JsHttpRequest=1-xml"
            % (self.provider.base_url, series_id)
        )
        data = self._call(url)
        js = data.get("js", {})
        if not isinstance(js, dict):
            return []
        return js.get("data", []) or []

    def resolve_series_url(self, episode_ref):
        """episode_ref is "<season_cmd>|<episode_number>" — see
        SeriesInfoReader._read_stalker for where this gets built and
        stored as episode.direct_source. Confirmed directly against a
        real portal: resolving one specific episode is the season's
        own cmd (from get_series_seasons) plus a series=<episode
        number> parameter on the same create_link action movies use —
        not a separate per-episode look-up call. Uses type=vod here,
        not type=series, matching exactly what was confirmed working
        directly against a real portal — type=series was not tried
        for this specific call, so no claim either way about it."""

        try:
            cmd, episode_number = episode_ref.rsplit("|", 1)
        except ValueError:
            raise StalkerError("malformed episode_ref: %r" % episode_ref)

        url = (
            "%s/server/load.php?type=vod&action=create_link"
            "&cmd=%s&series=%s&JsHttpRequest=1-xml"
            % (self.provider.base_url, cmd.replace(" ", "%20"), episode_number)
        )
        data = self._call(url)
        raw_cmd = data.get("js", {}).get("cmd", "")
        result = raw_cmd
        if result.startswith("ffmpeg "):
            result = result[len("ffmpeg "):]
        return result.strip()

    def resolve_stream_url(self, cmd):
        """The one extra step Stalker needs that Xtream doesn't — a
        channel's own "cmd" field from get_channels is NOT a directly
        playable URL, just an internal portal reference. This asks the
        portal to mint an actual, playable stream URL for it, done at
        play time rather than while loading the channel list (unlike
        Xtream, where the M3U already contains a direct link) — matches
        how a real STB only ever resolves a channel at the moment it's
        actually tuned, not all of them up front."""

        # Deliberately NOT a full URL-encode of cmd (only spaces are
        # escaped) — a known Stalker portal quirk: portal backends
        # commonly parse this parameter with their own simple string
        # split rather than a real URL-decode, so fully encoding
        # &/?/: (as %26/%3F/%3A — needed for a technically correct
        # query string) breaks their ability to re-split the URL
        # embedded inside cmd's own value, silently dropping
        # everything after the first &. First real playback test
        # confirmed exactly that: stream= coming back empty in the
        # response despite being genuinely present, unencoded, in the
        # request.
        url = (
            "%s/server/load.php?type=itv&action=create_link"
            "&cmd=%s&JsHttpRequest=1-xml"
            % (self.provider.base_url, cmd.replace(" ", "%20"))
        )
        data = self._call(url)
        raw_cmd = data.get("js", {}).get("cmd", "")
        # Portal responses commonly prefix the real URL with "ffmpeg "
        # (a hint at which local player to use, from the STB's own
        # firmware perspective) — meaningless to us, strip it if present.
        result = raw_cmd
        if result.startswith("ffmpeg "):
            result = result[len("ffmpeg "):]
        result = result.strip()

        return result
