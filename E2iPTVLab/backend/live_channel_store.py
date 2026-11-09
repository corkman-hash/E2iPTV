# =============================================================================
# E2iPTV Backend
# Live Channel Store — with disk cache so restarts are instant
# =============================================================================

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import json
import os
import time

from .store_base import StoreBase
from .live_channel_reader import LiveChannelReader
from .live_channel import LiveChannel

CACHE_DIR = DATA + "/cache"
CACHE_TTL = 604800  # 7 days default, overridden per provider


class LiveChannelStore(StoreBase):

    def __init__(self):
        super(LiveChannelStore, self).__init__()
        self.reader = LiveChannelReader()
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
        except Exception:
            pass

    def _cache_path(self, key):
        return os.path.join(
            CACHE_DIR,
            "ch_%s.json" % str(key).replace("/", "_")
        )

    def _load_disk_cache(self, category_key, ttl=CACHE_TTL):

        path = self._cache_path(category_key)

        try:
            if time.time() - os.path.getmtime(path) > ttl:
                return None

            with open(path, "r") as f:
                data = json.load(f)

            channels = []

            for d in data:

                c = LiveChannel()

                c.source_id           = d.get("source_id", 0)
                c.provider_key        = d.get("provider_key", "")
                c.category_id         = d.get("category_id", "")
                c.stream_id           = str(d.get("stream_id", ""))
                c.name                = d.get("name", "")
                c.stream_type         = d.get("stream_type", "live")
                c.epg_channel_id      = d.get("epg_channel_id", "")
                c.stream_icon         = d.get("stream_icon", "")
                c.tv_archive          = bool(d.get("tv_archive", False))
                c.tv_archive_duration = int(
                    d.get("tv_archive_duration", 0)
                )
                c.num                 = int(d.get("num", 0))

                # Stalker's own playback URL.
                c.direct_source       = d.get("direct_source", "")

                channels.append(c)

            return channels

        except Exception:
            return None

    def _save_disk_cache(self, category_key, channels):

        if not channels:
            return

        named = sum(
            1 for c in channels
            if c.name.strip()
        )

        path = self._cache_path(category_key)

        # If a stable cache already exists and contains more named
        # channels, keep the better existing cache.
        if os.path.exists(path):

            try:

                with open(path, "r") as f:
                    existing = json.load(f)

                existing_named = sum(
                    1 for c in existing
                    if c.get("name", "").strip()
                )

                if existing_named > named:
                    return

            except Exception:
                pass

        try:

            with open(path, "w") as f:

                json.dump([{
                    "source_id":           c.source_id,
                    "provider_key":        c.provider_key,
                    "category_id":         c.category_id,
                    "stream_id":           c.stream_id,
                    "name":                c.name,
                    "stream_type":         c.stream_type,
                    "epg_channel_id":      c.epg_channel_id,
                    "stream_icon":         c.stream_icon,
                    "tv_archive":         c.tv_archive,
                    "tv_archive_duration": c.tv_archive_duration,
                    "num":                 c.num,
                    "direct_source":       c.direct_source,
                } for c in channels], f)

        except Exception:
            pass

    def _composite_key(self, category):
        return "%s_%s" % (
            category.source_id,
            category.category_id
        )

    def _disk_key(self, provider, category):

        provider_key = getattr(
            provider,
            "provider_key",
            ""
        )

        if not provider_key:
            return self._composite_key(category)

        return "%s_%s" % (
            provider_key,
            category.category_id
        )

    def load(self, provider, category):

        runtime_key = self._composite_key(category)

        disk_key = self._disk_key(
            provider,
            category
        )

        ttl = getattr(
            provider,
            "refresh_interval",
            CACHE_TTL
        )

        cached = self._load_disk_cache(
            disk_key,
            ttl
        )

        if cached is not None:

            # Saved copies carry the provider's position number from when
            # they were saved; after playlists.txt is reordered that's
            # another provider's number, so lists could get mixed up.
            # Always stamp the provider's current number.
            for _x in cached:
                try:
                    _x.source_id = provider.provider_id
                except Exception:
                    pass

            self.put(
                runtime_key,
                cached
            )

            return len(cached)

        channels = self.reader.read(
            provider,
            category
        )

        self.put(
            runtime_key,
            channels
        )

        self._save_disk_cache(
            disk_key,
            channels
        )

        return len(channels)

    def find_on_disk(self, provider, stream_id):
        """A channel from this provider's saved channel lists on disk
        (any category, up to 30 days old) — for recordings that come due
        before the plugin has loaded that category since the box started."""
        key = str(getattr(provider, "provider_key", "") or "")
        if not key:
            return None
        stream_id = str(stream_id)
        prefix = "ch_%s_" % key
        try:
            names = [n for n in os.listdir(CACHE_DIR)
                     if n.startswith(prefix) and n.endswith(".json")]
        except OSError:
            return None
        for n in names:
            for c in self._load_disk_cache(n[3:-5], ttl=30 * 86400) or []:
                if c.stream_id == stream_id:
                    return c
        return None

    def find_by_stream_id(self, source_id, stream_id):

        stream_id = str(stream_id)

        for channels in self.cache.values():

            for channel in channels:

                if (
                    channel.source_id == source_id
                    and
                    str(channel.stream_id) == stream_id
                ):
                    return channel

        return None
