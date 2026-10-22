# =============================================================================
# IE2iPTV Backend
# Live Channel Record
# =============================================================================


class LiveChannel(object):

    def __init__(self):

        # Source
        self.source_id = 0
        self.provider_key = ""
        self.source_type = "XTREAM"

        # Category
        self.category_id = ""

        # Identity
        self.stream_id = ""
        self.name = ""
        self.stream_type = "live"

        # EPG
        self.epg_channel_id = ""

        # Artwork
        self.stream_icon = ""

        # Catch-up
        self.tv_archive = False
        self.tv_archive_duration = 0

        # Provider
        self.num = 0
        self.custom_sid = ""
        self.direct_source = ""
        self.added = 0

        # Application
        self.hidden = False
        self.favourite = False

    def __str__(self):

        return "[%s] %s" % (
            self.stream_id,
            self.name
        )
