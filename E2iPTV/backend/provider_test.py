# =============================================================================
# E2iPTV — Test Connection (Settings -> Playlists -> provider)
#
# Checks one provider step by step and says in plain English where it
# stops working:
#   1. the server's name can be looked up (DNS)
#   2. the server answers, and accepts the login
#      Xtream: account status, expiry date and connections in use
#      Stalker: the portal hands out a session for this MAC address
#   3. it sends its Live TV category list (how many)
# Each step has its own time limit, so a server that never answers shows
# as "no answer after N s" instead of hanging.
#
# Never shows or logs the login, password, MAC or full address - only the
# server's host name.
# =============================================================================

import json
import socket
import threading
import time
from urllib.parse import urlparse

STEP_TIMEOUT = 15            # seconds per step


def _host(provider):
    try:
        return urlparse(provider.base_url).hostname or "?"
    except Exception:
        return "?"


def _scrub(provider, text):
    text = str(text)
    for secret in (getattr(provider, "username", ""), getattr(provider, "password", ""),
                   getattr(provider, "mac", ""), getattr(provider, "base_url", "")):
        if secret and len(str(secret)) >= 3:
            text = text.replace(str(secret), "***")
    return text


def _with_limit(fn, limit):
    """Run fn in a thread; (result, error, seconds). error 'timeout' if it
    didn't finish in time."""
    box = {}

    def work():
        try:
            box["r"] = fn()
        except Exception as e:
            box["e"] = e
    t0 = time.time()
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(limit)
    took = time.time() - t0
    if th.is_alive():
        return None, "timeout", took
    return box.get("r"), box.get("e"), took


def _err_text(provider, e):
    code = getattr(e, "code", None)
    if code:
        meaning = {401: "login refused", 403: "access refused (blocked, or account not allowed)",
                   404: "address not found on the server", 429: "too many requests",
                   500: "server error", 502: "server error", 503: "server busy / down",
                   512: "account problem (often expired)", 884: "account problem",
                   }.get(code, "")
        return "HTTP %s%s" % (code, (" - " + meaning) if meaning else "")
    reason = getattr(e, "reason", None)
    if reason is not None:
        e = reason
    if isinstance(e, socket.timeout):
        return "no answer (timed out)"
    return _scrub(provider, e)[:160]


def test(provider):
    """Returns (ok, [lines])."""
    lines = []
    host = _host(provider)
    kind = getattr(provider, "source_type", "XTREAM")
    lines.append("Server: %s  (%s)" % (host, "Stalker portal" if kind == "STALKER" else "Xtream"))

    # 1. name lookup
    r, e, took = _with_limit(lambda: socket.getaddrinfo(host, None), STEP_TIMEOUT)
    if e == "timeout":
        lines.append("1. Name lookup: no answer after %d s - DNS problem on the box" % STEP_TIMEOUT)
        return False, lines
    if e is not None:
        lines.append("1. Name lookup: FAILED - the server's name doesn't exist any more,")
        lines.append("   or the box has no internet (%s)" % _err_text(provider, e))
        return False, lines
    lines.append("1. Name lookup: OK (%.1f s)" % took)

    if kind == "STALKER":
        return _test_stalker(provider, lines)
    return _test_xtream(provider, lines)


def _get_json(url, timeout):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Enigma2"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "null")


def _test_xtream(provider, lines):
    base = "%s/player_api.php?username=%s&password=%s" % (
        provider.base_url, provider.username, provider.password)
    data, e, took = _with_limit(lambda: _get_json(base, STEP_TIMEOUT), STEP_TIMEOUT + 2)
    if e == "timeout":
        lines.append("2. Login: no answer after %d s - server down or very slow" % STEP_TIMEOUT)
        return False, lines
    if e is not None:
        lines.append("2. Login: FAILED - %s" % _err_text(provider, e))
        return False, lines
    ui = (data or {}).get("user_info") if isinstance(data, dict) else None
    if not ui:
        lines.append("2. Login: server answered but sent no account details (%.1f s)" % took)
        lines.append("   - wrong server address, or it isn't an Xtream server")
        return False, lines
    if str(ui.get("auth", "1")) == "0":
        lines.append("2. Login: REFUSED - username or password not accepted")
        return False, lines
    status = str(ui.get("status") or "?")
    exp = ui.get("exp_date")
    try:
        exp_txt = time.strftime("%d %b %Y", time.localtime(int(exp))) if exp else "no expiry date"
    except Exception:
        exp_txt = str(exp)
    cons = "%s of %s connections in use" % (ui.get("active_cons", "?"), ui.get("max_connections", "?"))
    lines.append("2. Login: OK (%.1f s) - account %s, expires %s" % (took, status, exp_txt))
    lines.append("   %s" % cons)
    ok = status.lower() == "active"
    if not ok:
        lines.append("   The account isn't Active - that alone stops it working.")
    try:
        if int(ui.get("active_cons") or 0) >= int(ui.get("max_connections") or 0) > 0:
            lines.append("   All connections in use (this box counts if it's playing from")
            lines.append("   this provider) - otherwise another device is using the account.")
    except Exception:
        pass

    cats, e, took = _with_limit(
        lambda: _get_json(base + "&action=get_live_categories", STEP_TIMEOUT), STEP_TIMEOUT + 2)
    if e == "timeout":
        lines.append("3. Live TV categories: no answer after %d s" % STEP_TIMEOUT)
        return False, lines
    if e is not None:
        lines.append("3. Live TV categories: FAILED - %s" % _err_text(provider, e))
        return False, lines
    n = len(cats) if isinstance(cats, list) else 0
    if not n:
        lines.append("3. Live TV categories: NONE sent (%.1f s) - nothing to show" % took)
        return False, lines
    lines.append("3. Live TV categories: %d (%.1f s)" % (n, took))
    return ok, lines


def _test_stalker(provider, lines):
    from .stalker_client import handshake, _request
    token, e, took = _with_limit(lambda: handshake(provider, timeout=STEP_TIMEOUT), STEP_TIMEOUT * 2 + 2)
    if e == "timeout":
        lines.append("2. Portal login: no answer - portal down or very slow")
        return False, lines
    if e is not None:
        lines.append("2. Portal login: FAILED - %s" % _err_text(provider, e))
        return False, lines
    lines.append("2. Portal login: OK (%.1f s) - MAC address accepted" % took)

    url = "%s/server/load.php?type=itv&action=get_genres&JsHttpRequest=1-xml" % provider.base_url
    data, e, took = _with_limit(lambda: _request(url, provider.mac, token, timeout=STEP_TIMEOUT),
                                STEP_TIMEOUT + 2)
    if e == "timeout":
        lines.append("3. Live TV categories: no answer after %d s" % STEP_TIMEOUT)
        return False, lines
    if e is not None:
        lines.append("3. Live TV categories: FAILED - %s" % _err_text(provider, e))
        return False, lines
    genres = (data or {}).get("js") if isinstance(data, dict) else None
    n = len(genres) if isinstance(genres, list) else 0
    if not n:
        lines.append("3. Live TV categories: NONE sent (%.1f s)" % took)
        lines.append("   - subscription may have ended for this MAC address")
        return False, lines
    lines.append("3. Live TV categories: %d (%.1f s)" % (n, took))
    return True, lines
