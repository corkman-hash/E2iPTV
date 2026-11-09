# =============================================================================
# IE2iPTV Backend
# Category Reader
# =============================================================================

from .category import Category
from .xtream_client import XtreamClient
from .stalker_client import StalkerClient


class CategoryReader(object):

    def _read(self, source, action, category_type):

        if source.source_type == "STALKER":
            if category_type == "SERIES":
                data = StalkerClient(source).get_series_categories()
            elif category_type == "MOVIE":
                data = StalkerClient(source).get_vod_categories()
            else:
                data = StalkerClient(source).get_genres()
        else:
            client = XtreamClient(source)
            data = client.call(action)

        categories = []

        for item in data:

            c = Category()

            c.source_id = source.provider_id
            c.source_type = source.source_type

            c.category_type = category_type

            if source.source_type == "STALKER":
                # Stalker's own field names differ from Xtream's
                # (id/title rather than category_id/category_name) —
                # normalised to the same Category shape here so
                # nothing downstream needs to know which protocol
                # this actually came from.
                c.category_id = str(item.get("id", ""))
                c.category_name = item.get("title", "")
                c.parent_id = 0
            else:
                c.category_id = str(item.get("category_id", ""))
                c.category_name = item.get("category_name", "")
                c.parent_id = int(item.get("parent_id", 0))

            categories.append(c)

        return categories

    # -------------------------------------------------------------

    def read_live(self, source):

        return self._read(
            source,
            "get_live_categories",
            "LIVE"
        )

    # -------------------------------------------------------------

    def read_vod(self, source):

        return self._read(
            source,
            "get_vod_categories",
            "MOVIE"
        )

    # -------------------------------------------------------------

    def read_series(self, source):

        return self._read(
            source,
            "get_series_categories",
            "SERIES"
        )
