# =============================================================================
# E2iPTV Backend — My Bouquets
#
# Your own Live TV channel lists, made from channels of any provider.
# Shown in Live TV's B2 as "My Bouquets", with each bouquet as one of its
# categories.
#
# Each bouquet is one file:
#     /hdd/e2iptv_bouquets/<bouquet name>.json
# holding a list of channel references (provider key + stream ID, plus
# what's needed to play it without loading that provider's lists). The
# order of the list is the order shown.
#
# A bouquet channel is always played, and its guide fetched, through its
# OWN provider: every bouquet channel carries `_bq_provider`, and
# real_provider() swaps it in wherever a provider and channel are used
# together (stream addresses, EPG, the OK menu). EPG data and picons are
# keyed by the real provider, so they are shared with that provider.
#
# If a channel's provider is no longer in the playlists, or the provider
# has dropped the channel itself, it stays in the bouquet, greyed out,
# until you remove it. A dropped channel is spotted from the provider's
# own saved channel lists (/hdd/e2iptv_cache/ch_<provider>_<category>.json),
# so no server requests are made just to check.
# =============================================================================

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import json
import os
import re
import threading

BOUQUET_DIR = DATA + "/bouquets"
BOUQUET_PROVIDER_KEY = "bouquets"

_lock = threading.Lock()

# Channel fields saved per entry — enough to play and show it.
_FIELDS = ("stream_id", "name", "direct_source", "stream_icon",
           "epg_channel_id", "tv_archive", "tv_archive_duration",
           "stream_type", "custom_sid")


class BouquetProvider(object):
    """Stands in for a provider in Live TV's B2 so the bouquets can be
    opened and browsed exactly like a provider's categories."""

    is_bouquet = True

    def __init__(self):
        self.name = "My Bouquets"
        self.provider_key = BOUQUET_PROVIDER_KEY
        self.provider_id = -1
        self.source_id = BOUQUET_PROVIDER_KEY
        self.source_type = "BOUQUET"
        self.enabled = True
        self.online = True
        self.epg_offset = 0
        self.base_url = ""
        self.username = ""
        self.password = ""
        self.mac = ""
        self.token = None
        self.server_timestamp = 1          # never needs "connecting"
        self.live_categories_loaded = True
        self.refresh_interval = 0
        self.disabled_cats = set()

    def build_provider_key(self):
        return self.provider_key


def is_bouquet(provider):
    return bool(getattr(provider, "is_bouquet", False))


def real_provider(provider, channel):
    """The provider a channel actually belongs to: its own provider for a
    bouquet channel, otherwise `provider` unchanged."""
    real = getattr(channel, "_bq_provider", None)
    return real if real is not None else provider


# -----------------------------------------------------------------------------
# Files
# -----------------------------------------------------------------------------

def _safe(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name or "")).strip(" .")
    return name[:60]


def _path(name):
    return os.path.join(BOUQUET_DIR, _safe(name) + ".json")


def list_bouquets():
    """Bouquet names, alphabetically."""
    try:
        names = [n[:-5] for n in os.listdir(BOUQUET_DIR) if n.endswith(".json")]
    except OSError:
        return []
    return sorted(names, key=lambda n: n.casefold())


def _read(name):
    try:
        with open(_path(name)) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write(name, entries):
    if not os.path.isdir(BOUQUET_DIR):
        os.makedirs(BOUQUET_DIR)
    path = _path(name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries, f, indent=1)
    os.rename(tmp, path)


def create_bouquet(name):
    """Create an empty bouquet. Returns the stored name, or None if the
    name is unusable."""
    name = _safe(name)
    if not name:
        return None
    with _lock:
        if not os.path.exists(_path(name)):
            _write(name, [])
    return name


