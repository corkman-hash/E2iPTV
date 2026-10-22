# =============================================================================
# E2iPTV Backend
# Movie Store — with disk cache so restarts are instant
# =============================================================================

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import json
import os
import time

from .store_base import StoreBase
from .movie_reader import MovieReader
from .movie import Movie

CACHE_DIR = DATA + "/cache"
CACHE_TTL = 86400


class MovieStore(StoreBase):

    def __init__(self):

        super(MovieStore, self).__init__()

        self.reader = MovieReader()

        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
        except Exception:
            pass

    # -------------------------------------------------------------

    def _cache_path(self, key):

        return os.path.join(
            CACHE_DIR,
            "movies_%s.json" % str(key)
        )

    # -------------------------------------------------------------

    def _load_disk_cache(self, key, ttl=CACHE_TTL):

        path = self._cache_path(key)

        try:

            if time.time() - os.path.getmtime(path) > ttl:
                return None

            with open(path, "r") as f:
                data = json.load(f)

            movies = []

            for d in data:

                m = Movie()

                m.source_id           = d.get("source_id", 0)
                m.source_type         = d.get("source_type", "XTREAM")
                m.category_id         = d.get("category_id", "")
                m.stream_id           = d.get("stream_id", "")
                m.name                = d.get("name", "")
                m.stream_type         = d.get("stream_type", "movie")
                m.stream_icon         = d.get("stream_icon", "")
                m.container_extension = d.get("container_extension", "")
                m.direct_source       = d.get("direct_source", "")
                m.added               = d.get("added", 0)
                m.num                 = d.get("num", 0)
                m.rating              = d.get("rating", "")
                m.rating_5based       = d.get("rating_5based", "")
                m.year                = d.get("year", "")
                m.plot                = d.get("plot", "")
                m.genre               = d.get("genre", "")
                m.cast               = d.get("cast", "")
                m.director            = d.get("director", "")
                m.release_date        = d.get("release_date", "")
                m.hidden              = bool(
                    d.get("hidden", False)
                )
                m.favourite            = bool(
                    d.get("favourite", False)
                )
                m.info_loaded          = True

                movies.append(m)

            return movies

        except Exception:
            return None

    # -------------------------------------------------------------

    def _save_disk_cache(self, key, movies):

        try:

            with open(
                self._cache_path(key),
                "w"
            ) as f:

                json.dump([{

                    "source_id":           m.source_id,
                    "source_type":         m.source_type,
                    "category_id":         m.category_id,
                    "stream_id":           m.stream_id,
                    "name":                m.name,
                    "stream_type":         m.stream_type,
                    "stream_icon":         m.stream_icon,
                    "container_extension": m.container_extension,
                    "direct_source":       m.direct_source,
                    "added":               m.added,
                    "num":                 m.num,
                    "rating":              m.rating,
                    "rating_5based":       m.rating_5based,
                    "year":                m.year,
                    "plot":                m.plot,
                    "genre":               m.genre,
                    "cast":                m.cast,
                    "director":            m.director,
                    "release_date":        m.release_date,
                    "hidden":              m.hidden,
                    "favourite":           m.favourite,

                } for m in movies], f)

        except Exception:
            pass

    # -------------------------------------------------------------

    def load(self, provider, category):

        # Runtime key remains numeric for compatibility.
        runtime_key = "%s_%s" % (
            category.source_id,
            category.category_id
        )

        # Disk identity is permanently tied to the provider.
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

        movies = self.reader.read(
            provider,
            category
        )

        self.put(
            runtime_key,
            movies
        )

        self._save_disk_cache(
            disk_key,
            movies
        )

        return len(movies)
