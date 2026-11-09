# =============================================================================
# IE2iPTV Backend
# Episode Record
# =============================================================================


class Episode(object):

    def __init__(self):

        # Source
        self.source_id = 0
        self.source_type = "XTREAM"

        # Parent
        self.series_id = ""
        self.season_number = 0

        # Identity
        self.episode_id = ""
        self.episode_number = 0
        self.title = ""

        # Provider
        self.container_extension = ""
        self.direct_source = ""
        self.added = 0

        # Metadata
        self.tmdb_id = 0
        self.plot = ""
        self.duration = ""
        self.duration_secs = 0
        self.release_date = ""
        self.rating = 0
        self.movie_image = ""

    def __str__(self):

        return "S%02dE%02d %s" % (
            self.season_number,
            self.episode_number,
            self.title
        )
