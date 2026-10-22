# =============================================================================
# IE2iPTV Backend
# Xtream Client
# =============================================================================

from ..paths import LOG  # data folder / debug logs
import json
import time

try:
    import urllib2
except ImportError:
    import urllib.request as urllib2


XTREAM_TIMEOUT = 12
_FAIL_LOG = LOG("e2iptv_xtream.log")


def _log_failure(source, action, params, elapsed, ex):
    try:
        with open(_FAIL_LOG, "a") as f:
            f.write("%s %s %s %r after %.1fs: %s\n" % (
                time.strftime("%H:%M:%S"),
                getattr(source, "name", "?"),
                action,
                params,
                elapsed,
                ex,
            ))
    except Exception:
        pass


# -----------------------------------------------------------------------------
# "Too Many Requests" (HTTP 429): pause guide requests to that provider.
#
# Scrolling the guide asks for many channels' guides at once, and some
# providers answer 429 - slow down. Carrying on at full speed regardless is
# what can get an account temporarily blocked. So on a 429 the provider's
# GUIDE requests pause (30 s, doubling to at most 5 min if it keeps
# happening). The pause is kept in a small file so the overnight refresh's
# separate processes share it with the screens:
#   - on screen, a guide request during the pause is skipped at once
#     (XtreamBusy) - the channel just shows its guide a little later
#   - the overnight refresh (E2IPTV_WAIT_ON_BUSY set) waits it out instead
#     of counting channels as failed
# Other requests (categories, channel lists, films) are never held up.
# -----------------------------------------------------------------------------

_GUIDE_ACTIONS = ("get_simple_data_table", "get_short_epg")


class XtreamBusy(Exception):
    """Provider asked us to slow down; this guide request was skipped."""


def _wait_file(source):
    from ..paths import DATA
    import hashlib
    key = str(getattr(source, "provider_key", "") or "") or hashlib.sha1(
        str(getattr(source, "base_url", "")).encode("utf-8")).hexdigest()[:16]
    return DATA + "/cache/.xtream_wait_%s" % key


def _read_wait(source):
    """(pause_until, last_pause_seconds) for this provider."""
    try:
        with open(_wait_file(source)) as f:
            until, length = f.read().split()
        return float(until), float(length)
    except Exception:
        return 0.0, 0.0


def _start_pause(source, retry_after=None):
    import os
    now = time.time()
    until, length = _read_wait(source)
    # Doubles if it happens again soon after the last pause ended.
    if now - until < 120 and length:
        length = min(300.0, length * 2)
    else:
        length = 30.0
    if retry_after:
        length = max(length, min(300.0, float(retry_after)))
    try:
        tmp = _wait_file(source) + ".tmp"
        with open(tmp, "w") as f:
            f.write("%f %f" % (now + length, length))
        os.replace(tmp, _wait_file(source))
    except Exception:
        pass
    return length


class XtreamClient(object):

    def __init__(self, source):

        self.source = source

    def call(self, action, **params):

        url = (
            self.source.base_url +
            "/player_api.php?username=%s&password=%s&action=%s"
            % (
                self.source.username,
                self.source.password,
                action
            )
        )

        for key in sorted(params.keys()):
            url += "&%s=%s" % (key, params[key])

        # Was timeout=2. get_simple_data_table returns several days
        # of base64 EPG per channel, and the guide fires 6 of these at
        # once — 2 seconds left almost no margin, so a provider getting
        # even slightly slower made most channels fail. Those failures
        # were swallowed silently, so the guide just stayed blank.
        guide = action in _GUIDE_ACTIONS
        if guide:
            until, _length = _read_wait(self.source)
            wait = until - time.time()
            if wait > 0:
                import os
                if os.environ.get("E2IPTV_WAIT_ON_BUSY"):
                    time.sleep(wait)                 # overnight refresh
                else:
                    raise XtreamBusy("paused %.0fs (provider asked to slow down)" % wait)

        started = time.time()
        try:
            return json.loads(
                urllib2.urlopen(
                    url,
                    timeout=XTREAM_TIMEOUT
                ).read()
            )
        except Exception as ex:
            if guide and getattr(ex, "code", None) == 429:
                retry = None
                try:
                    retry = ex.headers.get("Retry-After")
                except Exception:
                    pass
                until, _l = _read_wait(self.source)
                if until < time.time():          # log once per pause
                    length = _start_pause(self.source, retry)
                    _log_failure(self.source, action, params,
                                 time.time() - started,
                                 "%s - pausing guide requests for %ds"
                                 % (ex, length))
                raise
            _log_failure(self.source, action, params, time.time() - started, ex)
            raise
