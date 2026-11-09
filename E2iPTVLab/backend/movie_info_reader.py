# =============================================================================
# IE2iPTV Backend
# Movie Info Reader
# =============================================================================

from .xtream_client import XtreamClient
from .utils import as_str


class MovieInfoReader(object):
    """Fetches the detailed VOD metadata (plot, cast, genre, etc.) that
    Xtream's basic movie list call (get_vod_streams — see MovieReader)
    doesn't include. Series gets this for free in its own list call
    (get_series returns plot/cast/genre per item directly — see
    SeriesReader), but Xtream's movie list is much leaner, so movies
    need this separate get_vod_info call, one per movie, to get a
    description at all.

    Returns a plain field dict rather than a whole second object type
    (the way SeriesInfoReader returns a SeriesInfo) — a movie has no
    episodes/seasons to carry, so there's nothing to gain from a
    parallel class; the caller (Backend.get_movie_info) just merges
    these fields straight into the existing Movie object."""

    def read(self, provider, movie):

        client = XtreamClient(provider)

        data = client.call(
            "get_vod_info",
            vod_id=movie.stream_id
        )

        meta = data.get("info", {}) if isinstance(data, dict) else {}

        # Some Xtream panels use "plot", others "description", for the
        # same field — try both rather than assuming one.
        plot = (
            as_str(meta.get("plot")) or
            as_str(meta.get("description"))
        )

        cast = (
            as_str(meta.get("cast")) or
            as_str(meta.get("actors"))
        )

        release_date = (
            as_str(meta.get("releasedate")) or
            as_str(meta.get("release_date"))
        )

        return {
            "plot": plot,
            "cast": cast,
            "director": as_str(meta.get("director")),
            "genre": as_str(meta.get("genre")),
            "rating": as_str(meta.get("rating")),
            "release_date": release_date,
            "year": as_str(meta.get("year")),
        }
