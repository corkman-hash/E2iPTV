# =============================================================================
# E2iPTV Backend
# EPG Store
# =============================================================================

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import json
import os
import time

from .store_base import StoreBase
from .epg import Epg
from .epg_reader import EpgReader

EPG_CACHE_DIR = DATA + "/epg"

# Per-provider EPG offset in seconds (Settings -> provider -> EPG Offset).
# Applied when the guide is READ, not when it's fetched: cached guide
# data (from the screens and the overnight job) is always stored exactly
# as the provider sent it, so changing the offset takes effect at once
# and never needs anything re-downloaded.
_OFFSETS = {}


def set_provider_offset(provider_key, hours):
    try:
        _OFFSETS[str(provider_key)] = int(hours or 0) * 3600
    except (TypeError, ValueError):
        _OFFSETS[str(provider_key)] = 0


def _offset_for_key(key):
    return _OFFSETS.get(str(key).rsplit("_", 1)[0], 0)


_SHIFT_CACHE = {}     # key -> (id of stored list, offset, shifted list)


def _shifted_for(key, entries, offset):
    """_shifted(), but remembered: the guide is redrawn on every key
    press, and copying every programme each time was a large part of
    what made scrolling pause on providers with an offset."""
    if not offset or not entries:
        return entries
    hit = _SHIFT_CACHE.get(key)
    if hit and hit[0] == id(entries) and hit[1] == offset:
        return hit[2]
    out = _shifted(entries, offset)
    if len(_SHIFT_CACHE) > 600:
        _SHIFT_CACHE.clear()
    _SHIFT_CACHE[key] = (id(entries), offset, out)
    return out


def _shifted(entries, offset):
    if not offset or not entries:
        return entries
    import copy
    out = []
    for e in entries:
        c = copy.copy(e)
        c.start = e.start + offset
        c.end = e.end + offset
        out.append(c)
    return out

# -- stand-in guides (epg_fill.py) ---------------------------------------------
# Channels with an empty or patchy guide can have a stand-in guide, taken
# from the same channel at another provider or from an XMLTV guide file.
# They're kept apart from the providers' own (epg/fill/), listed in
# epg/fill/index.json, and put together with the own guide here when read:
# the provider's own programmes always win; the stand-in only fills the
# times nothing is listed. Stored with any EPG offset already applied.

FILL_DIR = EPG_CACHE_DIR + "/fill"
_FILL = {"checked": 0.0, "mtime": None, "keys": frozenset(), "data": {}}
_MERGED = {}


def _fill_keys():
    """Keys with a stand-in guide (index re-checked at most every 30 s)."""
    now = time.time()
    if now - _FILL["checked"] > 30:
        _FILL["checked"] = now
        try:
            m = os.path.getmtime(FILL_DIR + "/index.json")
        except OSError:
            m = None
        if m != _FILL["mtime"]:
            keys = frozenset()
            if m is not None:
                try:
                    with open(FILL_DIR + "/index.json") as f:
                        keys = frozenset(json.load(f).get("keys") or ())
                except Exception:
                    keys = frozenset()
            _FILL.update(mtime=m, keys=keys, data={}, manual=set())
            _MERGED.clear()
    return _FILL["keys"]


def _fill_for(key):
    if key not in _fill_keys():
        return None
    hit = _FILL["data"].get(key)
    if hit is None:
        hit = []
        sid = str(key).rsplit("_", 1)[-1]
        try:
            with open(os.path.join(FILL_DIR, "%s.json" % key)) as f:
                doc = json.load(f)
            rows = doc.get("programmes") or []
            if doc.get("manual"):
                _FILL["manual"] = _FILL.get("manual", set()) | {key}
            for d in rows:
                e = Epg()
                e.stream_id = sid
                e.title = d.get("title", "")
                e.description = d.get("description", "")
                e.start = int(d.get("start", 0))
                e.end = int(d.get("end", 0))
                if e.start < e.end:
                    hit.append(e)
            hit.sort(key=lambda x: x.start)
        except Exception:
            hit = []
        _FILL["data"][key] = hit
    return hit


def _merged(key, own, fill):
    """The own guide, with the stand-in's programmes in its empty times."""
    if not fill:
        return own
    if not own:
        return fill
    hit = _MERGED.get(key)
    if hit and hit[0] is own and hit[1] is fill:
        return hit[2]
    import bisect
    spans = sorted((e.start, e.end) for e in own)
    starts = [a for a, _b in spans]
    out = list(own)
    for f in fill:
        i = bisect.bisect_left(starts, f.end)       # own programmes starting before f ends
        clash = False
        j = i - 1
        while j >= 0 and j >= i - 3:                # nearest few are enough
            if spans[j][1] > f.start:
                clash = True
                break
            j -= 1
        if not clash:
            out.append(f)
    out.sort(key=lambda x: x.start)
    if len(_MERGED) > 600:
        _MERGED.clear()
    _MERGED[key] = (own, fill, out)
    return out


