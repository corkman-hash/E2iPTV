# =============================================================================
# IE2iPTV Backend
# Series Reader
# =============================================================================

from .series import Series
from .xtream_client import XtreamClient
from .utils import as_int, as_str


class SeriesReader(object):

    def read(self, provider, category=None):
        """category=None fetches every series for this provider in one
        call — same reasoning as MovieReader.read(). Used by Search."""

        if provider.source_type == "STALKER":
            return self._read_stalker(provider, category)

        client = XtreamClient(provider)

        if category is not None:
            data = client.call(
                "get_series",
                category_id=category.category_id
            )
        else:
            data = client.call(
                "get_series"
            )

        series = []

        for item in data:

            s = Series()

            # Source
            s.source_id = provider.provider_id
            s.source_type = provider.source_type

            # Category
            s.category_id = as_str(item.get("category_id"))

            # Identity
            s.series_id = as_str(item.get("series_id"))
            s.name = as_str(item.get("name"))

            # Artwork
            s.cover = as_str(item.get("cover"))

            backdrop = item.get("backdrop_path", [])
            if isinstance(backdrop, list):
                if backdrop:
                    s.backdrop_path = as_str(backdrop[0])
            else:
                s.backdrop_path = as_str(backdrop)

            # Metadata
            s.plot = as_str(item.get("plot"))
            s.genre = as_str(item.get("genre"))
            s.cast = as_str(item.get("cast"))
            s.director = as_str(item.get("director"))
            s.rating = as_str(item.get("rating"))
            s.rating_5based = as_str(item.get("rating_5based"))
            s.release_date = as_str(item.get("releaseDate"))
            s.last_modified = as_int(item.get("last_modified"))

            series.append(s)

        return series

    def _read_stalker(self, provider, category=None):
        """category=None isn't supported for Stalker the same way it
        is for Xtream — see MovieReader._read_stalker's own docstring
        for why; the exact same reasoning applies here, and the exact
        same real call sites never pass None (see SeriesStore.load)."""

        from .stalker_client import StalkerClient

        if category is None:
            return []

        items = StalkerClient(provider).get_series_items(category.category_id, search=True)

        return self._read_stalker_items(provider, items)

    def _read_stalker_items(self, provider, items):
        """Convert one Stalker Series page into Series objects."""

        series = []

        for item in items or []:

            s = Series()

            # Source
            s.source_id = provider.provider_id
            s.source_type = provider.source_type

            # Category
            s.category_id = as_str(item.get("category_id"))

            # Identity — confirmed directly against a real portal:
            # the series list's own "id" field is a compound
            # "<series_id>:<series_id>" (e.g. "10503:10503"), not the
            # plain numeric id the later movie_id= lookup for seasons
            # actually needs. Splitting off the part before the colon
            # gives the real id; falling back to the raw value covers
            # any portal that doesn't use this compound form.
            raw_id = as_str(item.get("id"))
            s.series_id = raw_id.split(":")[0] if ":" in raw_id else raw_id
            s.name = as_str(item.get("name"))

            # Artwork — same field confirmed for movies; "cover" and
            # "backdrop_path" (Xtream's own field names) don't exist
            # on a Stalker item at all.
            s.cover = as_str(item.get("screenshot_uri"))
            s.backdrop_path = as_str(item.get("screenshot_uri"))

            # Metadata — same reasoning as MovieReader._read_stalker:
            # confirmed directly that the list call already has full
            # plot/cast/director/rating/genre, not just identity
            # fields.
            s.plot = as_str(item.get("description"))
            s.genre = as_str(item.get("genres_str"))
            s.cast = as_str(item.get("actors"))
            s.director = as_str(item.get("director"))
            s.rating = as_str(item.get("rating_imdb"))
            s.rating_5based = self._imdb_to_5based(item.get("rating_imdb"))
            s.release_date = as_str(item.get("year"))
            s.last_modified = self._parse_stalker_date(item.get("added"))

            series.append(s)

        return series

    def _parse_stalker_date(self, raw):
        """See MovieReader._read_stalker's identically-named helper
        for why this can't just be as_int() — Stalker's own "added"
        field is a human-readable datetime string, not the unix
        -timestamp digits Xtream's identically-named field already
        is."""
        if not raw:
            return 0
        import time
        try:
            return int(time.mktime(time.strptime(raw, "%Y-%m-%d %H:%M:%S")))
        except Exception:
            return 0

    def _imdb_to_5based(self, raw):
        """See MovieReader._read_stalker's identically-named helper —
        same 0-10 to 0-5 conversion, same reasoning."""
        try:
            return str(round(float(raw) / 2, 1))
        except Exception:
            return ""
