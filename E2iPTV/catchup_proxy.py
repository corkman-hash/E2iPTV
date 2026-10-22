# =============================================================================
# E2iPTV — catch-up relay: keeps catch-up sound working
#
# Catch-up streams can switch sound format part-way through (e.g. AAC ->
# AC-3 where the provider restarted its stream) while announcing the
# change with an unchanged version number, so the player keeps decoding
# the new sound as the old format: silence. Recordings are corrected as
# they're written (recording/ts_fixer.py); catch-up plays straight from
# the provider, so it's passed through this small relay instead:
#
#   wrap(url) -> "http://127.0.0.1:<port>/cu/<token>.ts"
#
# The player fetches that; the relay fetches the real address (passing on
# Range for skipping, following redirects) and corrects the track-list
# version numbers on the way through. Nothing else in the stream changes,
# and its length is unchanged, so skipping works exactly as before.
# Only this box can reach the relay (127.0.0.1), and the address the
# player sees holds a random token, not the login.
#
# If the relay can't start, wrap() returns the original address and
# catch-up plays directly, as it always did.
# =============================================================================

import os
import threading

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    import urllib.request as _ureq
except ImportError:                      # pragma: no cover (Python 2)
    _ureq = None

from .recording.ts_fixer import StreamFixer, StableFixer

_lock = threading.Lock()
_server = None
_port = None
_targets = {}        # token -> (url, StableFixer)
_MAX_TARGETS = 20    # forget the oldest catch-ups beyond this

_PASS_HEADERS = ("Content-Type", "Content-Length", "Content-Range",
                 "Accept-Ranges", "Last-Modified", "ETag")


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # keep enigma2's log quiet
        pass

    def _target(self):
        name = self.path.split("?", 1)[0].rsplit("/", 1)[-1]
        token = name.split(".", 1)[0]
        with _lock:
            return _targets.get(token)

    def do_HEAD(self):
        self._relay(head=True)

    def do_GET(self):
        self._relay(head=False)

    def _relay(self, head):
        target = self._target()
        if not target:
            self.send_error(404)
            return
        url, fixer = target
        req = _ureq.Request(url, method="HEAD" if head else "GET")
        for h in ("Range", "User-Agent", "Icy-MetaData"):
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)
        try:
            up = _ureq.urlopen(req, timeout=20)
        except Exception as e:
            code = getattr(e, "code", 502) or 502
            try:
                self.send_error(int(code))
            except Exception:
                pass
            return
        try:
            self.send_response(up.getcode() or 200)
            has_len = False
            for h in _PASS_HEADERS:
                v = up.headers.get(h)
                if v:
                    self.send_header(h, v)
                    has_len = has_len or h == "Content-Length"
            if not has_len:
                # Unknown length: close the connection to mark the end.
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if head:
                return
            sf = StreamFixer(fixer)
            while True:
                chunk = up.read(65536)
                if not chunk:
                    break
                try:
                    out = sf.feed(chunk)
                except Exception:
                    out = sf.carry + chunk      # never lose data over the fix
                    sf = None
                    self.wfile.write(out)
                    break
                self.wfile.write(out)
            if sf is None:
                # corrector stepped aside - pass the rest straight through
                while True:
                    chunk = up.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            else:
                self.wfile.write(sf.flush())
        except Exception:
            pass         # player closed the connection (skip / stop) - normal
        finally:
            try:
                up.close()
            except Exception:
                pass


def _ensure_server():
    global _server, _port
    if _server is not None:
        return True
    if _ureq is None:
        return False
    try:
        srv = _Server(("127.0.0.1", 0), _Handler)
    except Exception as e:
        print("[E2iPTV] catch-up relay could not start:", e)
        return False
    _server, _port = srv, srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return True


def wrap(url):
    """Local relay address for a catch-up url (or the url itself if the
    relay isn't available)."""
    if not url or "/timeshift/" not in url:
        return url
    try:
        with _lock:
            if not _ensure_server():
                return url
            for token, (u, _f) in _targets.items():
                if u == url:
                    break
            else:
                token = os.urandom(8).hex()
                if len(_targets) >= _MAX_TARGETS:
                    _targets.pop(next(iter(_targets)))
                _targets[token] = (url, StableFixer())
            return "http://127.0.0.1:%d/cu/%s.ts" % (_port, token)
    except Exception as e:
        print("[E2iPTV] catch-up relay:", e)
        return url
