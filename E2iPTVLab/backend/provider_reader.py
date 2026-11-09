# =============================================================================
# IE2iPTV Backend
# Provider Reader
#
# Reads account information for ONE provider.
# =============================================================================

import json

try:
    import urllib2
except ImportError:
    import urllib.request as urllib2

from .provider import Provider
from .utils import as_int, as_str
from .stalker_client import StalkerClient, StalkerError, handshake, get_profile


class ProviderReader(object):

    def read(self, provider):

        if provider.source_type == "STALKER":
            return self._read_stalker(provider)

        url = (
            provider.base_url +
            "/player_api.php?username=%s&password=%s"
            % (provider.username, provider.password)
        )

        response = urllib2.urlopen(url, timeout=15)
        data = json.loads(response.read())

        user = data.get("user_info", {})
        server = data.get("server_info", {})

        # Account
        provider.status = user.get("status", "Unknown")
        provider.current_connections = as_int(user.get("active_cons"))
        provider.max_connections = as_int(user.get("max_connections"))
        provider.expires = as_int(user.get("exp_date"))

        # Server
        provider.host = as_str(server.get("url"))
        provider.protocol = as_str(server.get("server_protocol"))
        provider.port = server.get("port", "")
        provider.https_port = server.get("https_port", "")

        # Time
        provider.timezone = server.get("timezone", "")
        provider.server_time = as_str(server.get("time_now"))
        provider.server_timestamp = as_int(server.get("timestamp_now"))

        return provider

    def _read_stalker(self, provider):
        # Stalker has no direct equivalent of Xtream's user_info/
        # server_info response — a successful handshake IS the
        # account check here (an invalid/expired MAC fails right at
        # that step, same as the UI needs to know either way). Portal-
        # reported connection limits/expiry, where get_profile
        # actually returns them (not all portals do), are filled in
        # on a best-effort basis rather than left to fail the whole
        # check if absent.
        try:
            token = handshake(provider)
            provider.token = token
            provider._token_time = __import__("time").time()
        except StalkerError as e:
            provider.status = "Unknown"
            raise

        provider.status = "Active"

        try:
            profile = get_profile(provider.base_url, provider.mac, token)
            js = profile.get("js", {})
            provider.max_connections = as_int(js.get("max_len_num", 1)) or 1
            provider.current_connections = 0
        except Exception:
            # Non-fatal — see get_profile's own docstring for why;
            # the handshake above already confirmed the account
            # itself works, this is just extra detail on top.
            pass

        provider.host = provider.base_url
        provider.server_timestamp = int(__import__("time").time())

        return provider
