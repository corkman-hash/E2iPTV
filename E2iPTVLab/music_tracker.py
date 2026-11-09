# =============================================================================
# E2iPTV - Tracker music (.mod .s3m .xm .it ...)
#
# The box's player can't play tracker files, so they are turned into a
# .wav just before they play, using libmodplug (in the image's own feed:
# libmodplug1) through Python's ctypes - nothing else to install. The
# .wav goes into a cache on the box (<data>/music_cache), which keeps the
# last CACHE_KEEP of them; the originals on the NAS are never changed.
# =============================================================================

import ctypes
import ctypes.util
import hashlib
import os
import threading
import time
import wave

from .paths import DATA

TRACKER_EXT = (".mod", ".s3m", ".xm", ".it", ".mtm", ".669", ".med", ".stm",
               ".ult", ".far", ".amf", ".okt", ".ptm", ".mdl", ".dmf", ".dbm")
CACHE_DIR = os.path.join(DATA, "music_cache")
CACHE_KEEP = 20
RATE = 44100
MAX_SECONDS = 20 * 60          # a tune that loops forever stops here

_lock = threading.Lock()


def _log(msg):
    try:
        from .music import _log as mlog
        mlog("tracker: " + msg)
    except Exception:
        pass


# Inside the "Tracker Files" folder, files are recognised by what is in
# them, not their name: S3M files without ".s3m", Amiga-style "mod.tune"
# names and so on. These are never looked inside:
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".txt", ".nfo", ".diz",
            ".pdf", ".url", ".htm", ".html", ".doc", ".rtf", ".cue", ".log",
            ".zip", ".rar", ".lha", ".lzh", ".lzx", ".7z", ".gz", ".tar",
            ".cmf", ".mid", ".midi", ".sid", ".exe", ".iso", ".db", ".ini",
            ".json", ".xml", ".m3u", ".m3u8", ".pls", ".wav", ".mp3",
            ".flac", ".ogg", ".m4a", ".mp4", ".aac", ".wma", ".opus")

# (where in the file, what it says, format)
_MAGIC = (
    (0, b"Extended Module:", "xm"), (0, b"IMPM", "it"), (44, b"SCRM", "s3m"),
    (0, b"MTM", "mtm"), (0, b"ASYLUM Music Format", "amf"), (0, b"AMF", "amf"),
    (20, b"!Scream!", "stm"), (20, b"BMOD2STM", "stm"), (0, b"MAS_UTrack_V", "ult"),
    (0, b"FAR\xfe", "far"), (0, b"OKTASONG", "okt"), (44, b"PTMF", "ptm"),
    (0, b"DMDL", "mdl"), (0, b"DDMF", "dmf"), (0, b"DBM0", "dbm"), (0, b"MMD", "med"),
    (0, b"if", "669"), (0, b"JN", "669"),
)
_MOD_TAGS = (b"M.K.", b"M!K!", b"M&K!", b"FLT4", b"FLT8", b"4CHN", b"6CHN",
             b"8CHN", b"CD81", b"OKTA", b"OCTA", b"16CN", b"32CN", b"TDZ4")
_sniffed = {}


def in_tracker_folder(path):
    return "/tracker files/" in path.lower()


def maybe_tracker(name):
    """A file name worth looking inside (Tracker Files folder only)."""
    if name.startswith("."):
        return False
    return not name.lower().endswith(SKIP_EXT)


def sniff(path):
    """The tracker format from the start of the file ("s3m", "mod" ...),
    or "". Remembered, so each file is only read once."""
    if path in _sniffed:
        return _sniffed[path]
    kind = ""
    try:
        with open(path, "rb") as f:
            h = f.read(1084)
        for off, magic, k in _MAGIC:
            if h[off:off + len(magic)] == magic:
                kind = k
                break
        if not kind and len(h) >= 1084:
            tag = h[1080:1084]
            if tag in _MOD_TAGS or (tag[2:] == b"CH" and tag[:2].isdigit()) \
                    or (tag[1:] == b"CHN" and tag[:1].isdigit()):
                kind = "mod"
    except Exception:
        pass
    _sniffed[path] = kind
    return kind


def is_tracker(path):
    if path.lower().endswith(TRACKER_EXT):
        return True
    name = os.path.basename(path)
    return in_tracker_folder(path) and maybe_tracker(name) and bool(sniff(path))


# -- the title inside the file ---------------------------------------------------

def _clean(raw):
    t = raw.split(b"\0", 1)[0].decode("latin-1", "replace")
    t = "".join(c if c.isprintable() else " " for c in t)
    return " ".join(t.split())


def header_title(path):
    """The song title stored in the file, or "" (only the first bytes of
    the file are read)."""
    try:
        with open(path, "rb") as f:
            h = f.read(64)
    except Exception:
        return ""
    ext = os.path.splitext(path)[1].lower()
    if ext not in TRACKER_EXT:
        ext = "." + (sniff(path) or "")
    t = ""
    if h[:17] == b"Extended Module: ":
        t = _clean(h[17:37])
    elif h[:4] == b"IMPM":
        t = _clean(h[4:30])
    elif h[44:48] == b"SCRM":
        t = _clean(h[0:28])
    elif h[:3] == b"MTM":
        t = _clean(h[4:24])
    elif ext == ".mod":
        t = _clean(h[0:20])
    # Some trackers leave the title as junk or the file name
    if len(t) < 2 or not any(c.isalpha() for c in t):
        return ""
    return t


# -- libmodplug ------------------------------------------------------------------

