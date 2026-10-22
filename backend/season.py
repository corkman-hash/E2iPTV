# =============================================================================
# IE2iPTV Backend
# Season Record
# =============================================================================


class Season(object):

    def __init__(self):

        # Source
        self.source_id = 0
        self.source_type = "XTREAM"

        # Parent
        self.series_id = ""

        # Identity
        self.season_id = 0
        self.season_number = 0
        self.name = ""

        # Metadata
        self.air_date = ""
        self.overview = ""
        self.episode_count = 0

        # Artwork
        self.cover = ""
        self.cover_big = ""

    def __str__(self):

        return "S%02d %s" % (
            self.season_number,
            self.name
        )
