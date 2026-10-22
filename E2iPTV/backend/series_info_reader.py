# =============================================================================
# IE2iPTV Backend
# Series Info Reader
# =============================================================================

from .series_info import SeriesInfo
from .season import Season
from .episode import Episode

from .xtream_client import XtreamClient
from .utils import as_int, as_str


class SeriesInfoReader(object):

    def read(self, provider, series):

        if provider.source_type == "STALKER":
            return self._read_stalker(provider, series)

        client = XtreamClient(provider)

        data = client.call(
            "get_series_info",
            series_id=series.series_id
        )

        info = SeriesInfo()

        # -------------------------------------------------------------
        # Parent
        # -------------------------------------------------------------

        info.source_id = provider.provider_id
        info.source_type = provider.source_type
        info.series_id = series.series_id

        # -------------------------------------------------------------
        # Series metadata
        # -------------------------------------------------------------

        meta = data.get("info", {})

        info.name = as_str(meta.get("name"))
        info.cover = as_str(meta.get("cover"))
        info.plot = as_str(meta.get("plot"))
        info.cast = as_str(meta.get("cast"))
        info.director = as_str(meta.get("director"))
        info.genre = as_str(meta.get("genre"))
        info.rating = as_str(meta.get("rating"))
        info.rating_5based = as_str(meta.get("rating_5based"))
        info.release_date = as_str(meta.get("releaseDate"))
        info.last_modified = as_int(meta.get("last_modified"))
        info.youtube_trailer = as_str(meta.get("youtube_trailer"))
        info.episode_run_time = as_int(meta.get("episode_run_time"))
        info.category_id = as_str(meta.get("category_id"))

        backdrop = meta.get("backdrop_path", [])

        if isinstance(backdrop, list):
            if backdrop:
                info.backdrop_path = as_str(backdrop[0])
        else:
            info.backdrop_path = as_str(backdrop)

        # -------------------------------------------------------------
        # Seasons
        # -------------------------------------------------------------

        for item in data.get("seasons", []):

            season = Season()

            season.source_id = provider.provider_id
            season.source_type = provider.source_type

            season.series_id = series.series_id

            season.season_id = as_int(item.get("id"))
            season.season_number = as_int(item.get("season_number"))
            season.name = as_str(item.get("name"))

            season.air_date = as_str(item.get("air_date"))
            season.overview = as_str(item.get("overview"))
            season.episode_count = as_int(item.get("episode_count"))

            season.cover = as_str(item.get("cover"))
            season.cover_big = as_str(item.get("cover_big"))

            info.seasons.append(season)

        # -------------------------------------------------------------
        # Episodes
        # -------------------------------------------------------------

        # Normally a dict of season_number -> episode list, but real
        # providers have been seen sending this differently for
        # specific series: an empty list instead of an empty dict when
        # there's genuinely nothing, or — seen on a series with a
        # Specials season plus several numbered ones — a list of
        # per-season lists grouped by array position instead of a dict
        # keyed by season number. The season_number key/position was
        # only ever used as an iteration convenience anyway (each
        # episode's own season number is read from the episode dict
        # itself below, via item.get("season")), so flattening any of
        # these shapes into one plain list of episode dicts up front
        # handles all of them without needing to treat them
        # differently in the loop that follows. Checking isinstance
        # at each level (rather than assuming a list's contents are
        # always dicts, which is what broke on the grouped-list case)
        # is what actually makes this robust to a shape not seen yet
        # either, rather than just adding another special case.
        episodes_raw = data.get("episodes", {})

        all_episode_dicts = []

        if isinstance(episodes_raw, dict):
            for group in episodes_raw.values():
                if isinstance(group, list):
                    all_episode_dicts.extend(
                        e for e in group if isinstance(e, dict)
                    )
        elif isinstance(episodes_raw, list):
            for entry in episodes_raw:
                if isinstance(entry, dict):
                    # Flat list of episode dicts directly.
                    all_episode_dicts.append(entry)
                elif isinstance(entry, list):
                    # List of per-season lists, grouped by position
                    # rather than by a season_number dict key.
                    all_episode_dicts.extend(
                        e for e in entry if isinstance(e, dict)
                    )

        for item in all_episode_dicts:

            episode = Episode()

            episode.source_id = provider.provider_id
            episode.source_type = provider.source_type

            episode.series_id = series.series_id

            episode.season_number = as_int(item.get("season"))
            episode.episode_id = as_str(item.get("id"))
            episode.episode_number = as_int(item.get("episode_num"))
            episode.title = as_str(item.get("title"))

            episode.container_extension = as_str(item.get("container_extension"))
            episode.direct_source = as_str(item.get("direct_source"))
            episode.added = as_int(item.get("added"))

            meta = item.get("info", {})

            episode.tmdb_id = as_int(meta.get("tmdb_id"))
            episode.plot = as_str(meta.get("plot"))
            episode.duration = as_str(meta.get("duration"))
            episode.duration_secs = as_int(meta.get("duration_secs"))
            episode.release_date = as_str(meta.get("releasedate"))
            episode.rating = as_int(meta.get("rating"))
            episode.movie_image = as_str(meta.get("movie_image"))

            info.episodes.append(episode)

        return info

    def _read_stalker(self, provider, series):
        """No separate "get series info" call exists for Stalker the
        way Xtream has one — the series list item (read by
        SeriesReader._read_stalker) already carries every metadata
        field a detail call would otherwise add, so that's copied
        straight across here rather than re-fetched.

        Confirmed directly against a real portal: get_series_seasons
        (type=series&action=get_ordered_list&movie_id=<series_id>)
        returns one item per season, each with its own "series" field
        — a plain list of episode numbers, e.g. [1, 2, 3] — and its
        own "cmd", which combined with one of those numbers is
        everything resolve_series_url needs later. No per-episode
        title exists anywhere in this data; "Episode N" is what's
        actually shown, not a placeholder standing in for something
        this portal has and this code just isn't reading yet."""

        from .stalker_client import StalkerClient

        info = SeriesInfo()

        info.source_id = provider.provider_id
        info.source_type = provider.source_type
        info.series_id = series.series_id

        # Copied straight from the already-read series list item
        # rather than re-fetched — see this method's own docstring.
        info.name = series.name
        info.cover = series.cover
        info.backdrop_path = series.backdrop_path
        info.plot = series.plot
        info.cast = series.cast
        info.director = series.director
        info.genre = series.genre
        info.rating = series.rating
        info.rating_5based = series.rating_5based
        info.release_date = series.release_date
        info.last_modified = series.last_modified
        info.category_id = series.category_id

        seasons_raw = StalkerClient(provider).get_series_seasons(series.series_id)

        for item in seasons_raw:

            raw_id = as_str(item.get("id"))
            season_number = 0
            if ":" in raw_id:
                try:
                    season_number = int(raw_id.split(":")[1])
                except (ValueError, IndexError):
                    season_number = 0

            season = Season()
            season.source_id = provider.provider_id
            season.source_type = provider.source_type
            season.series_id = series.series_id
            season.season_id = season_number
            season.season_number = season_number
            season.name = as_str(item.get("name")) or ("Season %d" % season_number)
            season.overview = as_str(item.get("description"))

            episode_numbers = item.get("series", [])
            if not isinstance(episode_numbers, list):
                episode_numbers = []
            season.episode_count = len(episode_numbers)

            season.cover = as_str(item.get("screenshot_uri"))
            season.cover_big = as_str(item.get("screenshot_uri"))

            info.seasons.append(season)

            season_cmd = as_str(item.get("cmd"))

            for ep_num in episode_numbers:
                episode = Episode()

                episode.source_id = provider.provider_id
                episode.source_type = provider.source_type

                episode.series_id = series.series_id
                episode.season_number = season_number

                episode.episode_id = "%s:%d:%s" % (series.series_id, season_number, ep_num)
                episode.episode_number = as_int(ep_num)
                episode.title = "Episode %s" % ep_num

                episode.container_extension = ""
                # See resolve_series_url's own docstring for this
                # exact format — season cmd and episode number,
                # joined so they can be split back apart at actual
                # play time rather than resolved (and the resulting
                # url held onto, going stale) up front.
                episode.direct_source = "%s|%s" % (season_cmd, ep_num)
                episode.added = 0

                episode.plot = as_str(item.get("description"))
                episode.release_date = as_str(item.get("year"))
                episode.rating = as_int(float(item.get("rating_imdb") or 0))

                info.episodes.append(episode)

        return info