class _Settings(ctypes.Structure):
    # ModPlug_Settings (libmodplug 0.8.x), with room to spare after it
    _fields_ = [(n, ctypes.c_int) for n in (
        "mFlags", "mChannels", "mBits", "mFrequency", "mResamplingMode",
        "mStereoSeparation", "mMaxMixChannels", "mReverbDepth", "mReverbDelay",
        "mBassAmount", "mBassRange", "mSurroundDepth", "mSurroundDelay",
        "mLoopCount")] + [("_spare", ctypes.c_int * 16)]


_lib = [None, False]       # library, tried


def _modplug():
    if _lib[1]:
        return _lib[0]
    _lib[1] = True
    for name in (ctypes.util.find_library("modplug"), "libmodplug.so.1", "libmodplug.so"):
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
            break
        except Exception:
            lib = None
    else:
        lib = None
    if lib is None:
        _log("libmodplug not found - tracker files can't be played")
        return None
    lib.ModPlug_Load.restype = ctypes.c_void_p
    lib.ModPlug_Load.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.ModPlug_Unload.argtypes = [ctypes.c_void_p]
    lib.ModPlug_Read.restype = ctypes.c_int
    lib.ModPlug_Read.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    lib.ModPlug_GetName.restype = ctypes.c_char_p
    lib.ModPlug_GetName.argtypes = [ctypes.c_void_p]
    lib.ModPlug_GetLength.restype = ctypes.c_int
    lib.ModPlug_GetLength.argtypes = [ctypes.c_void_p]
    lib.ModPlug_GetSettings.argtypes = [ctypes.POINTER(_Settings)]
    lib.ModPlug_SetSettings.argtypes = [ctypes.POINTER(_Settings)]
    s = _Settings()
    lib.ModPlug_GetSettings(ctypes.byref(s))
    s.mFlags |= 1 | 2              # oversampling, noise reduction
    s.mChannels, s.mBits, s.mFrequency = 2, 16, RATE
    s.mResamplingMode = 3          # FIR (best)
    s.mLoopCount = 0               # play once
    lib.ModPlug_SetSettings(ctypes.byref(s))
    _lib[0] = lib
    return lib


def available():
    return _modplug() is not None


def _load(lib, path):
    with open(path, "rb") as f:
        data = f.read()
    mp = lib.ModPlug_Load(data, len(data))
    if not mp:
        raise ValueError("libmodplug can't read this file")
    return mp


def info(path):
    """{'title', 'length'} for the details panel."""
    out = {}
    t = header_title(path)
    lib = _modplug()
    if lib is not None:
        try:
            with _lock:
                mp = _load(lib, path)
                try:
                    if not t:
                        t = _clean(lib.ModPlug_GetName(mp) or b"")
                    ms = lib.ModPlug_GetLength(mp)
                    if ms > 0:
                        out["length"] = ms / 1000.0
                finally:
                    lib.ModPlug_Unload(mp)
        except Exception as e:
            _log("info %s: %s" % (os.path.basename(path), e))
    if t:
        out["title"] = t
    return out


# -- the cache ----------------------------------------------------------------------

def _cache_path(path):
    try:
        st = os.stat(path)
        key = "%s|%d|%d" % (path, st.st_size, int(st.st_mtime))
    except Exception:
        key = path
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode("utf-8", "replace")).hexdigest() + ".wav")


def cached(path):
    """The ready .wav for this tune, or None."""
    p = _cache_path(path)
    if os.path.isfile(p):
        try:
            os.utime(p, None)       # recently used: kept longest
        except Exception:
            pass
        return p
    return None


def _prune():
    try:
        files = [os.path.join(CACHE_DIR, n) for n in os.listdir(CACHE_DIR)
                 if n.endswith(".wav")]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for p in files[CACHE_KEEP:]:
            os.remove(p)
        for n in os.listdir(CACHE_DIR):
            if n.endswith(".part"):
                p = os.path.join(CACHE_DIR, n)
                if time.time() - os.path.getmtime(p) > 600:
                    os.remove(p)
    except Exception:
        pass


def render(path):
    """Turn the tune into a .wav in the cache (if not there already) and
    return its path, or None if it can't be done."""
    lib = _modplug()
    if lib is None:
        return None
    with _lock:                     # one at a time; libmodplug settings are shared
        done = cached(path)
        if done:
            return done
        out = _cache_path(path)
        tmp = out + ".part"
        t0 = time.time()
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            mp = _load(lib, path)
            try:
                ms = lib.ModPlug_GetLength(mp)
                limit = MAX_SECONDS
                if ms > 0:
                    limit = min(MAX_SECONDS, ms / 1000.0 + 5)
                max_bytes = int(limit * RATE) * 4
                buf = ctypes.create_string_buffer(65536)
                total = 0
                w = wave.open(tmp, "wb")
                try:
                    w.setnchannels(2)
                    w.setsampwidth(2)
                    w.setframerate(RATE)
                    while total < max_bytes:
                        n = lib.ModPlug_Read(mp, buf, len(buf))
                        if n <= 0:
                            break
                        w.writeframes(buf.raw[:n])
                        total += n
                finally:
                    w.close()
            finally:
                lib.ModPlug_Unload(mp)
            if total <= 0:
                raise ValueError("no sound came out")
            os.replace(tmp, out)
            _log("%s ready: %.0f s of sound in %.1f s" % (
                os.path.basename(path), total / 4.0 / RATE, time.time() - t0))
        except Exception as e:
            _log("%s failed: %s" % (os.path.basename(path), e))
            try:
                os.remove(tmp)
            except Exception:
                pass
            return None
    _prune()
    return out
