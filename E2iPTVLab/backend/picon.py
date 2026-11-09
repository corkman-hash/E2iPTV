# =============================================================================
# E2iPTV Backend — Picons
#
# Standalone: uses only the logo each provider itself supplies for every
# channel (Xtream "stream_icon", Stalker "logo"). Nothing from BMX,
# EPGImport or any other plugin is read.
#
# Logos are downloaded once, in the background, and kept permanently in
#     <picon folder>/<provider_key>/<stream_id>.png|.jpg
# The picon folder defaults to /hdd/picon/ and can be changed in
# Settings -> Other -> Picons. Logos already in that folder are used as
# they are; missing ones are downloaded into it. Keyed by provider AND
# stream_id, so two providers using the same stream number can never get
# each other's logo, and nothing in the folder's top level (where the
# box's own picons live) is ever touched.
#
# A logo that can't be downloaded (dead link, not an image, too big) is
# remembered with a "<stream_id>.none" marker and not retried for a day.
# =============================================================================

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import os
import re
import ssl
import threading
import time
import weakref

try:
    from queue import LifoQueue, Empty
except ImportError:
    from Queue import LifoQueue, Empty

try:
    import urllib.request as _urlreq
except ImportError:
    import urllib2 as _urlreq

DEFAULT_PICON_ROOT = HDD + "/picon/"
_OLD_PICON_ROOT = HDD + "/e2iptv_picons"      # previous fixed location
_SETTINGS_FILE = DATA + "/cache/app_settings.json"
_SETTINGS_KEY = "picon_path"

DOWNLOAD_WORKERS = 4
DOWNLOAD_TIMEOUT = 5
MAX_BYTES = 300 * 1024        # ignore anything over 300 KB
FAIL_RETRY_SECS = 24 * 3600
# Decoded pixmaps kept in memory. Was 400, which with full-size
# provider logos (often several hundred pixels square, some far bigger)
# could reach hundreds of MB once decoded and run the box out of memory.
# A screen shows about 10 rows, so a small cache is plenty.
PIXMAP_CACHE_MAX = 80

# Largest logo (in pixels, width x height) we'll load. A 56x58 slot
# never needs more; anything bigger costs memory for nothing once decoded.
MAX_PIXELS = 512 * 512
REFRESH_DELAY = 3.0           # batch UI refreshes after downloads

_EXTS = (".png", ".jpg")
_UA = "Mozilla/5.0 (E2iPTV)"

# Newest request first: the rows on screen right now are always the
# last ones asked for, so they jump ahead of anything queued from rows
# you've already scrolled past.
_queue = LifoQueue()
_queued = set()
_lock = threading.Lock()
_workers_started = False
from collections import OrderedDict
_pixmaps = OrderedDict()      # path -> (mtime, pixmap), least recently used first
_size_ok = set()              # logo files already checked for size
_too_big = set()              # logo files skipped for being oversized
_refresh_callbacks = []       # weak refs to bound methods
_refresh_pending = False
_root = None


# -----------------------------------------------------------------------------
# Picon folder setting
# -----------------------------------------------------------------------------

def get_root():
    """The picon folder currently in use (Settings -> Other -> Picons)."""
    global _root
    if _root is None:
        path = DEFAULT_PICON_ROOT
        try:
            import json
            with open(_SETTINGS_FILE) as f:
                path = json.load(f).get(_SETTINGS_KEY) or path
        except Exception:
            pass
        _root = path
        _migrate_old_folder(path)
    return _root


def set_root(path):
    """Change the picon folder and save it. Logos already in the new
    folder are used; missing ones are downloaded there from now on."""
    global _root
    import json
    path = path if path.endswith("/") else path + "/"
    data = {}
    try:
        with open(_SETTINGS_FILE) as f:
            data = json.load(f)
    except Exception:
        pass
    data[_SETTINGS_KEY] = path
    try:
        with open(_SETTINGS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass
    _root = path
    _pixmaps.clear()


def _migrate_old_folder(root):
    """One-off: move logos from the old fixed /hdd/e2iptv_picons folder
    into the picon folder, so nothing already downloaded is fetched
    again. Same disk, so each provider folder is a quick rename."""
    try:
        if not os.path.isdir(_OLD_PICON_ROOT):
            return
        if not os.path.isdir(root):
            os.makedirs(root)
        for sub in os.listdir(_OLD_PICON_ROOT):
            src = os.path.join(_OLD_PICON_ROOT, sub)
            dst = os.path.join(root, sub)
            if os.path.isdir(src) and not os.path.exists(dst):
                try:
                    os.rename(src, dst)
                except OSError:
                    import shutil
                    shutil.move(src, dst)
        if not os.listdir(_OLD_PICON_ROOT):
            os.rmdir(_OLD_PICON_ROOT)
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

def _safe(text):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(text or "")) or "_"


