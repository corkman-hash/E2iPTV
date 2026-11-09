# =============================================================================
# IE2iPTV Backend
# Movie Record
# =============================================================================


class Movie(object):

    def __init__(self):

        # Source
        self.source_id = 0
        self.source_type = "XTREAM"

        # Category
        self.category_id = ""

        # Identity
        self.stream_id = ""
        self.name = ""
        self.stream_type = "movie"

        # Artwork
        self.stream_icon = ""

        # Provider
        self.container_extension = ""
        self.direct_source = ""
        self.added = 0
        self.num = 0

        # Metadata
        self.rating = ""
        self.rating_5based = ""
        self.year = ""
        self.plot = ""
        self.genre = ""
        self.cast = ""
        self.director = ""
        self.release_date = ""

        # Application
        self.hidden = False
        self.favourite = False

        # Set once get_movie_info() has fetched and merged the
        # detail-call fields (plot/cast/genre/etc — see
        # MovieInfoReader) — prevents re-fetching the same movie's
        # info every time it's re-highlighted while browsing.
        self.info_loaded = False

    def __str__(self):

        return "[%s] %s" % (
            self.stream_id,
            self.name
        )
