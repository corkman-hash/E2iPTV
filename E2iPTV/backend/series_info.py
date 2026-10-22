# =============================================================================
# IE2iPTV Backend
# Series Info Record
# =============================================================================


class SeriesInfo(object):

    def __init__(self):

        # Source
        self.source_id = 0
        self.source_type = "XTREAM"

        # Identity
        self.series_id = ""

        # Metadata
        self.name = ""
        self.cover = ""
        self.backdrop_path = ""
        self.plot = ""
        self.cast = ""
        self.director = ""
        self.genre = ""
        self.rating = ""
        self.rating_5based = ""
        self.release_date = ""
        self.last_modified = 0
        self.youtube_trailer = ""
        self.episode_run_time = 0
        self.category_id = ""

        # Child Objects
        self.seasons = []
        self.episodes = []

    def __str__(self):

        return "[%s] %s" % (
            self.series_id,
            self.name
        )