def _provider_key(channel):
    return _safe(
        getattr(channel, "provider_key", "")
        or getattr(channel, "source_id", "")
    )


def _dir_for(channel):
    return os.path.join(get_root(), _provider_key(channel))


def _base(channel):
    return os.path.join(_dir_for(channel), _safe(channel.stream_id))


def _existing_file(channel):
    base = _base(channel)
    for ext in _EXTS:
        path = base + ext
        if os.path.exists(path):
            return path
    return None


def _recently_failed(channel):
    try:
        return time.time() - os.path.getmtime(_base(channel) + ".none") < FAIL_RETRY_SECS
    except OSError:
        return False


# -----------------------------------------------------------------------------
# Logo URL
# -----------------------------------------------------------------------------

def _find_provider(channel):
    try:
        from ..state import state
        key = str(getattr(channel, "provider_key", "") or "")
        for _name, p in (state.providers or []):
            if str(getattr(p, "provider_key", "")) == key:
                return p
    except Exception:
        pass
    return None


def _logo_urls(channel):
    """Candidate download URLs for this channel's own logo."""
    logo = (getattr(channel, "stream_icon", "") or "").strip()
    if not logo:
        return []
    if logo.startswith("http://") or logo.startswith("https://"):
        return [logo]

    # Stalker portals often give just a file name or a relative path.
    provider = _find_provider(channel)
    base = (getattr(provider, "base_url", "") or "").rstrip("/")
    if not base:
        return []
    m = re.match(r"(https?://[^/]+)", base)
    host = m.group(1) if m else base
    if logo.startswith("/"):
        return [host + logo]
    return [
        base + "/misc/logos/320/" + logo,
        host + "/stalker_portal/misc/logos/320/" + logo,
        base + "/misc/logos/240/" + logo,
    ]


# -----------------------------------------------------------------------------
# Download workers
# -----------------------------------------------------------------------------

def _ssl_context():
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    except Exception:
        return None


_SSL = _ssl_context()


def _fetch(url):
    req = _urlreq.Request(url, headers={"User-Agent": _UA})
    kwargs = {"timeout": DOWNLOAD_TIMEOUT}
    if _SSL is not None and url.startswith("https://"):
        kwargs["context"] = _SSL
    resp = _urlreq.urlopen(req, **kwargs)
    try:
        data = resp.read(MAX_BYTES + 1)
    finally:
        resp.close()
    if len(data) > MAX_BYTES:
        return None
    return data


def _png_too_big(data):
    """True if a PNG's header says it's larger than MAX_PIXELS."""
    try:
        import struct
        w, h = struct.unpack(">II", data[16:24])
        return w * h > MAX_PIXELS
    except Exception:
        return False


def _jpg_too_big(data):
    """True if a JPEG's frame header says it's larger than MAX_PIXELS."""
    try:
        import struct
        i = 2
        n = len(data)
        while i + 9 <= n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w * h > MAX_PIXELS
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg
    except Exception:
        pass
    return False


