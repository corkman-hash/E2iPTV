# =============================================================================
# IE2iPTV Backend
# Movie Reader
# =============================================================================

import time

from .movie import Movie
from .xtream_client import XtreamClient
from .utils import as_int, as_str


class MovieReader(object):

    def read(self, source, category=None):
        """category=None fetches every movie for this provider in one
        call — Xtream's get_vod_streams returns the full catalogue
        when category_id is omitted, rather than requiring a loop over
        every category. Used by Search (see search.py) to get a
        provider's whole movie list to filter locally against."""

        if source.source_type == "STALKER":
            return self._read_stalker(source, category)

        client = XtreamClient(source)

        if category is not None:
            data = client.call(
                "get_vod_streams",
                category_id=category.category_id
            )
        else:
            data = client.call(
                "get_vod_streams"
            )

        movies = []

        for item in data:

            m = Movie()

            # Source
            m.source_id = source.provider_id
            m.source_type = source.source_type

            # Category
            m.category_id = as_str(item.get("category_id"))

            # Identity
            m.stream_id = as_str(item.get("stream_id"))
            m.name = as_str(item.get("name"))
            m.stream_type = as_str(item.get("stream_type"))

            # Artwork
            m.stream_icon = as_str(item.get("stream_icon"))

            # Provider
            m.container_extension = as_str(item.get("container_extension"))
            m.direct_source = as_str(item.get("direct_source"))
            m.added = as_int(item.get("added"))
            m.num = as_int(item.get("num"))

            # Metadata
            m.rating = as_str(item.get("rating"))
            m.rating_5based = as_str(item.get("rating_5based"))

            movies.append(m)

        return movies

    def _read_stalker(self, source, category=None):
        """Read a Stalker VOD category using the normal paginated call."""

        from .stalker_client import StalkerClient

        if category is None:
            return []

        items = StalkerClient(source).get_vod_items(
            category.category_id
        )

        return self._read_stalker_items(source, items)

    def _read_stalker_items(self, source, items):
        """Convert one Stalker VOD page into Movie objects."""

        movies = []

        for item in items or []:

            m = Movie()

            # Source
            m.source_id = source.provider_id
            m.source_type = source.source_type

            # Category
            m.category_id = as_str(item.get("category_id"))

            # Identity
            m.stream_id = as_str(item.get("id"))
            m.name = as_str(item.get("name"))
            m.stream_type = "movie"

            # Artwork
            m.stream_icon = as_str(item.get("screenshot_uri"))

            # Provider
            m.container_extension = ""
            m.direct_source = as_str(item.get("cmd"))
            m.added = self._parse_stalker_date(item.get("added"))
            m.num = 0

            # Metadata
            m.rating = as_str(item.get("rating_imdb"))
            m.rating_5based = self._imdb_to_5based(
                item.get("rating_imdb")
            )
            m.year = as_str(item.get("year"))[:4]
            m.plot = as_str(item.get("description"))
            m.genre = as_str(item.get("genres_str"))
            m.cast = as_str(item.get("actors"))
            m.director = as_str(item.get("director"))
            m.release_date = as_str(item.get("year"))

            m.info_loaded = True

            movies.append(m)

        return movies

    def _parse_stalker_date(self, raw):
        """Stalker's "added" field is a human-readable datetime
        string ("2026-08-11 17:55:00"), not the unix-timestamp digits
        Xtream's own "added" field already is — as_int() alone would
        silently produce 0 for this rather than a usable timestamp."""
        if not raw:
            return 0
        try:
            return int(time.mktime(time.strptime(raw, "%Y-%m-%d %H:%M:%S")))
        except Exception:
            return 0

    def _imdb_to_5based(self, raw):
        """rating_imdb is 0-10; rating_5based is the 0-5 scale the
        rest of the app already expects from Xtream's own identically
        -named field — a straight halving, not a guess at some other
        portal-specific scale."""
        try:
            return str(round(float(raw) / 2, 1))
        except Exception:
            return ""
