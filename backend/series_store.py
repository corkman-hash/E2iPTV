# =============================================================================
# E2iPTV Backend
# Series Store — with disk cache
# =============================================================================

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import json
import os
import time

from .store_base import StoreBase
from .series_reader import SeriesReader
from .series import Series

CACHE_DIR = DATA + "/cache"
CACHE_TTL = 86400


class SeriesStore(StoreBase):

    def __init__(self):

        super(SeriesStore, self).__init__()

        self.reader = SeriesReader()

        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
        except Exception:
            pass

    # -------------------------------------------------------------

    def _cache_path(self, key):

        return os.path.join(
            CACHE_DIR,
            "series_%s.json" % str(key)
        )

    # -------------------------------------------------------------

    def _load_disk_cache(self, key, ttl=CACHE_TTL):

        path = self._cache_path(key)

        try:

            if time.time() - os.path.getmtime(path) > ttl:
                return None

            with open(path, "r") as f:
                data = json.load(f)

            series_list = []

            for d in data:

                s = Series()

                s.source_id     = d.get("source_id", 0)
                s.source_type   = d.get("source_type", "XTREAM")
                s.category_id   = d.get("category_id", "")
                s.series_id     = d.get("series_id", "")
                s.name          = d.get("name", "")
                s.cover         = d.get("cover", "")
                s.backdrop_path = d.get("backdrop_path", "")
                s.plot          = d.get("plot", "")
                s.genre         = d.get("genre", "")
                s.cast          = d.get("cast", "")
                s.director      = d.get("director", "")
                s.rating        = d.get("rating", "")
                s.rating_5based = d.get("rating_5based", "")
                s.release_date  = d.get("release_date", "")
                s.last_modified = d.get("last_modified", 0)
                s.hidden        = bool(d.get("hidden", False))
                s.favourite     = bool(d.get("favourite", False))

                series_list.append(s)

            return series_list

        except Exception:
            return None

    # -------------------------------------------------------------

    def _save_disk_cache(self, key, series_list):

        try:

            with open(
                self._cache_path(key),
                "w"
            ) as f:

                json.dump([{

                    "source_id":     s.source_id,
                    "source_type":   s.source_type,
                    "category_id":   s.category_id,
                    "series_id":     s.series_id,
                    "name":          s.name,
                    "cover":         s.cover,
                    "backdrop_path": s.backdrop_path,
                    "plot":          s.plot,
                    "genre":         s.genre,
                    "cast":          s.cast,
                    "director":      s.director,
                    "rating":        s.rating,
                    "rating_5based": s.rating_5based,
                    "release_date":  s.release_date,
                    "last_modified": s.last_modified,
                    "hidden":        s.hidden,
                    "favourite":     s.favourite,

                } for s in series_list], f)

        except Exception:
            pass

    # -------------------------------------------------------------

    def load(self, provider, category):

        # Runtime key remains numeric for compatibility.
        runtime_key = "%s_%s" % (
            category.source_id,
            category.category_id
        )

        # Disk identity is tied permanently to the provider.
        provider_key = getattr(
            provider,
            "provider_key",
            ""
        )

        if provider_key:

            disk_key = "%s_%s" % (
                provider_key,
                category.category_id
            )

        else:

            disk_key = runtime_key

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

        series = self.reader.read(
            provider,
            category
        )

        self.put(
            runtime_key,
            series
        )

        self._save_disk_cache(
            disk_key,
            series
        )

        return len(series)