def _image_ext(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    return None


def _download(channel):
    folder = _dir_for(channel)
    base = _base(channel)
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
    except OSError:
        pass

    for url in _logo_urls(channel):
        try:
            data = _fetch(url)
        except Exception:
            continue
        ext = _image_ext(data or b"")
        if not ext:
            continue
        if _png_too_big(data) if ext == ".png" else _jpg_too_big(data):
            continue          # too big to be worth decoding for a picon
        tmp = base + ext + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.rename(tmp, base + ext)
            try:
                os.remove(base + ".none")
            except OSError:
                pass
            return True
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass

    try:
        with open(base + ".none", "w") as f:
            f.write(str(int(time.time())))
    except Exception:
        pass
    return False


def _worker():
    while True:
        try:
            key, channel = _queue.get(timeout=60)
        except Empty:
            continue
        try:
            if _download(channel):
                _schedule_refresh()
        except Exception:
            pass
        finally:
            with _lock:
                _queued.discard(key)


def _ensure_workers():
    global _workers_started
    with _lock:
        if _workers_started:
            return
        _workers_started = True
    for _ in range(DOWNLOAD_WORKERS):
        t = threading.Thread(target=_worker, name="e2iptv-picons")
        t.daemon = True
        t.start()


def _queue_download(channel):
    if not _logo_urls(channel) or _recently_failed(channel):
        return
    key = (_provider_key(channel), str(channel.stream_id))
    with _lock:
        if key in _queued:
            return
        _queued.add(key)
    _ensure_workers()
    _queue.put((key, channel))


# -----------------------------------------------------------------------------
# UI refresh after downloads
# -----------------------------------------------------------------------------

def register_refresh(callback):
    """Ask for `callback` (a bound method, e.g. a screen's _b3_refresh)
    to be called on the GUI thread shortly after new picons arrive.
    Held weakly, so a closed screen is simply dropped."""
    if callback is None:
        return
    try:
        ref = weakref.WeakMethod(callback)
    except TypeError:
        return
    for existing in _refresh_callbacks:
        if existing() == callback:
            return
    _refresh_callbacks.append(ref)


def _schedule_refresh():
    global _refresh_pending
    with _lock:
        if _refresh_pending:
            return
        _refresh_pending = True
    try:
        from twisted.internet import reactor
        reactor.callFromThread(reactor.callLater, REFRESH_DELAY, _run_refresh)
    except Exception:
        with _lock:
            _refresh_pending = False


_hold_until = 0.0


def hold_refresh(seconds):
    """Don't repaint for new logos for `seconds` (called while the user
    is scrolling, so repaints don't land in the middle of it)."""
    global _hold_until
    _hold_until = max(_hold_until, time.time() + seconds)


def _run_refresh():
    global _refresh_pending
    wait = _hold_until - time.time()
    if wait > 0:
        try:
            from twisted.internet import reactor
            reactor.callLater(wait + 0.2, _run_refresh)
            return
        except Exception:
            pass
    with _lock:
        _refresh_pending = False
    alive = []
    for ref in list(_refresh_callbacks):
        cb = ref()
        if cb is None:
            continue
        alive.append(ref)
        try:
            cb()
        except Exception:
            pass
    _refresh_callbacks[:] = alive


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def picon_path_for(channel):
    """On-disk path of this channel's picon, or None. If there isn't one
    yet, a background download is queued and the screen is refreshed
    when it arrives. Safe to call from the GUI thread: only cheap
    file-existence checks happen here."""
    path = _existing_file(channel)
    if path:
        return path
    _queue_download(channel)
    return None


def picon_pixmap_for(channel):
    """Decoded pixmap for this channel's picon, or None. Pixmaps are
    cached in memory so scrolling doesn't re-decode the same images."""
    path = picon_path_for(channel)
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _pixmaps.get(path)
    if hit and hit[0] == mtime:
        _pixmaps.move_to_end(path)
        return hit[1]
    # Logos downloaded before the size limit existed: check the header
    # (a few bytes) before decoding, and skip oversized ones. Each file
    # is only checked once.
    if path in _too_big:
        return None
    if path not in _size_ok:
        try:
            with open(path, "rb") as f:
                head = f.read(65536)
            if _png_too_big(head) if path.endswith(".png") else _jpg_too_big(head):
                _too_big.add(path)
                return None
            _size_ok.add(path)
        except Exception:
            return None
    try:
        from Tools.LoadPixmap import LoadPixmap
        pm = LoadPixmap(path)
    except Exception:
        pm = None
    if pm is None:
        return None
    # Drop only the least recently shown logos. (It used to empty the
    # whole cache once full, so in any category with more channels than
    # the cache held, every scroll re-decoded every logo on screen.)
    _pixmaps[path] = (mtime, pm)
    while len(_pixmaps) > PIXMAP_CACHE_MAX:
        _pixmaps.popitem(last=False)
    return pm


def _provider_folders():
    """This plugin's own per-provider folders inside the picon folder —
    only folders named after a configured provider, so the box's own
    picons and anything else in there are never counted or deleted."""
    root = get_root()
    keys = set()
    try:
        from ..state import state
        for _name, p in (state.providers or []):
            keys.add(_safe(getattr(p, "provider_key", "")))
    except Exception:
        pass
    return [os.path.join(root, k) for k in keys if os.path.isdir(os.path.join(root, k))]


def cached_count():
    """Number of logos currently saved, across all providers."""
    total = 0
    for folder in _provider_folders():
        try:
            total += sum(1 for n in os.listdir(folder) if n.endswith(_EXTS))
        except OSError:
            pass
    return total


def clear_cache():
    """Delete the downloaded logos (and failure markers) of every
    configured provider, so they are fetched fresh as channels are next
    shown. Only this plugin's provider folders are removed."""
    import shutil
    _pixmaps.clear()
    for folder in _provider_folders():
        try:
            shutil.rmtree(folder)
        except OSError:
            pass
