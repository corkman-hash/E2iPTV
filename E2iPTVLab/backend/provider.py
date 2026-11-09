# =============================================================================
# IE2iPTV Backend
# Provider Object
# =============================================================================

import hashlib


class Provider(object):

    def __init__(self):

        # Internal
        self.provider_id = 0
        self.provider_idx = 0
        self.provider_key = ""

        # Source
        self.source_type = "XTREAM"

        # Playlist
        self.name = ""
        self.base_url = ""
        self.username = ""
        self.password = ""
        self.enabled = True

        # Which content this provider is used for (Settings -> Playlists
        # -> provider -> Live TV / Movies / Series). See shows().
        self.show_live = True
        self.show_movies = True
        self.show_series = True

        # Stalker/Ministra portals authenticate by MAC address instead
        # of username/password — only ever set when source_type is
        # "STALKER". self.token below is the handshake session token
        # (see backend/stalker_client.py), refreshed automatically
        # whenever it expires or a call is rejected — never persisted,
        # always re-established per session.
        self.mac   = ""
        self.token = ""

        # Server
        self.host = ""
        self.protocol = ""
        self.port = ""
        self.https_port = ""

        # Account
        self.status = "Unknown"
        self.current_connections = 0
        self.max_connections = 0
        self.expires = 0

        # Time
        self.timezone = ""
        self.server_time = ""
        self.server_timestamp = 0
        self.epg_offset = 0

        # Capabilities
        self.supports_live = False
        self.supports_movies = False
        self.supports_series = False
        self.supports_epg = False
        self.supports_catchup = False

        # Load State
        self.live_categories_loaded = False
        self.movie_categories_loaded = False
        self.series_categories_loaded = False

        # Detailed Objects
        self.series_info_loaded = {}

        # Backend
        self.last_checked = 0
        self.last_refresh = 0
        self.connected = False

    def build_provider_key(self):

        if self.source_type == "STALKER":

            identity = "%s|%s|%s" % (
                self.source_type,
                self.base_url.strip().rstrip("/").casefold(),
                self.mac.strip().upper()
            )

        else:

            identity = "%s|%s|%s" % (
                self.source_type,
                self.base_url.strip().rstrip("/").casefold(),
                self.username.strip()
            )

        self.provider_key = hashlib.sha1(
            identity.encode("utf-8")
        ).hexdigest()[:16]

        return self.provider_key

    def __str__(self):

        return "[%03d] %s" % (
            self.provider_id,
            self.name
        )


def shows(provider, kind):
    """True if `provider` is switched on and used for `kind` ("live",
    "movies" or "series"). My Bouquets is Live TV only."""
    if provider is None or not getattr(provider, "enabled", True):
        return False
    if getattr(provider, "is_bouquet", False):
        return kind == "live"
    return bool(getattr(provider, "show_" + kind, True))
