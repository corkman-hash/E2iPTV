# =============================================================================
# IE2iPTV Backend
# Live Channel Reader
# =============================================================================

import json

try:
    import urllib2
except ImportError:
    import urllib.request as urllib2

from .live_channel import LiveChannel
from .stalker_client import StalkerClient


class LiveChannelReader(object):

    def read(self, source, category):

        if source.source_type == "STALKER":
            data = StalkerClient(source).get_channels(category.category_id)
        else:
            url = (
                source.base_url +
                "/player_api.php?username=%s&password=%s"
                "&action=get_live_streams&category_id=%s"
                % (
                    source.username,
                    source.password,
                    category.category_id
                )
            )
            data = json.loads(
                urllib2.urlopen(url, timeout=3).read()
            )

        channels = []

        for item in data:

            c = LiveChannel()

            # Source
            c.source_id = source.provider_id
            c.provider_key = source.provider_key
            c.source_type = source.source_type

            # Category
            c.category_id = item.get("category_id", category.category_id)

            if source.source_type == "STALKER":
                # Stalker's own field names/shape differ from Xtream's
                # — normalised to the same LiveChannel shape here (see
                # CategoryReader's identical reasoning) so the rest of
                # the app — UI, EPG, playback — never needs to branch
                # on source_type itself, only this reader does.
                # "cmd" is deliberately NOT stored as a playable URL —
                # see build_live_url's own comment on why that
                # resolution has to happen at play time instead.
                c.stream_id = str(item.get("id", ""))
                c.name = item.get("name", "")
                c.stream_type = "live"
                c.epg_channel_id = item.get("xmltv_id", "") or item.get("epg_id", "")
                c.stream_icon = item.get("logo", "")
                c.tv_archive = bool(int(item.get("tv_archive", 0) or 0))
                c.tv_archive_duration = int(item.get("tv_archive_duration", 0) or 0)
                c.num = int(item.get("number", 0) or 0)
                c.custom_sid = ""
                c.direct_source = item.get("cmd", "")
                c.added = 0
            else:
                # Identity
                c.stream_id = str(item.get("stream_id", ""))
                c.name = item.get("name", "")
                c.stream_type = item.get("stream_type", "live")

                # EPG
                c.epg_channel_id = item.get("epg_channel_id", "")

                # Artwork
                c.stream_icon = item.get("stream_icon", "")

                # Catch-up
                c.tv_archive = bool(int(item.get("tv_archive", 0)))
                c.tv_archive_duration = int(item.get("tv_archive_duration", 0))

                # Provider
                c.num = int(item.get("num", 0))
                c.custom_sid = item.get("custom_sid", "")
                c.direct_source = item.get("direct_source", "")
                c.added = int(item.get("added", 0))

            channels.append(c)

        return channels