# How long a "confirmed no data" result is trusted before checking
# again. Without this, a channel the portal genuinely has nothing for
# gets a fresh, live network call every single time the grid screen
# displays it — has_for() had no way to tell "already checked, there
# really is nothing" apart from "never checked yet". Shorter than the
# overnight job's own 24-hour marker (bulk_epg_fetch.py) deliberately
# — this one's driven by someone actively looking at the grid, and a
# portal that was mid-outage earlier in the day recovering later the
# same day should get picked up without waiting for tomorrow's job.
NODATA_TTL = 4 * 3600


class EpgStore(StoreBase):

    def __init__(self):
        super(EpgStore, self).__init__()
        self._ref_map = None
        try:
            os.makedirs(EPG_CACHE_DIR, exist_ok=True)
        except Exception:
            pass

    def _cache_path(self, key):
        return os.path.join(
            EPG_CACHE_DIR,
            "%s.json" % str(key).replace("/", "_")
        )

    def _nodata_marker_path(self, key):
        return os.path.join(
            EPG_CACHE_DIR,
            ".nodata_screen_%s" % str(key).replace("/", "_")
        )

    def _has_fresh_nodata_marker(self, key):
        path = self._nodata_marker_path(key)
        try:
            return (time.time() - os.path.getmtime(path)) < NODATA_TTL
        except Exception:
            return False

    def _write_nodata_marker(self, key):
        try:
            with open(self._nodata_marker_path(key), "w") as f:
                f.write(str(int(time.time())))
        except Exception:
            pass

    def _composite_key(self, channel):
        provider_key = str(
            getattr(channel, "provider_key", "") or ""
        ).strip()

        if not provider_key:
            provider_key = str(
                channel.source_id
            )

        return "%s_%s" % (
            provider_key,
            channel.stream_id
        )

    def _load_disk_cache(self, key):
        path = self._cache_path(key)

        try:
            with open(path, "r") as f:
                data = json.load(f)

            if not isinstance(data, list):
                return []

            entries = []

            for d in data:
                e = Epg()
                e.stream_id = str(d.get("stream_id", ""))
                e.title = d.get("title", "")
                e.description = d.get("description", "")
                e.start = int(d.get("start", 0))
                e.end = int(d.get("end", 0))

                if e.start and e.end and e.start < e.end:
                    entries.append(e)

            return sorted(entries, key=lambda x: x.start)

        except Exception:
            return []

    def _save_disk_cache(self, key, entries):
        path = self._cache_path(key)
        tmp = path + ".tmp"

        data = []

        for e in entries:
            data.append({
                "stream_id": str(getattr(e, "stream_id", "")),
                "title": getattr(e, "title", ""),
                "description": getattr(e, "description", ""),
                "start": int(getattr(e, "start", 0)),
                "end": int(getattr(e, "end", 0))
            })

        try:
            with open(tmp, "w") as f:
                json.dump(data, f)

            os.replace(tmp, path)

        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def _load_key(self, key):
        key = str(key)

        if self.has(key):
            return self.get(key)

        entries = self._load_disk_cache(key)

        if entries:
            self.put(key, entries)

        return entries

    def load(self, provider, channel):
        """
        Fetch EPG from the provider when no persistent cache exists,
        then save it to the persistent disk cache.
        """

        key = self._composite_key(channel)

        entries = EpgReader().read(provider, channel)

        # No catch-up on this channel: past programmes can't be played,
        # so keep only from the start of today (less disk and memory).
        if entries and not getattr(channel, "tv_archive", False):
            t = time.localtime()
            cut = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))
            entries = [e for e in entries if e.end > cut]

        if entries:
            self.put(key, entries)
            self._save_disk_cache(key, entries)
        else:
            # Remember that this came back genuinely empty — see
            # NODATA_TTL's own comment for why, otherwise this exact
            # network call repeats every time the grid shows this
            # channel again.
            self._write_nodata_marker(key)

        return len(entries)

    def get_for(self, channel):
        """Return EPG for a channel from memory or persistent disk cache,
        with the provider's EPG offset applied."""
        key = self._composite_key(channel)
        own = _shifted_for(key, self._load_key(key), _offset_for_key(key))
        fill = _fill_for(key)
        if fill and key in _FILL.get("manual", ()):
            # your own choice (Guide from...): it comes first, the
            # provider's programmes only fill any times it leaves empty
            return _merged("m:" + key, fill, own)
        return _merged(key, own, fill)

    @staticmethod
    def _is_current(entries, offset=0):
        """True if at least one programme hasn't finished yet. A cache
        whose programmes have all ended is as good as no cache: before
        this check, has_for() treated any non-empty file as \"have
        it\", so once a provider's cached guide ran out (e.g. the
        overnight job didn't refresh it) the plugin never asked the
        server again and the guide stayed blank."""
        now = time.time()
        for e in entries or []:
            if getattr(e, "end", 0) + offset > now:
                return True
        return False

    def has_for(self, channel):
        """Return whether CURRENT in-memory or on-disk EPG exists, OR a
        still-fresh confirmation that this channel genuinely has
        none — either way, nothing more needs fetching right now.
        Cached data whose programmes have all ended counts as missing,
        so it gets fetched again."""
        key = self._composite_key(channel)

        off = _offset_for_key(key)
        if self.has(key) and self._is_current(self.get(key), off):
            return True

        cached = self._load_disk_cache(key)
        if self._is_current(cached, off):
            self.put(key, cached)
            return True

        return self._has_fresh_nodata_marker(key)
