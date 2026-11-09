# =============================================================================
# E2iPTV Backend
# Category Store — with disk cache so restarts are instant
# =============================================================================

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import json
import os
import time

from .store_base import StoreBase
from .category_reader import CategoryReader
from .category import Category

CACHE_DIR = DATA + "/cache"
CACHE_TTL = 86400  # 24 hours default, overridden per provider


class CategoryStore(StoreBase):

    def __init__(self):
        super(CategoryStore, self).__init__()
        self.reader = CategoryReader()
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
        except Exception:
            pass

    def _cache_path(self, provider_key, cat_type):
        return os.path.join(
            CACHE_DIR, "cats_%s_%s.json" % (
                str(provider_key),
                cat_type
            )
        )

    def _legacy_cache_path(self, provider_id, cat_type):
        return os.path.join(
            CACHE_DIR, "cats_%s_%s.json" % (
                str(provider_id),
                cat_type
            )
        )

    def _load_disk_cache(self, provider, cat_type, ttl=CACHE_TTL):

        provider_key = getattr(provider, "provider_key", "")

        if not provider_key:
            return None

        path = self._cache_path(
            provider_key,
            cat_type
        )

        try:
            if time.time() - os.path.getmtime(path) > ttl:
                return None

            with open(path, "r") as f:
                data = json.load(f)

            cats = []

            for d in data:

                c = Category()

                c.source_id     = d.get("source_id", 0)
                c.source_type   = d.get("source_type", "XTREAM")
                c.category_id   = d.get("category_id", "")
                c.category_name = d.get("category_name", "")
                c.parent_id     = d.get("parent_id", 0)
                c.category_type = d.get(
                    "category_type",
                    cat_type.upper()
                )
                c.hidden = bool(
                    d.get("hidden", False)
                )

                cats.append(c)

            return cats

        except Exception:
            return None

    def _save_disk_cache(self, provider, cat_type, cats):

        try:

            provider_key = getattr(provider, "provider_key", "")

            if not provider_key:
                provider_key = getattr(
                    provider,
                    "provider_id",
                    0
                )

            path = self._cache_path(
                provider_key,
                cat_type
            )

            with open(path, "w") as f:

                json.dump([{
                    "source_id":     c.source_id,
                    "source_type":   c.source_type,
                    "category_id":   c.category_id,
                    "category_name": c.category_name,
                    "parent_id":     c.parent_id,
                    "category_type": c.category_type,
                    "hidden":        c.hidden,
                } for c in cats], f)

        except Exception:
            pass

    def _load(self, provider, cat_type, reader_fn):

        ttl = getattr(
            provider,
            'refresh_interval',
            CACHE_TTL
        )

        cached = self._load_disk_cache(
            provider,
            cat_type,
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

            self.clear()
            self.extend(cached)

            return self.count()

        self.clear()

        cats = reader_fn(provider)

        self.extend(cats)

        self._save_disk_cache(
            provider,
            cat_type,
            cats
        )

        return self.count()

    def load_live(self, provider):
        return self._load(
            provider,
            "live",
            self.reader.read_live
        )

    def load_vod(self, provider):
        return self._load(
            provider,
            "vod",
            self.reader.read_vod
        )

    def load_series(self, provider):
        return self._load(
            provider,
            "series",
            self.reader.read_series
        )