def add_channel(bouquet, provider, channel):
    """Copy a channel into a bouquet (at the bottom). Returns False if it
    is already there."""
    provider = real_provider(provider, channel)
    entry = {"provider_key": str(getattr(provider, "provider_key", "")),
             "provider_name": getattr(provider, "name", "")}
    for f in _FIELDS:
        v = getattr(channel, f, "")
        entry[f] = v if isinstance(v, (int, float, bool)) else ("" if v is None else str(v))
    # The provider category it came from (kept when copying between
    # bouquets), used to notice if the provider later drops it.
    entry["src_category_id"] = str(
        getattr(channel, "_bq_src_cat", None)
        or getattr(channel, "category_id", "") or "")
    with _lock:
        entries = _read(bouquet)
        for e in entries:
            if (e.get("provider_key") == entry["provider_key"]
                    and str(e.get("stream_id")) == entry["stream_id"]):
                return False
        entries.append(entry)
        _write(bouquet, entries)
    return True


# -----------------------------------------------------------------------------
# What B2 / the grid see
# -----------------------------------------------------------------------------

def categories():
    """One Category per bouquet."""
    from .category import Category
    cats = []
    for name in list_bouquets():
        c = Category()
        c.source_id = BOUQUET_PROVIDER_KEY
        c.source_type = "BOUQUET"
        c.category_id = name
        c.category_name = name
        c.category_type = "LIVE"
        cats.append(c)
    return cats


CHANNEL_CACHE_DIR = DATA + "/cache"
_CACHE_TRUST_SECS = 7 * 24 * 3600   # same 7 days the channel store trusts


class _ProviderLists(object):
    """The stream IDs in a provider's saved channel lists, read once per
    bouquet load. Only lists saved in the last 7 days count."""

    def __init__(self, provider_key):
        self.key = provider_key
        self._by_cat = {}
        self._all = None

    def _load(self, cat_id):
        if cat_id not in self._by_cat:
            path = os.path.join(CHANNEL_CACHE_DIR, "ch_%s_%s.json" % (
                self.key, str(cat_id).replace("/", "_")))
            ids = None
            try:
                import time
                if time.time() - os.path.getmtime(path) < _CACHE_TRUST_SECS:
                    with open(path) as f:
                        ids = set(str(d.get("stream_id", "")) for d in json.load(f))
            except Exception:
                ids = None
            self._by_cat[cat_id] = ids
        return self._by_cat[cat_id]

    def find(self, stream_id):
        """Category ID of any saved list holding this channel, or None."""
        if self._all is None:
            prefix = "ch_%s_" % self.key
            try:
                names = [n for n in os.listdir(CHANNEL_CACHE_DIR)
                         if n.startswith(prefix) and n.endswith(".json")]
            except OSError:
                names = []
            self._all = [n[len(prefix):-5] for n in names]
        for cat_id in self._all:
            ids = self._load(cat_id)
            if ids and stream_id in ids:
                return cat_id
        return None

    def status(self, stream_id, src_cat):
        """("ok" | "dropped" | "unknown", category it's now in or None)."""
        if src_cat:
            ids = self._load(src_cat)
            if ids is not None and stream_id in ids:
                return "ok", src_cat
            moved = self.find(stream_id)
            if moved is not None:
                return "ok", moved
            # Only call it dropped if we have a recent list for the
            # category it came from, and it isn't in any other one.
            return ("dropped" if ids is not None else "unknown"), None
        moved = self.find(stream_id)
        return ("ok", moved) if moved is not None else ("unknown", None)


