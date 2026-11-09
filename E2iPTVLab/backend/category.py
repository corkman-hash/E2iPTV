# =============================================================================
# IE2iPTV Backend
# Category Record
# =============================================================================


class Category(object):

    def __init__(self):

        # Source
        self.source_id = 0
        self.source_type = "XTREAM"

        # Category
        self.category_id = ""
        self.category_name = ""
        self.parent_id = "0"

        # Type
        self.category_type = "LIVE"

        # Application
        self.hidden = False

    def __str__(self):

        return "[%s] %s" % (
            self.category_id,
            self.category_name
        )
