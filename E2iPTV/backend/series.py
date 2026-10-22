# =============================================================================
# IE2iPTV Backend
# Series Record
# =============================================================================


class Series(object):

    def __init__(self):

        # Source
        self.source_id = 0
        self.source_type = "XTREAM"

        # Category
        self.category_id = ""

        # Identity
        self.series_id = ""
        self.name = ""

        # Artwork
        self.cover = ""
        self.backdrop_path = ""

        # Metadata
        self.plot = ""
        self.genre = ""
        self.cast = ""
        self.director = ""
        self.rating = ""
        self.rating_5based = ""
        self.release_date = ""
        self.last_modified = 0

        # Application
        self.hidden = False
        self.favourite = False

    def __str__(self):

        return "[%s] %s" % (
            self.series_id,
            self.name
        )