def channels(category, providers):
    """LiveChannel objects for one bouquet, each tied to its own provider.
    `providers` is state.providers ([(name, provider), ...])."""
    from .live_channel import LiveChannel
    by_key = {}
    for _n, p in providers or []:
        if not is_bouquet(p):
            by_key[str(getattr(p, "provider_key", ""))] = p

    out = []
    lists = {}
    backfill = False
    entries = _read(getattr(category, "category_id", ""))
    for e in entries:
        ch = LiveChannel()
        for f in _FIELDS:
            if f in e:
                setattr(ch, f, e[f])
        ch.stream_id = str(e.get("stream_id", ""))
        ch.provider_key = str(e.get("provider_key", ""))
        ch.category_id = getattr(category, "category_id", "")
        real = by_key.get(ch.provider_key)
        src_cat = str(e.get("src_category_id", "") or "")
        ch._bq_src_cat = src_cat
        if real is not None and getattr(real, "enabled", True) \
                and getattr(real, "show_live", True):
            pl = lists.setdefault(ch.provider_key, _ProviderLists(ch.provider_key))
            status, now_cat = pl.status(ch.stream_id, src_cat)
            if now_cat and now_cat != src_cat:
                e["src_category_id"] = now_cat      # moved, or older entry
                ch._bq_src_cat = now_cat
                backfill = True
        else:
            status = "provider_gone"
        if status in ("ok", "unknown"):
            ch._bq_provider = real
            ch.source_id = getattr(real, "source_id", 0)
            ch.source_type = getattr(real, "source_type", "XTREAM")
        elif status == "dropped":
            # The provider no longer lists this channel: keep it, greyed out.
            ch._bq_provider = None
            ch._bq_missing = True
            ch.name = "%s  (no longer on %s)" % (
                ch.name, e.get("provider_name") or "provider")
        else:
            # Provider removed (or switched off): keep it, greyed out.
            ch._bq_provider = None
            ch._bq_missing = True
            ch.name = "%s  (%s - unavailable)" % (
                ch.name, e.get("provider_name") or "provider")
        out.append(ch)
    if backfill:
        try:
            with _lock:
                _write(getattr(category, "category_id", ""), entries)
        except Exception:
            pass
    return out


def ensure_in_state(state):
    """Add the My Bouquets entry to the provider list once at least one
    bouquet exists (and only once)."""
    if not list_bouquets():
        return False
    for _n, p in state.providers or []:
        if is_bouquet(p):
            return False
    bp = BouquetProvider()
    state.providers.append((bp.name, bp))
    return True


def real_providers(providers):
    """state.providers without the My Bouquets entry — for screens that
    deal with actual providers only (settings, search, the player list)."""
    return [(n, p) for n, p in (providers or []) if not is_bouquet(p)]


def remove_channel(bouquet, channel):
    """Take a channel out of a bouquet (it stays in its provider)."""
    key = str(getattr(channel, "provider_key", ""))
    sid = str(getattr(channel, "stream_id", ""))
    with _lock:
        entries = [e for e in _read(bouquet)
                   if not (str(e.get("provider_key")) == key
                           and str(e.get("stream_id")) == sid)]
        _write(bouquet, entries)


def delete_bouquet(name):
    """Delete a whole bouquet. Its channels stay in their own providers."""
    with _lock:
        try:
            os.remove(_path(name))
        except OSError:
            pass


def remove_from_state_if_empty(state):
    """Take My Bouquets out of the provider list once the last bouquet
    has been deleted. Returns the index it had, or None."""
    if list_bouquets():
        return None
    for i, (_n, p) in enumerate(state.providers or []):
        if is_bouquet(p):
            del state.providers[i]
            return i
    return None


def reorder(bouquet, channels):
    """Save a bouquet in the order of `channels` (EPG grid Move Channel).
    Any entry not in `channels` keeps its place at the end."""
    key = lambda pk, sid: (str(pk), str(sid))
    with _lock:
        entries = _read(bouquet)
        by_key = dict((key(e.get("provider_key"), e.get("stream_id")), e) for e in entries)
        out = []
        for c in channels:
            e = by_key.pop(key(getattr(c, "provider_key", ""), getattr(c, "stream_id", "")), None)
            if e is not None:
                out.append(e)
        out.extend(e for e in entries
                   if key(e.get("provider_key"), e.get("stream_id")) in by_key)
        _write(bouquet, out)
