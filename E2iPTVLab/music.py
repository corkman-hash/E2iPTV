# =============================================================================
# E2iPTV — Music
#
# Plays the audio files in a folder on the NAS (or any folder the box can
# see), with the folders shown as they are on disk - for the usual
# Genre / Artist / Album layout that's three levels of folders, then the
# tracks.
#
#   MusicBrowser   the folder list (a rail screen, like Favourites)
#   MusicPlayer    the Now Playing screen: cover, title/artist/album,
#                  time and a progress bar
#   _Queue         the play list itself. Lives on its own, so music keeps
#                  playing while you browse; it stops (and whatever was
#                  playing before comes back) when you leave Music.
#
# The music folder is chosen in Settings -> Playback -> Music folder and
# kept in E2iPTV_Data/cache/music.json ("root"), along with the last
# folder you were in ("last"). There's no default: until one is chosen,
# Music says where to set it.
#
# Files are played by the box's own player (service 4097, GStreamer), so
# anything it can play works: MP3, FLAC, M4A/AAC, OGG, Opus, WAV, ...
# Tags and embedded cover pictures are read with ffmpeg, in the
# background, so a slow NAS never holds up the remote.
# =============================================================================

from . import theme as _T
import json
import math
import os
from array import array
import random
import re
import subprocess
import sys
import threading
import time

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.MultiContent import MultiContentEntryText
from .compat import MultiContentEntryText  # square rows where the image has no rounded ones
from Components.GUIComponent import GUIComponent
from Tools.LoadPixmap import LoadPixmap
from enigma import (
    eTimer, eListbox, eListboxPythonMultiContent, eServiceReference,
    eActionMap, iPlayableService, gFont, eSize, ePoint,
    RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER,
)

from .paths import DATA, LOG
from .b1common import B1_ICON_PATH
from . import music_index as _mi
from . import music_playlists as _pls
from . import music_tracker as _trk

DEFAULT_ROOT = ""                   # none: chosen in Settings -> Playback
SETTINGS_PATH = os.path.join(DATA, "cache", "music.json")
LOG_PATH = LOG("e2iptv_music.log")
FFMPEG = "/usr/bin/ffmpeg"

AUDIO_EXT = (".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus",
             ".wav", ".wma", ".ape", ".wv", ".aiff", ".aif", ".alac",
             ".mka", ".mp2", ".ac3", ".dsf", ".dff", ".mp4", ".m4r") + _trk.TRACKER_EXT
COVER_NAMES = ("folder", "cover", "front", "albumart", "album", "albumartlarge")
IMAGE_EXT = (".jpg", ".jpeg", ".png")
MAX_TRACKS = 10000         # Play all / Shuffle on a whole genre: plenty

# Remote keys, caught raw (the media keys aren't in every keymap).
KEY_STOP = 128
KEY_PLAYPAUSE = (164, 207, 119)   # Play/Pause, Play, Pause
KEY_NEXT = 163
KEY_PREV = 165
KEY_FF = 208
KEY_REW = 168
KEY_MENU = 139


def _log(msg):
    try:
        with open(LOG_PATH, "a") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# -- settings -----------------------------------------------------------------

def _settings():
    try:
        with open(SETTINGS_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_settings(**kw):
    data = _settings()
    data.update(kw)
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, SETTINGS_PATH)
    except Exception as e:
        _log("settings save: %s" % e)


DELETED_DIR = "Deleted"     # Menu -> Delete this track moves files here


def music_root():
    """The chosen music folder, or "" if none has been chosen yet."""
    root = str(_settings().get("root") or DEFAULT_ROOT).strip()
    if not root:
        return ""
    return root.rstrip("/") or "/"


def set_music_root(path):
    """Settings -> Playback -> Music folder. Forgets the last folder
    (it was inside the old one)."""
    data = _settings()
    data["root"] = str(path or "").rstrip("/") or "/"
    data.pop("last", None)
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, SETTINGS_PATH)
    except Exception as e:
        _log("settings save: %s" % e)


# -- files --------------------------------------------------------------------

def _natural_key(name):
    return [int(t) if t.isdigit() else t.casefold()
            for t in re.split(r"(\d+)", name)]


def is_audio(name):
    return name.lower().endswith(AUDIO_EXT)


def is_music_file(full, name):
    """is_audio, plus tracker files known only by what's in them (inside
    the Tracker Files folder: S3M files without '.s3m' and the like)."""
    if is_audio(name):
        return True
    return _trk.in_tracker_folder(full) and _trk.maybe_tracker(name) \
        and bool(_trk.sniff(full))


def list_folder(path):
    """(folders, tracks) in a folder, each a sorted list of (name, path).
    Hidden files (Mac "._" files etc.) are left out."""
    folders, tracks = [], []
    for name in os.listdir(path):
        if name.startswith("."):
            continue
        full = os.path.join(path, name)
        try:
            if os.path.isdir(full):
                if name == DELETED_DIR and path.rstrip("/") == music_root():
                    continue           # the bin (Delete this track)
                folders.append((name, full))
            elif is_music_file(full, name):
                tracks.append((name, full))
        except Exception:
            pass
    folders.sort(key=lambda x: _natural_key(x[0]))
    tracks.sort(key=lambda x: _natural_key(x[0]))
    return folders, tracks


def gather_tracks(path, limit=MAX_TRACKS):
    """Every track under a folder, folder by folder in name order."""
    out = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted((d for d in dirnames if not d.startswith(".")),
                             key=_natural_key)
        for name in sorted(filenames, key=_natural_key):
            if not name.startswith(".") and \
                    is_music_file(os.path.join(dirpath, name), name):
                out.append(os.path.join(dirpath, name))
                if len(out) >= limit:
                    return out
    return out


def list_name(name, path):
    """(name, type) for the file list: 'mod.axelf' -> ('axelf', '.mod')
    like 'axelf.mod'; a tracker file with no extension shows the type
    found inside it ('SATELL' -> ('SATELL', '.s3m'))."""
    m = re.match(r"(?i)^(mod|s3m|xm|it|med|okt|stm|mtm|669|ptm|ult|far)\.(.+)$", name)
    if m and not name.lower().endswith(AUDIO_EXT):
        return m.group(2), "." + m.group(1).lower()
    base, ext = os.path.splitext(name)
    if not ext and _trk.in_tracker_folder(path):
        kind = _trk.sniff(path)
        if kind:
            return name, "." + kind
    return base, ext


def track_name(path):
    """File name without the extension or a leading track number."""
    base = os.path.basename(path)
    m = re.match(r"(?i)^(mod|s3m|xm|it|med|okt|stm|mtm|669|ptm|ult|far)\.(.+)$", base)
    if m and not base.lower().endswith(AUDIO_EXT):
        return m.group(2)               # Amiga style: mod.tune name
    name = os.path.splitext(base)[0]
    return re.sub(r"^\s*\d{1,3}\s*[-._)]?\s+", "", name) or name


def path_info(path):
    """(artist, album) from the folders: .../Artist/Album/track."""
    album_dir = os.path.dirname(path)
    artist_dir = os.path.dirname(album_dir)
    root = music_root()
    album = os.path.basename(album_dir)
    artist = os.path.basename(artist_dir) if artist_dir.startswith(root) \
        and artist_dir != root else ""
    return artist, album


def find_folder_cover(folder):
    try:
        names = os.listdir(folder)
    except Exception:
        return None
    images = [n for n in names if n.lower().endswith(IMAGE_EXT)
              and not n.startswith(".")]
    for want in COVER_NAMES:
        for n in images:
            if os.path.splitext(n)[0].lower() == want:
                return os.path.join(folder, n)
    return os.path.join(folder, sorted(images)[0]) if images else None


_tag_cache = {}


def read_tags(path):
    """Tags and length via ffmpeg: {'title','artist','album','track',
    'length' (seconds)}. Cached; {} if ffmpeg can't read the file."""
    if path in _tag_cache:
        return _tag_cache[path]
    if _trk.is_tracker(path):
        tags = _trk.info(path)
        _tag_cache[path] = tags
        return tags
    tags = {}
    try:
        p = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", path,
                            "-f", "ffmetadata", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=15)
        for line in p.stdout.decode("utf-8", "replace").splitlines():
            if "=" in line and not line.startswith(";"):
                k, v = line.split("=", 1)
                k = k.strip().lower()
                if k in ("title", "artist", "album", "track", "album_artist") and v.strip():
                    tags.setdefault(k, v.strip())
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                      p.stderr.decode("utf-8", "replace"))
        if m:
            tags["length"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 \
                + float(m.group(3))
    except Exception as e:
        _log("tags %s: %s" % (os.path.basename(path), e))
    _tag_cache[path] = tags
    return tags


_cover_n = [0]


def make_cover(path, size=600):
    """A cover picture for this track, scaled to fit `size`, written to
    /tmp. The album folder's own picture (folder.jpg, cover.jpg, ...) if
    there is one, otherwise the picture inside the file. None if neither."""
    src = find_folder_cover(os.path.dirname(path))
    _cover_n[0] = (_cover_n[0] + 1) % 4
    out = "/tmp/e2iptv_music_cover%d.jpg" % _cover_n[0]
    for source in ([src] if src else []) + [path]:
        try:
            if os.path.exists(out):
                os.remove(out)
            subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-y",
                            "-loglevel", "error", "-i", source, "-an",
                            "-frames:v", "1", "-vf",
                            "scale=%d:%d:force_original_aspect_ratio=decrease" % (size, size),
                            "-q:v", "3", out],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                return out
        except Exception as e:
            _log("cover %s: %s" % (os.path.basename(source), e))
    return None


def _get_volume():
    try:
        from enigma import eDVBVolumecontrol
        return int(eDVBVolumecontrol.getInstance().getVolume())
    except Exception as e:
        _log("volume read: %s" % e)
        return None


def _set_volume(v):
    v = max(0, min(100, int(v)))
    try:
        from enigma import eDVBVolumecontrol
        vc = eDVBVolumecontrol.getInstance()
        try:
            vc.setVolume(v, v)
        except TypeError:
            vc.setVolume(v)
    except Exception as e:
        _log("volume set: %s" % e)
        return
    # Keep the box's own volume setting (and its on-screen bar) in step.
    try:
        from Components.config import config
        config.audio.volume.value = v
    except Exception:
        pass


def fmt_time(secs):
    secs = int(max(0, secs or 0))
    if secs >= 3600:
        return "%d:%02d:%02d" % (secs // 3600, secs // 60 % 60, secs % 60)
    return "%d:%02d" % (secs // 60, secs % 60)


# =============================================================================
# Sound levels for the VU meter
#
# The box's player doesn't hand the sound itself to plugins, so the track
# is read a second time, quietly in the background, into a small table of
# loudness every 1/40 s for each side. The meter then reads the table at
# the player's current position, so it stays in step through pauses,
# skips and seeks. The reading runs well ahead of the music, at low
# priority, and only about 20 s ahead of the music (no point racing
# through the whole track at once). Two ways, tried in this order (the
# first that works is used from then on):
#   ffmpeg  ffmpeg's own loudness filter (astats) measures it and prints
#           the numbers - all the sums done by ffmpeg, nothing here
#   gst     GStreamer (the box's own player engine) decodes the track to
#           plain sound and the loudness is worked out here (more work
#           for the plugin itself, so only if ffmpeg can't)
# =============================================================================

LEVEL_STEP = 0.025                 # seconds per table entry
_LEVEL_RATE = 8000                 # decode rate - plenty for a meter
_LEVEL_FRAME = int(_LEVEL_RATE * LEVEL_STEP)      # samples per entry
DB_FLOOR = -60.0                   # quietest level stored


def _db_byte(amplitude):
    """0..255 for -60..+3.75 dB (0 = silence) - 1/4 dB steps."""
    if amplitude <= 0:
        return 0
    db = 20.0 * math.log10(amplitude / 32768.0)
    return max(0, min(255, int((db - DB_FLOOR) * 4)))


def byte_db(v):
    return v / 4.0 + DB_FLOOR


GST_LAUNCH = "/usr/bin/gst-launch-1.0"
_method = []                       # the way that worked: "gst" / "ffmpeg"


def _popen(cmd):
    """Start a reader at low priority. Returns (process, error)."""
    err = None
    for pre in (["nice", "-n", "19"], []):
        try:
            return subprocess.Popen(pre + cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE), None
        except Exception as e:
            err = e
    return None, err


play_pos = [0.0]                   # where the music is (set by the screens)
READ_AHEAD = 20.0                  # seconds the readers keep ahead of it


def _pace(seconds_read, stopped):
    """Readers call this as they go: once they're READ_AHEAD in front of
    the music, wait for it (ffmpeg / GStreamer simply pause meanwhile).
    False if the music has jumped well ahead of the reader: it should
    give up rather than race to catch up (Now Playing starts a new one
    from the new place)."""
    while seconds_read - play_pos[0] > READ_AHEAD and not stopped():
        time.sleep(0.25)
    return play_pos[0] - seconds_read < 5.0


def _db_text_byte(text):
    """'-21.07' (dB) -> table byte; '-inf' / rubbish -> silence."""
    try:
        db = float(text)
    except ValueError:
        return 0
    if db != db or db < DB_FLOOR:              # nan / -inf
        return 0
    return max(0, min(255, int((db - DB_FLOOR) * 4)))


class Levels(object):
    """Loudness table for one track: rms_l/rms_r (average loudness, what
    the bars show) and pk_l/pk_r (peaks, the markers), one byte each
    per LEVEL_STEP."""

    _step = LEVEL_STEP

    def _count(self):
        return len(self.rms_l)

    def __init__(self, path, start=0.0):
        self.path = path
        self.start = start         # seconds into the track the table begins
        self.gave_up = False       # stopped because the music jumped ahead
        self.rms_l, self.rms_r = bytearray(), bytearray()
        self.pk_l, self.pk_r = bytearray(), bytearray()
        self.done = False
        self._stop = False
        self._proc = None
        threading.Thread(target=self._run, name="E2iPTV-Music-Levels",
                         daemon=True).start()

    def _ss(self):
        """Start ffmpeg part-way into the track (a jump ahead)."""
        return ["-ss", "%.2f" % self.start] if self.start > 0 else []

    def covers(self, pos):
        """Has (or will soon have, reading on from here) the levels for
        `pos`? False after a jump ahead past what's read, or back before
        where this table starts."""
        if self.gave_up:
            return False
        if self.done and not self._count():
            return True            # this way of reading doesn't work - no retrying
        end = self.start + self._count() * self._step
        if pos < self.start - 0.5:
            return False
        return pos <= end + (0.5 if self.done else 3.0)

    def stop(self):
        self._stop = True
        p = self._proc
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass

    def _run(self):
        started = time.time()
        order = _method + [m for m in ("ffmpeg", "gst") if m not in _method]
        used = None
        for way in order:
            if self._stop or self.gave_up:
                break
            try:
                msg = self._read_gst() if way == "gst" else self._read_ffmpeg()
            except Exception as e:
                msg = "error %r" % (e,)
            if self.rms_l:
                used = way
                if not _method:
                    _method.append(way)
                    _log("meter: measuring with %s" % way)
                break
            if not self._stop:
                _log("meter: %s gave no levels for %s%s" % (
                    way, os.path.basename(self.path),
                    (" - " + msg) if msg else ""))
        self.done = True
        _log("meter: read %s%s - %.0f s of levels in %.1f s%s%s" % (
            os.path.basename(self.path),
            (" from %.0f s" % self.start) if self.start else "",
            len(self.rms_l) * LEVEL_STEP,
            time.time() - started, (" with " + used) if used else "",
            " - stopped early" if self._stop else ""))

    def _finish(self, p):
        """Stop the reader; its error text if it produced nothing."""
        msg = ""
        try:
            if not self.rms_l and not self._stop:
                msg = p.stderr.read(2000).decode("utf-8", "replace").strip()
        except Exception:
            pass
        try:
            p.kill()
        except Exception:
            pass
        try:
            code = p.wait()
        except Exception:
            code = "?"
        self._proc = None
        if msg or (code and not self.rms_l and not self._stop):
            return ("exit %s: %s" % (code, msg.replace("\n", " | ")))[-500:]
        return ""

    # -- GStreamer: plain sound, measured here ------------------------------

    def _read_gst(self):
        if self.start > 0:
            return "not used after a jump"
        if not os.path.exists(GST_LAUNCH):
            return "gst-launch-1.0 not found"
        from urllib.parse import quote
        uri = "file://" + quote(self.path)
        p, err = _popen([
            GST_LAUNCH, "-q", "uridecodebin", "uri=" + uri, "!",
            "audioconvert", "!", "audioresample", "!",
            "audio/x-raw,format=S16LE,channels=2,rate=%d" % _LEVEL_RATE, "!",
            "fdsink", "fd=1"])
        if p is None:
            return "can't start: %s" % err
        self._proc = p
        if self._stop:
            p.kill()
        frame_bytes = _LEVEL_FRAME * 4             # 2 sides x 16 bit
        mul = __import__("operator").mul
        swap = sys.byteorder == "big"
        buf = b""
        try:
            while not self._stop:
                chunk = p.stdout.read(frame_bytes * 40)
                if not chunk:
                    break
                buf += chunk
                n = len(buf) // frame_bytes
                for k in range(n):
                    a = array("h")
                    a.frombytes(buf[k * frame_bytes:(k + 1) * frame_bytes])
                    if swap:
                        a.byteswap()
                    for side, rms, pk in ((a[0::2], self.rms_l, self.pk_l),
                                          (a[1::2], self.rms_r, self.pk_r)):
                        peak = max(max(side), -min(side))
                        ms = sum(map(mul, side, side)) / float(len(side))
                        rms.append(_db_byte(math.sqrt(ms)))
                        pk.append(_db_byte(peak))
                buf = buf[n * frame_bytes:]
                if not _pace(self.start + len(self.rms_l) * LEVEL_STEP, lambda: self._stop):
                    self.gave_up = True    # music jumped ahead - give up
                    break
        finally:
            msg = self._finish(p)
        return msg

    # -- ffmpeg: its loudness filter prints the numbers ---------------------

    def _read_ffmpeg(self):
        filt = ("aresample=%d,asetnsamples=n=%d:p=0,"
                "astats=metadata=1:reset=1:measure_perchannel=RMS_level+Peak_level"
                ":measure_overall=none,"
                "ametadata=mode=print:file=/dev/stdout" % (_LEVEL_RATE, _LEVEL_FRAME))
        msg = ""
        # The sound itself has to go somewhere: thrown away, in whichever
        # format this ffmpeg can write.
        for codec, fmt in (("pcm_s16le", "matroska"), ("ac3", "ac3"), ("aac", "adts")):
            if self._stop or self.gave_up:
                break
            p, err = _popen([FFMPEG, "-hide_banner", "-nostdin", "-y",
                             "-loglevel", "error"] + self._ss() + ["-i", self.path, "-vn",
                             "-af", filt, "-c:a", codec, "-f", fmt, "/dev/null"])
            if p is None:
                return "can't start: %s" % err
            self._proc = p
            if self._stop:
                p.kill()
            cur = {}

            def flush():
                if cur:
                    l_rms, l_pk = cur.get(1, (0, 0))
                    r_rms, r_pk = cur.get(2, (l_rms, l_pk))      # mono: same
                    self.rms_l.append(l_rms)
                    self.pk_l.append(l_pk)
                    self.rms_r.append(r_rms)
                    self.pk_r.append(r_pk)
                    cur.clear()
            try:
                for raw in p.stdout:
                    if self._stop:
                        break
                    line = raw.decode("ascii", "replace").strip()
                    if line.startswith("frame:"):
                        flush()
                        cur[0] = True
                        if len(self.rms_l) % 20 == 0:
                            if not _pace(self.start + len(self.rms_l) * LEVEL_STEP, lambda: self._stop):
                                self.gave_up = True    # music jumped ahead - give up
                                break
                    elif line.startswith("lavfi.astats."):
                        try:
                            key, val = line[13:].split("=", 1)
                            ch, what = key.split(".", 1)
                            ch = int(ch)
                        except ValueError:
                            continue
                        if ch not in (1, 2):
                            continue
                        rms, pk = cur.get(ch, (0, 0))
                        if what == "RMS_level":
                            rms = _db_text_byte(val)
                        elif what == "Peak_level":
                            pk = _db_text_byte(val)
                        cur[ch] = (rms, pk)
                flush()
            finally:
                msg = self._finish(p)
            if self.rms_l or self.gave_up:
                return ""
        return msg

    def at(self, t0, t1):
        """(rms_l, rms_r, peak_l, peak_r) in dB over t0..t1 seconds, or
        None if the decode hasn't got that far yet."""
        i0 = max(0, int((t0 - self.start) / LEVEL_STEP))
        i1 = max(i0 + 1, int((t1 - self.start) / LEVEL_STEP))
        n = min(len(self.rms_l), len(self.rms_r), len(self.pk_l), len(self.pk_r))
        if i0 >= n or t1 < self.start:
            return None
        i1 = min(i1, n)
        return (byte_db(max(self.rms_l[i0:i1])), byte_db(max(self.rms_r[i0:i1])),
                byte_db(max(self.pk_l[i0:i1])), byte_db(max(self.pk_r[i0:i1])))


# =============================================================================
# Spectrum: 16 bands, low to high
#
# ffmpeg splits the sound into 16 frequency bands with its own filters
# (fast, and the box's ffmpeg has them) and its loudness filter reports
# each band's level 25 times a second - read in the background like the
# VU levels, and shown from the table at the player's position.
# =============================================================================

SPEC_BANDS = 16
SPEC_RATE = 32000
SPEC_STEP = 0.04                   # seconds per table entry (25 a second)
SPEC_FREQS = [50.0 * (12500.0 / 50.0) ** (i / (SPEC_BANDS - 1.0))
              for i in range(SPEC_BANDS)]


def _spec_filter():
    width = math.log(12500.0 / 50.0, 2) / (SPEC_BANDS - 1)    # octaves
    parts = ["aformat=channel_layouts=mono,aresample=%d,asplit=%d%s" % (
        SPEC_RATE, SPEC_BANDS, "".join("[s%d]" % i for i in range(SPEC_BANDS)))]
    for i, f in enumerate(SPEC_FREQS):
        # two filters in a row: sharper edges, so a single note lights
        # its own band rather than half the display
        parts.append("[s%d]bandpass=f=%.0f:width_type=o:w=%.2f,"
                     "bandpass=f=%.0f:width_type=o:w=%.2f[b%d]" % (i, f, width, f, width, i))
    parts.append(
        "".join("[b%d]" % i for i in range(SPEC_BANDS)) +
        "join=inputs=%d:channel_layout=hexadecagonal,"
        "asetnsamples=n=%d:p=0,"
        "astats=metadata=1:reset=1:measure_perchannel=RMS_level:measure_overall=none,"
        "ametadata=mode=print:file=/dev/stdout,pan=mono|c0=c0"
        % (SPEC_BANDS, int(SPEC_RATE * SPEC_STEP)))
    return ";".join(parts)


class Spectrum(object):
    """Band levels for one track: bands[i] is a bytearray (dB bytes, as
    for the VU) per SPEC_STEP."""

    _step = SPEC_STEP

    def _count(self):
        return len(self)

    def __init__(self, path, start=0.0):
        self.path = path
        self.start = start         # seconds into the track the table begins
        self.gave_up = False       # stopped because the music jumped ahead
        self.bands = [bytearray() for _i in range(SPEC_BANDS)]
        self.done = False
        self._stop = False
        self._proc = None
        threading.Thread(target=self._run, name="E2iPTV-Music-Spectrum",
                         daemon=True).start()

    def _ss(self):
        """Start ffmpeg part-way into the track (a jump ahead)."""
        return ["-ss", "%.2f" % self.start] if self.start > 0 else []

    def covers(self, pos):
        """Has (or will soon have, reading on from here) the levels for
        `pos`? False after a jump ahead past what's read, or back before
        where this table starts."""
        if self.gave_up:
            return False
        if self.done and not self._count():
            return True            # this way of reading doesn't work - no retrying
        end = self.start + self._count() * self._step
        if pos < self.start - 0.5:
            return False
        return pos <= end + (0.5 if self.done else 3.0)

    def stop(self):
        self._stop = True
        p = self._proc
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass

    def __len__(self):
        return min(len(b) for b in self.bands)

    def _run(self):
        started = time.time()
        msg = ""
        filt = _spec_filter()
        for codec, fmt in (("pcm_s16le", "matroska"), ("ac3", "ac3"), ("aac", "adts")):
            if self._stop or self.gave_up:
                break
            p, err = _popen([FFMPEG, "-hide_banner", "-nostdin", "-y",
                             "-loglevel", "error"] + self._ss() + ["-i", self.path, "-vn",
                             "-filter_complex", filt,
                             "-c:a", codec, "-f", fmt, "/dev/null"])
            if p is None:
                msg = "can't start: %s" % err
                break
            self._proc = p
            if self._stop:
                p.kill()
            cur = {}

            def flush():
                if cur:
                    for i in range(SPEC_BANDS):
                        self.bands[i].append(cur.get(i + 1, 0))
                    cur.clear()
            try:
                for raw in p.stdout:
                    if self._stop:
                        break
                    line = raw.decode("ascii", "replace").strip()
                    if line.startswith("frame:"):
                        flush()
                        cur[0] = 0
                        n = len(self.bands[0])
                        if n % 20 == 0:
                            if not _pace(self.start + n * SPEC_STEP, lambda: self._stop):
                                self.gave_up = True    # music jumped ahead - give up
                                break
                    elif line.startswith("lavfi.astats.") and ".RMS_level=" in line:
                        try:
                            ch = int(line[13:line.index(".", 13)])
                        except ValueError:
                            continue
                        cur[ch] = _db_text_byte(line.split("=", 1)[1])
                flush()
            finally:
                try:
                    if not len(self) and not self._stop:
                        msg = p.stderr.read(2000).decode("utf-8", "replace").strip()
                except Exception:
                    pass
                try:
                    p.kill()
                except Exception:
                    pass
                try:
                    p.wait()
                except Exception:
                    pass
                self._proc = None
            if len(self) or self.gave_up:
                break
        self.done = True
        if not len(self) and not self._stop:
            _log("spectrum: no levels for %s - %s" % (
                os.path.basename(self.path), msg.replace("\n", " | ")[-500:]))
        else:
            _log("spectrum: read %s - %.0f s in %.1f s%s" % (
                os.path.basename(self.path), len(self) * SPEC_STEP,
                time.time() - started, " - stopped early" if self._stop else ""))

    def at(self, t0, t1):
        """Band levels in dB (loudest over t0..t1), or None if not read
        that far yet."""
        i0 = max(0, int((t0 - self.start) / SPEC_STEP))
        i1 = max(i0 + 1, int((t1 - self.start) / SPEC_STEP))
        n = len(self)
        if i0 >= n or t1 < self.start:
            return None
        i1 = min(i1, n)
        return [byte_db(max(b[i0:i1])) for b in self.bands]


# =============================================================================
# The play list
# =============================================================================

class _Queue(object):

    def __init__(self):
        self.session = None
        self.tracks = []
        self.index = 0
        self.active = False
        self.paused = False
        self.changed = 0           # goes up whenever the track/state changes
        self._prev_ref = None
        self._hooked = False
        self._started_at = 0.0
        self.levels = None         # Levels for the current track
        self.spectrum = None       # Spectrum for the current track
        self.preparing = None      # tracker file being turned into sound
        self._play_path = None     # what the box is actually playing
        self._prep_result = None
        self._prep_timer = None

    # -- starting / stopping ------------------------------------------------

    def start(self, session, tracks, index=0):
        if not tracks:
            return
        self.session = session
        if not self.active and self._prev_ref is None:
            try:
                self._prev_ref = session.nav.getCurrentlyPlayingServiceOrGroup()
            except Exception:
                self._prev_ref = None
        if getattr(self, "_tv_volume", None) is None:
            self._volume_to_music()
        if not self._hooked:
            try:
                session.nav.event.append(self._event)
                self._hooked = True
            except Exception:
                pass
        self.tracks = list(tracks)
        self.index = max(0, min(index, len(self.tracks) - 1))
        self.active = True
        self._play()

    def _play(self):
        path = self.current()
        if not path:
            return
        self.paused = False
        self.preparing = None
        if _trk.is_tracker(path):
            wav = _trk.cached(path)
            if wav is None:
                self._prepare(path)
                return
            self._play_file(path, wav)
        else:
            self._play_file(path, path)

    def _play_file(self, path, play):
        """Play `play` for the track `path` (the same file, or the .wav
        made from a tracker file)."""
        ref = eServiceReference(4097, 0, play)
        name = _tag_cache.get(path, {}).get("title") or track_name(path)
        if _trk.is_tracker(path):
            name = " ".join(name.replace("_", " ").split()) or name
        ref.setName(name)
        _log("play %d/%d: %s" % (self.index + 1, len(self.tracks),
                                 os.path.basename(path)))
        self._play_path = play
        self._started_at = time.time()
        self._stop_readers()
        play_pos[0] = 0.0
        self.levels = Levels(play)
        self.spectrum = Spectrum(play)
        try:
            self.session.nav.playService(ref)
        except Exception as e:
            _log("play failed: %s" % e)
        self.changed += 1
        self._prefetch()

    # Tracker files: made into sound first (in the background)
    def _prepare(self, path):
        try:
            if self._is_ours():
                self.session.nav.stopService()   # quiet while it's made
        except Exception:
            pass
        self._stop_readers()
        self.preparing = path
        self._prep_result = None
        self.changed += 1
        _log("preparing %s" % os.path.basename(path))

        def work():
            self._prep_result = (path, _trk.render(path))
        threading.Thread(target=work, name="E2iPTV-Tracker", daemon=True).start()
        if self._prep_timer is None:
            self._prep_timer = eTimer()
            self._prep_timer.callback.append(self._prep_poll)
        self._prep_timer.start(250, False)

    def _prep_poll(self):
        res = self._prep_result
        if res is None:
            return
        self._prep_timer.stop()
        self._prep_result = None
        path, wav = res
        if not self.active or self.preparing != path or self.current() != path:
            return                      # moved on meanwhile (it stays cached)
        self.preparing = None
        if wav:
            self._play_file(path, wav)
        else:
            _log("can't play %s - skipped" % os.path.basename(path))
            self.changed += 1
            self.next()

    def _prefetch(self):
        """Get the next tune ready while this one plays."""
        nxt = self.upcoming()
        if nxt and _trk.is_tracker(nxt) and _trk.cached(nxt) is None:
            threading.Thread(target=_trk.render, args=(nxt,),
                             name="E2iPTV-Tracker-Next", daemon=True).start()

    def stop(self):
        """Stop the music (stay silent)."""
        if self.active:
            _log("stop")
        self.active = False
        self.paused = False
        self.preparing = None
        self._stop_readers()
        try:
            if self._is_ours():
                self.session.nav.stopService()
        except Exception:
            pass
        self.changed += 1

    # -- volume: Music keeps its own --------------------------------------
    #
    # Music files are mastered far louder than TV, so Music has its own
    # volume: the TV volume is put aside when music starts and put back on
    # leaving, and whatever the volume was during music is used next time
    # (the first time: 15 steps below the TV volume).

    def _volume_to_music(self):
        tv = _get_volume()
        if tv is None:
            return
        self._tv_volume = tv
        music = _settings().get("music_volume")
        if not isinstance(music, int):
            music = max(5, tv - 15)
        _log("volume: TV %d -> music %d" % (tv, music))
        _set_volume(music)

    def _volume_to_tv(self):
        tv = getattr(self, "_tv_volume", None)
        if tv is None:
            return
        self._tv_volume = None
        music = _get_volume()
        if music is not None:
            _save_settings(music_volume=music)
        _log("volume: music %s -> TV %d" % (music, tv))
        _set_volume(tv)

    def leave(self):
        """Leaving Music: stop, and put back what was playing before."""
        was = self.active
        self.stop()
        self._volume_to_tv()
        if self._hooked:
            try:
                self.session.nav.event.remove(self._event)
            except Exception:
                pass
            self._hooked = False
        prev, self._prev_ref = self._prev_ref, None
        if was or prev is not None:
            try:
                cur = self.session.nav.getCurrentlyPlayingServiceReference()
            except Exception:
                cur = None
            if prev is not None and cur is None:
                try:
                    self.session.nav.playService(prev)
                except Exception as e:
                    _log("restore failed: %s" % e)
        self.tracks = []
        self.index = 0

    def check_readers(self, pos):
        """Called by Now Playing as the music plays: after a jump ahead
        past what the meters have read (or back before where they
        started), start them again from here rather than have them race
        through everything in between - that load slowed the sound."""
        path = self._play_path
        if not path or not self.active or self.preparing:
            return
        lv, sp = self.levels, self.spectrum
        if lv is not None and sp is not None and lv.covers(pos) and sp.covers(pos):
            self._jump_seen = None
            return
        # Wait until the position has settled (several presses of 6).
        now = time.time()
        seen = getattr(self, "_jump_seen", None)
        if seen is None or abs(seen[1] - pos) > 2.0 + (now - seen[0]):
            self._jump_seen = (now, pos)
            return
        if now - seen[0] < 0.6:
            return
        self._jump_seen = None
        start = max(0.0, pos - 0.5)
        _log("meter: jump to %.0f s - reading from there" % start)
        self._stop_readers()
        self.levels = Levels(path, start)
        self.spectrum = Spectrum(path, start)

    def _stop_readers(self):
        for r in (self.levels, self.spectrum):
            if r is not None:
                r.stop()
        self.levels = self.spectrum = None

    # -- moving about ---------------------------------------------------------

    def current(self):
        if self.tracks and 0 <= self.index < len(self.tracks):
            return self.tracks[self.index]
        return None

    def upcoming(self):
        if self.active and self.index + 1 < len(self.tracks):
            return self.tracks[self.index + 1]
        return None

    def next(self):
        if not self.active:
            return
        if self.index + 1 < len(self.tracks):
            self.index += 1
            self._play()
        else:
            self.stop()

    def prev(self):
        if not self.active:
            return
        # Like a CD player: back to the start of this track first,
        # previous track if pressed again within a few seconds.
        if self.position()[0] > 4 or self.index == 0:
            self.seek_to(0)
            return
        self.index -= 1
        self._play()

    def toggle_pause(self):
        if not self.active:
            return
        try:
            p = self.session.nav.getCurrentService().pause()
            if self.paused:
                p.unpause()
            else:
                p.pause()
            self.paused = not self.paused
        except Exception as e:
            _log("pause: %s" % e)
        self.changed += 1

    def position(self):
        """(seconds played, track length) - length 0 if not known yet."""
        pos = length = 0
        try:
            seek = self.session.nav.getCurrentService().seek()
            r = seek.getPlayPosition()
            if r and not r[0]:
                pos = r[1] / 90000.0
                play_pos[0] = pos
            r = seek.getLength()
            if r and not r[0]:
                length = r[1] / 90000.0
        except Exception:
            pass
        if not length:
            length = _tag_cache.get(self.current() or "", {}).get("length", 0)
        return pos, length

    def seek_by(self, secs):
        pos, length = self.position()
        target = max(0, pos + secs)
        if length and target > length - 2:
            self.next()
            return
        self.seek_to(target)

    def seek_to(self, secs):
        try:
            self.session.nav.getCurrentService().seek().seekTo(int(secs * 90000))
        except Exception as e:
            _log("seek: %s" % e)

    # -- the box's player -----------------------------------------------------

    def _is_ours(self):
        try:
            cur = self.session.nav.getCurrentlyPlayingServiceReference()
            return cur is not None and cur.type == 4097 \
                and cur.getPath() == self._play_path
        except Exception:
            return False

    def _event(self, evt):
        if not self.active:
            return
        if self.preparing:
            return                      # nothing of ours playing just now
        if evt == iPlayableService.evEOF and self._is_ours():
            _log("end of track")
            self.next()
        elif evt == iPlayableService.evStopped or evt == iPlayableService.evStart:
            # Something else started playing (or ours was stopped): the
            # list has finished as far as we're concerned.
            if time.time() - self._started_at > 2 and not self._is_ours():
                _log("something else took over - music stopped")
                self._stop_readers()
                self.active = False
                self.paused = False
                self.changed += 1


queue = _Queue()


# =============================================================================
# Raw media keys (Stop, Play/Pause, |<< >>|, << >>)
# =============================================================================

class _MediaKeys(object):

    def _keys_bind(self):
        self._keys_handler = self._on_raw_key
        try:
            eActionMap.getInstance().bindAction("", -0x7FFFFFFF, self._keys_handler)
        except Exception:
            pass

    def _keys_unbind(self):
        try:
            eActionMap.getInstance().unbindAction("", self._keys_handler)
        except Exception:
            pass

    def _on_raw_key(self, key, flag):
        if flag != 0:
            return 0
        try:
            if self.session.current_dialog is not self:
                return 0
        except Exception:
            return 0
        if key == KEY_MENU:
            if not hasattr(self, "_item_menu"):
                return 0
            from twisted.internet import reactor
            reactor.callLater(0, self._item_menu)
            return 1
        if key == KEY_STOP:
            queue.stop()
        elif key in KEY_PLAYPAUSE:
            if queue.active:
                queue.toggle_pause()
            else:
                return 0
        elif key == KEY_NEXT:
            queue.next()
        elif key == KEY_PREV:
            queue.prev()
        elif key == KEY_FF:
            queue.seek_by(30)
        elif key == KEY_REW:
            queue.seek_by(-30)
        else:
            return 0
        self._media_changed()
        return 1

    def _media_changed(self):
        pass


# =============================================================================
# Folder list
# =============================================================================

_ROW_H = 60


class _MusicList(GUIComponent):

    GUI_WIDGET = eListbox

    def __init__(self, width):
        GUIComponent.__init__(self)
        self._w = width
        self.l = eListboxPythonMultiContent()
        self.l.setItemHeight(_ROW_H)
        self.l.setFont(0, gFont("E2iRegular", 30))
        self.l.setFont(1, gFont("E2iRegular", 24))
        self._rows = []
        self._sel = 0

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setWrapAround(False)
        instance.setSelectionEnable(False)

    def preWidgetRemove(self, instance):
        pass

    VISIBLE = 14              # rows that fit (840 / 60)

    def setRows(self, rows, sel=0):
        """rows: [(text, note)]. Only the rows that fit are given to the
        box's list (it doesn't scroll by itself with its selection bar
        switched off)."""
        self._rows = rows
        self._sel = sel = max(0, min(sel, len(rows) - 1)) if rows else 0
        # The highlight stays on the middle row and the list moves past
        # it (as on Live TV / Movies / Series); rows before the first or
        # after the last entry are left blank.
        out = []
        if not rows:
            self.l.setList(out)
            return
        mid = self.VISIBLE // 2
        for r in range(self.VISIBLE):
            i = sel - mid + r
            if i < 0 or i >= len(rows):
                out.append([None, MultiContentEntryText(
                    pos=(0, 0), size=(self._w, _ROW_H), font=0,
                    flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER, text="",
                    backcolor=_T.PANEL, backcolor_sel=_T.PANEL)])
                continue
            text, note = rows[i]
            on = (i == sel)
            bg = _T.HI if on else _T.PANEL
            fg = 0x000000 if on else 0xffffff
            fg2 = 0x303030 if on else 0x9fc4ff
            out.append([
                None,
                MultiContentEntryText(pos=(0, 0), size=(self._w, _ROW_H), font=0,
                                      flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                                      text="", backcolor=bg, backcolor_sel=bg),
                MultiContentEntryText(pos=(20, 0), size=(self._w - 240, _ROW_H), font=0,
                                      flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                                      text=text, color=fg, color_sel=fg,
                                      backcolor=bg, backcolor_sel=bg),
                MultiContentEntryText(pos=(self._w - 220, 0), size=(200, _ROW_H), font=1,
                                      flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER,
                                      text=note, color=fg2, color_sel=fg2,
                                      backcolor=bg, backcolor_sel=bg),
            ])
        self.l.setList(out)
        if self.instance and out:
            self.instance.moveSelectionTo(0)

    def reset(self):
        pass

    def position_text(self):
        """'12 of 240' ('' for an empty folder)."""
        n = len(self._rows)
        if not n:
            return ""
        return "%d of %d" % (self._sel + 1, n)

    def setSelection(self, sel):
        self.setRows(self._rows, sel)
        try:
            self._on_move()
        except Exception:
            pass

    def _on_move(self):
        pass


class _PanelList(GUIComponent):
    """A plain list the screen draws itself (no selection bar of the
    box's own) - used for both panels."""

    GUI_WIDGET = eListbox

    def __init__(self, item_h):
        GUIComponent.__init__(self)
        self.l = eListboxPythonMultiContent()
        self.l.setItemHeight(item_h)
        self.l.setFont(0, gFont("E2iRegular", 30))
        self.l.setFont(1, gFont("E2iRegular", 26))

    def postWidgetCreate(self, instance):
        instance.setContent(self.l)
        instance.setWrapAround(False)
        instance.setSelectionEnable(False)

    def preWidgetRemove(self, instance):
        pass

    def setList(self, rows):
        self.l.setList(rows)


# Layout (1920x1080) - the same as the Lab Recordings screen
_C_BG, _C_PANEL, _C_TEXT, _C_DIM, _C_ACCENT = (
    "E2iBg", "E2iPanel", "#ffffff", "E2iDim", "E2iHi")
_P_PANEL, _P_SEL, _P_TEXT, _P_DIM, _P_ACCENT = (
    _T.PANEL, _T.HI, 0xffffff, _T.DIM, _T.HI)
_L_X, _L_W = 40, 600
_R_X, _R_W = 670, 1210
_P_Y, _P_H = 100, 940
_PL_ROW_H, _PL_ROWS = 44, 10
_PL_Y = _P_Y + 16
_F_ROW_H, _F_ROWS = 66, 13
_F_Y = _P_Y + 16
_INFO_Y = _PL_Y + _PL_ROWS * _PL_ROW_H + 30
_THUMB = 190


def _row(w, h, cells, sel):
    """One list row: background, rounded highlight when selected, then
    the text cells [(x, width, font, text, colour, align)]."""
    bg = _P_SEL if sel else _P_PANEL
    out = [None, MultiContentEntryText(
        pos=(0, 0), size=(w, h), font=0, text="",
        flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
        backcolor=_P_PANEL, backcolor_sel=_P_PANEL)]
    if sel:
        out.append(MultiContentEntryText(
            pos=(4, 3), size=(w - 8, h - 6), font=0, text="",
            flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
            backcolor=bg, backcolor_sel=bg, cornerRadius=14))
    for x, cw, font, text, col, align in cells:
        col = 0x000000 if sel else col
        out.append(MultiContentEntryText(
            pos=(x, 0), size=(cw, h), font=font, text=text,
            flags=align | RT_VALIGN_CENTER,
            color=col, color_sel=col, backcolor=bg, backcolor_sel=bg))
    return out


_SEARCH_CRIT = {"any": "", "artist": [], "genre": [], "y1": "", "y2": "",
                "album": "", "track": ""}


class MusicBrowser(_MediaKeys, Screen):
    """Lab: two panels, as the Recordings screen.

    Left: Now Playing, Play this folder, Shuffle this folder, Stop
    music, Music folder - and the info (cover, title, artist, album,
    length) for whatever is highlighted. Right: folders and tracks.

    OK opens a folder, or plays the folder's tracks from the one
    picked. Left/Right swap panels; Left on the left panel opens the
    rail (the music keeps playing). Exit goes up a folder, and at the
    top opens the rail. Green/Yellow/Blue still work as before."""

    def __init__(self, session):
        Screen.__init__(self, session)
        self._root = music_root()
        last = str(_settings().get("last") or "")
        self._path = (last if self._root and last.startswith(self._root)
                      else self._root)
        self._items = []           # [(name, path, is_dir)]; is_dir "up" = ".."
        self._sel = 0
        self._scroll = 0
        self._want_sel = None      # name to highlight once listed
        self._job = None           # background result
        self._busy = False
        self._seen_change = -1
        self._notice = ""          # message shown while nothing is playing
        self._focus = "files"
        self._places = []
        self._pl_sel = 0
        self._info_key = None      # what the info panel is showing / loading
        self._info_job = None
        self._info_due = 0
        # Browsing by Artist / Genre / Year / Album, from the music index
        st = _settings()
        self._mode = st.get("browse") if st.get("browse") in _mi.MODE_NAMES else "folders"
        self._sort = st.get("sort") if st.get("sort") in _mi.SORT_NAMES else "az"
        self._layout = st.get("layout") if st.get("layout") in _mi.LAYOUT_NAMES \
            else _mi.DEFAULT_LAYOUT
        self._lib = None           # music_index.Library once loaded
        self._vpath = []           # [(field, key)] chosen so far
        self._vstack = []          # the highlight to go back to, per level
        self._album_path = None    # album folder opened from the index
        self._album_back = 0
        self._item_notes = []      # right-hand note per row (index lists)
        self._seen_index = _mi.indexer.done_count
        # Search: the criteria (kept while the box is on), the results
        # being shown (None when not searching) and where to go back to
        self._crit = dict(_SEARCH_CRIT)
        self._search = None
        self._s_back = None
        self._s_menu_pos = 0
        # Playlists view: {"open": name or None, "back": where to return,
        # "sel": highlight on the list of playlists}; None when not shown
        self._plv = None
        self._pl_target = None
        self._tracker_dir = self._find_tracker_dir()

        self.skin = """
<screen name="E2iLabMusic" position="0,0" size="1920,1080" flags="wfNoBorder" backgroundColor="{bg}">
  <eLabel position="0,0" size="1920,1080" backgroundColor="{bg}" zPosition="0"/>
  <widget name="header" position="60,30" size="1400,50" font="E2iRegular;34" foregroundColor="{text}" backgroundColor="{bg}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="clock"  position="1560,30" size="320,50" font="E2iRegular;34" foregroundColor="{dim}" backgroundColor="{bg}" halign="right" valign="center" transparent="1" zPosition="2"/>
  <widget name="l_bg" position="{lx},{py}" size="{lw},{ph}" backgroundColor="{panel}" cornerRadius="18" zPosition="1"/>
  <widget name="r_bg" position="{rx},{py}" size="{rw},{ph}" backgroundColor="{panel}" cornerRadius="18" zPosition="1"/>
  <widget name="places" position="{lx2},{ply}" size="{lw2},{plh}" backgroundColor="{panel}" scrollbarMode="showNever" transparent="1" zPosition="2"/>
  <eLabel position="{lx3},{sepy}" size="{lw3},2" backgroundColor="E2iSep" zPosition="2"/>
  <eLabel position="{lx3},{iy}" size="{th},{th}" backgroundColor="E2iPanel2" zPosition="2"/>
  <widget name="nocover" position="{lx3},{iy}" size="{th},{th}" font="E2iRegular;34" foregroundColor="E2iMuted" backgroundColor="E2iPanel2" halign="center" valign="center" transparent="1" zPosition="3"/>
  <widget name="cover" position="{lx3},{iy}" size="{th},{th}" alphatest="blend" zPosition="4"/>
  <widget name="i_title"  position="{lx3},{iy1}" size="{lw3},84" font="E2iRegular;34" foregroundColor="{text}" backgroundColor="{panel}" halign="left" valign="top" transparent="1" zPosition="2"/>
  <widget name="i_artist" position="{lx3},{iy2}" size="{lw3},38" font="E2iRegular;28" foregroundColor="{accent}" backgroundColor="{panel}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="i_album"  position="{lx3},{iy3}" size="{lw3},38" font="E2iRegular;28" foregroundColor="{dim}" backgroundColor="{panel}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="i_detail" position="{lx3},{iy4}" size="{lw3},38" font="E2iRegular;26" foregroundColor="{dim}" backgroundColor="{panel}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="list" position="{rx2},{fy}" size="{rw2},{fh}" backgroundColor="{panel}" scrollbarMode="showNever" transparent="1" zPosition="2"/>
  <widget name="status" position="{rx3},{sy}" size="{sw},34" font="E2iRegular;26" foregroundColor="#f5c542" backgroundColor="{panel}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="count"  position="{cx},{sy}" size="260,34" font="E2iRegular;24" foregroundColor="{dim}" backgroundColor="{panel}" halign="right" valign="center" transparent="1" zPosition="2"/>
</screen>""".format(
            bg=_C_BG, text=_C_TEXT, dim=_C_DIM, panel=_C_PANEL, accent=_C_ACCENT,
            lx=_L_X, lw=_L_W, rx=_R_X, rw=_R_W, py=_P_Y, ph=_P_H,
            lx2=_L_X + 10, lw2=_L_W - 20, ply=_PL_Y, plh=_PL_ROWS * _PL_ROW_H,
            lx3=_L_X + 24, lw3=_L_W - 48, sepy=_INFO_Y - 14, th=_THUMB,
            iy=_INFO_Y, iy1=_INFO_Y + _THUMB + 16, iy2=_INFO_Y + _THUMB + 104,
            iy3=_INFO_Y + _THUMB + 146, iy4=_INFO_Y + _THUMB + 188,
            rx2=_R_X + 10, rw2=_R_W - 20, fy=_F_Y, fh=_F_ROWS * _F_ROW_H,
            rx3=_R_X + 34, sw=_R_W - 340, cx=_R_X + _R_W - 294,
            sy=_P_Y + _P_H - 48)

        self["header"] = Label("Music")
        self["clock"] = Label("")
        self["l_bg"] = Label("")
        self["r_bg"] = Label("")
        self["places"] = _PanelList(_PL_ROW_H)
        self["list"] = _PanelList(_F_ROW_H)
        self["status"] = Label("")
        self["count"] = Label("")
        self["cover"] = Pixmap()
        self["nocover"] = Label("♫")
        for n in ("i_title", "i_artist", "i_album", "i_detail"):
            self[n] = Label("")

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok":     self._on_ok,
                "cancel": self._on_cancel,
                "up":     lambda: self._move(-1),
                "down":   lambda: self._move(1),
                "left":   self._on_left,
                "right":  self._on_right,
                "green":  lambda: self._play_all(False),
                "yellow": lambda: self._play_all(True),
                "blue":   self._open_player,
                "red":    self._search_open,
            }, -1)
        # CH+ / CH-: a page at a time
        self["pages"] = ActionMap(["ChannelSelectBaseActions"], {
            "nextBouquet": lambda: self._move(-self._page()),
            "prevBouquet": lambda: self._move(self._page()),
        }, -1)

        self._timer = eTimer()
        self._timer.callback.append(self._tick)
        self.onLayoutFinish.append(self._first_load)
        self.onClose.append(self._closing)
        self._keys_bind()

    # -- loading --------------------------------------------------------------

    def _first_load(self):
        try:
            self["cover"].hide()
        except Exception:
            pass
        self._timer.start(200, False)
        self._places_build()
        self._clock_text()
        if not self._root:
            self["header"].setText("Music")
            self._say("No music folder chosen yet - set it in Settings -> Playback -> Music folder")
            self._paint()
            return
        if self._mode != "folders":
            self._index_open()
        else:
            pl = _settings().get("place") or {}
            item = pl.get("item") if pl.get("mode") == "folders" \
                and pl.get("path") == self._path else None
            self._load(self._path, select=item)

    def _load(self, path, select=None):
        if self._busy:
            return
        self._busy = True
        self._want_sel = select
        self["header"].setText("%s   (opening...)" % self._crumbs(path))

        def work():
            try:
                folders, tracks = list_folder(path)
                self._job = ("list", path, folders, tracks, None)
            except Exception as e:
                self._job = ("list", path, None, None, e)
        threading.Thread(target=work, name="E2iPTV-Music-List", daemon=True).start()

    def _crumbs(self, path):
        rel = os.path.relpath(path, self._root) if path.startswith(self._root) else path
        parts = [] if rel in (".", "") else rel.split(os.sep)
        return "  ›  ".join(["Music"] + parts)

    def _clock_text(self):
        try:
            self["clock"].setText(time.strftime("%a %d %b  %H:%M"))
        except Exception:
            pass

    def _tick(self):
        job, self._job = self._job, None
        if job is not None:
            self._finish_job(job)
        info, self._info_job = self._info_job, None
        if info is not None:
            self._info_done(info)
        if self._info_due and time.time() >= self._info_due:
            self._info_due = 0
            self._info_start()
        self._index_tick()
        if queue.changed != self._seen_change:
            self._seen_change = queue.changed
            self._places_build()
            self._paint_places()
            self._show_status()
            if self._focus == "places":
                self._info_now()
        elif queue.active:
            self._show_status()
        if int(time.time()) % 15 == 0:
            self._clock_text()

    def _finish_job(self, job):
        kind = job[0]
        if kind == "list":
            _k, path, folders, tracks, err = job
            self._busy = False
            if err is not None:
                _log("list %s: %s" % (path, err))
                if path != self._root and path == self._path:
                    # Remembered folder gone (renamed?) - start at the top.
                    self._path = self._root
                    self._load(self._root)
                    return
                self["header"].setText("Music")
                self._say("Can't open the music folder: %s" % path)
                self._items = []
                self._paint()
                return
            self._path = path
            self._notice = ""
            _save_settings(last=path)
            items = []
            if path != self._root and path.startswith(self._root):
                items.append(("..", os.path.dirname(path), "up"))
            items += [(n, p, True) for n, p in folders] + \
                     [(n, p, False) for n, p in tracks]
            self._items = items
            self._item_notes = []
            sel = 1 if (items and items[0][2] == "up" and len(items) > 1) else 0
            if self._want_sel:
                sel = next((i for i, it in enumerate(items)
                            if it[0] == self._want_sel), sel)
            self._sel = sel
            self._scroll = 0
            self["header"].setText(self._crumbs(path))
            self._paint()
            if not folders and not tracks:
                self._say("This folder is empty")
        elif kind == "index":
            self._busy = False
            self._index_loaded(job[1])
        elif kind == "sindex":
            self._busy = False
            self._search_index_loaded(job[1])
        elif kind == "search":
            self._busy = False
            self._search_done(job[1], job[2])
        elif kind == "pladd":
            self._busy = False
            self._pl_added(job[1], job[2])
        elif kind == "gather":
            _k, tracks, shuffle, label = job
            self._busy = False
            if not tracks:
                self._say("No music found in '%s'" % label)
                return
            if shuffle:
                random.shuffle(tracks)
            _log("%s %d tracks from %s" % ("shuffle" if shuffle else "play all",
                                           len(tracks), label))
            queue.start(self.session, tracks, 0)
            self._open_player()

    # -- drawing ----------------------------------------------------------------

    def _places_build(self):
        places = []
        if queue.active or queue.current():
            places.append(("now", "Now Playing"))
        what = "list" if self._virtual() else "folder"
        places.append(("play", "Play this " + what))
        places.append(("shuffle", "Shuffle this " + what))
        if queue.active:
            places.append(("stop", "Stop music"))
        if self._tracker_dir:
            places.append(("tracker", "Tracker Files"))
        places.append(("search", "Search"))
        places.append(("playlists", "Playlists"))
        if self._mode != "folders":
            places.append(("browse", "Browse: " + _mi.MODE_NAMES.get(self._mode, "")))
        places.append(("library", "Browse, sort and rescan"))
        places.append(("root", "Music folder"))
        keep = self._places[self._pl_sel][0] if self._places else None
        self._places = places
        self._pl_sel = next((i for i, p in enumerate(places) if p[0] == keep), 0) \
            if keep else 0

    def _paint_places(self):
        w = _L_W - 20
        rows = []
        for i, (key, text) in enumerate(self._places[:_PL_ROWS]):
            sel = self._focus == "places" and i == self._pl_sel
            col = _P_ACCENT if key == "now" else _P_TEXT
            rows.append(_row(w, _PL_ROW_H,
                             [(24, w - 48, 0, text, col, RT_HALIGN_LEFT)], sel))
        self["places"].setList(rows)

    def _paint(self):
        w = _R_W - 20
        n = len(self._items)
        self._sel = max(0, min(self._sel, n - 1)) if n else 0
        if self._sel < self._scroll:
            self._scroll = self._sel
        elif self._sel >= self._scroll + _F_ROWS:
            self._scroll = self._sel - _F_ROWS + 1
        self._scroll = max(0, min(self._scroll, max(0, n - _F_ROWS)))
        cur = queue.current()
        rows = []
        for i in range(self._scroll, min(n, self._scroll + _F_ROWS)):
            name, path, is_dir = self._items[i]
            sel = self._focus == "files" and i == self._sel
            note = self._item_notes[i] if i < len(self._item_notes) else ""
            if is_dir == "up":
                cells = [(24, w - 48, 0, "..", _P_ACCENT, RT_HALIGN_LEFT)]
            elif is_dir in ("group", "album", "plist"):
                nw = 440 if is_dir == "album" else 300
                cells = [(24, w - 72 - nw, 0, "\u25b8  " + name, _P_TEXT, RT_HALIGN_LEFT),
                         (w - 24 - nw, nw, 1, note, _P_DIM, RT_HALIGN_RIGHT)]
            elif is_dir:
                cells = [(24, w - 48, 0, "▸  " + name, _P_TEXT, RT_HALIGN_LEFT)]
            else:
                base, ext = list_name(name, path)
                playing = path == cur
                if note:
                    cells = [(24, w - 72 - 440, 0, ("♫  " if playing else "") + name,
                              _P_ACCENT if playing else _P_TEXT, RT_HALIGN_LEFT),
                             (w - 24 - 440, 440, 1, note, _P_DIM, RT_HALIGN_RIGHT)]
                else:
                    cells = [(24, w - 48 - 160, 0, ("♫  " if playing else "") + base,
                              _P_ACCENT if playing else _P_TEXT, RT_HALIGN_LEFT),
                             (w - 24 - 140, 140, 1, ext[1:].upper(), _P_DIM, RT_HALIGN_RIGHT)]
            rows.append(_row(w, _F_ROW_H, cells, sel))
        self["list"].setList(rows)
        self._paint_places()
        self._show_count()
        self._info_soon()

    def _show_count(self):
        n = len(self._items)
        self["count"].setText("%d of %d" % (self._sel + 1, n) if n > _F_ROWS else "")

    def _say(self, text):
        """A message that stays on the status line until something plays."""
        self._notice = text
        self["status"].setText(text)

    def _check_root(self):
        """Back from Settings (via the rail): the music folder may have
        been chosen or changed there - start again in it."""
        root = music_root()
        if root == self._root:
            return
        self._root = root
        self._path = root
        self._items = []
        self._sel = 0
        self._paint()
        if not root:
            self["header"].setText("Music")
            self._say("No music folder chosen yet - set it in Settings -> Playback -> Music folder")
            return
        self._notice = ""
        self._lib = None
        self._search, self._s_back = None, None
        self._plv = None
        self._tracker_dir = self._find_tracker_dir()
        self._vpath, self._vstack, self._album_path = [], [], None
        if self._mode != "folders":
            self._index_open()
        else:
            self._load(root)

    def _show_status(self):
        if self._busy:
            return
        cur = queue.current() if queue.active else None
        if not cur:
            self["status"].setText(self._notice)
            return
        if queue.preparing:
            self["status"].setText("Preparing:  %s ..." % (
                _tag_cache.get(cur, {}).get("title") or track_name(cur)))
            return
        pos, length = queue.position()
        tags = _tag_cache.get(cur, {})
        artist = tags.get("artist") or path_info(cur)[0]
        self["status"].setText("%s  %s%s   %s%s" % (
            "Paused:" if queue.paused else "Now playing:",
            tags.get("title") or track_name(cur),
            ("  -  " + artist) if artist else "",
            fmt_time(pos), (" / " + fmt_time(length)) if length else ""))

    # -- the info panel (cover, title, artist ...) ------------------------------

    def _info_target(self):
        """(key, path, kind) for what's highlighted: kind track/folder/none."""
        if self._focus == "places":
            key = self._places[self._pl_sel][0] if self._places else None
            cur = queue.current()
            if key == "now" and cur:
                return ("track", cur)
            if key in ("play", "shuffle"):
                if self._virtual():
                    return ("list", key)
                return ("folder", self._path)
            if key == "root":
                return ("folder", self._root)
            return ("none", key)
        if not self._items:
            return ("none", None)
        name, path, is_dir = self._items[self._sel]
        if is_dir == "up":
            return ("folder", path)
        if is_dir in ("group", "plist"):
            return ("group", (self._sel, path))
        return ("folder" if is_dir else "track", path)

    def _info_soon(self):
        """Fill in what's known straight away; the cover and tags follow
        once the highlight has stayed put for a moment."""
        target = self._info_target()
        if target == self._info_key:
            return
        self._info_key = target
        kind, path = target
        self["cover"].hide()
        self["nocover"].show()
        if kind == "track":
            artist, album = path_info(path)
            tags = _tag_cache.get(path, {})
            self._info_text(tags.get("title") or track_name(path),
                            tags.get("artist") or artist,
                            tags.get("album") or album, "")
        elif kind == "folder" and path:
            self._info_text(os.path.basename(path.rstrip("/")) or "Music", "", "", "")
        elif kind == "group":
            i = path[0]
            self._info_text(self._items[i][0], "", "",
                            self._item_notes[i] if i < len(self._item_notes) else "")
        elif kind == "list":
            self._info_text(self._v_title(), "", "", self._v_count_text())
        else:
            self._info_text(self._places_help(path), "", "", "")
        if kind in ("track", "folder") and path:
            self._info_due = time.time() + 0.4

    def _info_now(self):
        self._info_key = None
        self._info_soon()

    def _places_help(self, key):
        return {"stop": "Stop the music",
                "now": "Open Now Playing",
                "library": "Browse by Artist, Genre, Year or Album, "
                           "sort the albums, or rescan the music",
                "search": "Find music by artist, genre, year, album "
                          "or track name  (Red)",
                "playlists": "Your own playlists. Menu on a track, album, "
                             "artist or list adds it to one",
                "browse": "The top of your music by %s - change it in "
                          "Browse, sort and rescan" % _mi.MODE_NAMES.get(self._mode, ""),
                "root": "Your music folder, A-Z as on disk",
                "tracker": "Your tracker music: MOD, S3M, XM, IT ... "
                           "Made ready to play when you play it"}.get(key, "")

    def _info_text(self, title, artist, album, detail):
        for n, v in (("i_title", title), ("i_artist", artist),
                     ("i_album", album), ("i_detail", detail)):
            self[n].setText(v or "")

    def _info_start(self):
        target = self._info_key
        if not target or target[0] not in ("track", "folder") or not target[1]:
            return
        kind, path = target

        def work():
            try:
                if kind == "track":
                    tags = read_tags(path)
                    cover = make_cover(path, _THUMB)
                    self._info_job = (target, tags, cover, None)
                else:
                    folders, tracks = list_folder(path)
                    src = find_folder_cover(path)
                    if not src and tracks:
                        src = tracks[0][1]
                    cover = make_cover(src, _THUMB) if src else None
                    self._info_job = (target, None, cover, (len(folders), len(tracks)))
            except Exception as e:
                _log("info %s: %s" % (os.path.basename(str(path)), e))
        threading.Thread(target=work, name="E2iPTV-Music-Info", daemon=True).start()

    def _info_done(self, info):
        target, tags, cover, counts = info
        if target != self._info_key:
            return                      # the highlight has moved on
        kind, path = target
        if kind == "track" and tags is not None:
            artist, album = path_info(path)
            bits = []
            if tags.get("length"):
                bits.append(fmt_time(tags["length"]))
            if tags.get("track"):
                bits.append("Track %s" % tags["track"])
            bits.append(os.path.splitext(path)[1][1:].upper())
            self._info_text(tags.get("title") or track_name(path),
                            tags.get("artist") or tags.get("album_artist") or artist,
                            tags.get("album") or album,
                            "   ·   ".join(b for b in bits if b))
        elif counts is not None:
            nf, nt = counts
            bits = []
            if nf:
                bits.append("%d folder%s" % (nf, "" if nf == 1 else "s"))
            if nt:
                bits.append("%d track%s" % (nt, "" if nt == 1 else "s"))
            self["i_detail"].setText("   ·   ".join(bits) or "Empty")
        if cover:
            try:
                pm = LoadPixmap(cover, cached=False,
                                desired_width=_THUMB, desired_height=_THUMB)
            except TypeError:
                pm = LoadPixmap(cover, cached=False)
            except Exception:
                pm = None
            if pm:
                try:
                    self["cover"].instance.setPixmap(pm)
                    self["cover"].show()
                    self["nocover"].hide()
                except Exception:
                    pass

    # -- keys ---------------------------------------------------------------

    def _page(self):
        return _PL_ROWS if self._focus == "places" else _F_ROWS

    @staticmethod
    def _step(cur, d, n):
        """Down on the last entry goes to the first, Up on the first to
        the last. A page (CH+/CH-) stops at the end first, and wraps
        round on the next press."""
        if n <= 0:
            return 0
        new = cur + d
        if new >= n:
            return 0 if cur == n - 1 else n - 1
        if new < 0:
            return n - 1 if cur == 0 else 0
        return new

    def _move(self, d):
        if self._focus == "places":
            if self._places:
                self._pl_sel = self._step(self._pl_sel, d, len(self._places))
                self._paint_places()
                self._info_soon()
            return
        if self._items:
            self._sel = self._step(self._sel, d, len(self._items))
            self._paint()

    def _on_ok(self):
        if self._focus == "places":
            self._place_ok()
            return
        if self._busy or not self._items:
            return
        name, path, is_dir = self._items[self._sel]
        if is_dir == "plist":
            self._plv["sel"] = self._sel
            self._plv["open"] = path
            self._plv_show(None)
            return
        if is_dir == "group":
            self._v_enter(path)
            return
        if is_dir == "album":
            self._album_open(path)
            return
        if is_dir == "up":
            self._up_folder()
            return
        if is_dir:
            self._load(path)
            return
        tracks = [p for _n, p, d in self._items if not d]
        queue.start(self.session, tracks, tracks.index(path))
        self._open_player()

    def _place_ok(self):
        if not self._places:
            return
        key = self._places[self._pl_sel][0]
        if key == "now":
            self._open_player()
        elif key in ("play", "shuffle"):
            if self._virtual():
                self._v_play(None, key == "shuffle")
            else:
                self._play_folder(self._path, key == "shuffle")
        elif key == "library":
            self._library_menu()
        elif key == "search":
            self._search_open()
        elif key == "playlists":
            self._focus = "files"
            self._plv_open()
        elif key == "tracker" and self._tracker_dir:
            self._focus = "files"
            self._search, self._s_back = None, None
            self._plv = None
            if self._mode != "folders" and self._lib is not None:
                self._album_open(self._tracker_dir)    # Exit comes back here
            else:
                self._load(self._tracker_dir)
        elif key == "stop":
            queue.stop()
            self._places_build()
            self._paint()
            self._show_status()
        elif key == "root" and self._root:
            # Always the music folder itself, A-Z as on disk (Exit goes back
            # to the Browse by list when one is in use)
            self._focus = "files"
            self._search, self._s_back = None, None
            self._plv = None
            if self._mode != "folders" and self._lib is not None:
                if self._album_path is not None:
                    self._album_path = None
                    self._v_top()
                self._album_open(self._root)
            else:
                self._load(self._root)
        elif key == "browse":
            # The top of the Browse by list (Genres, Artists, Years ...)
            self._focus = "files"
            self._search, self._s_back = None, None
            self._plv = None
            if self._lib is not None:
                self._v_top()
            else:
                self._index_open()

    def _play_all(self, shuffle):
        """Green / Yellow: the highlighted folder, or this folder."""
        if self._items and self._items[self._sel][2] == "plist":
            self._pl_play(self._items[self._sel][1], shuffle)
            return
        if self._items and self._items[self._sel][2] == "group":
            self._v_play(self._items[self._sel][1], shuffle)
            return
        if self._items and self._items[self._sel][2] == "album":
            self._play_folder(self._items[self._sel][1], shuffle)
            return
        if self._virtual():
            self._v_play(None, shuffle)
            return
        if self._items and self._items[self._sel][2] is True:
            self._play_folder(self._items[self._sel][1], shuffle)
        else:
            self._play_folder(self._path, shuffle)

    def _play_folder(self, path, shuffle):
        if self._busy or not path:
            return
        name = os.path.basename(path.rstrip("/")) or "Music"
        self._busy = True
        self["status"].setText("Finding the music in '%s'..." % name)

        def work():
            try:
                tracks = gather_tracks(path)
            except Exception as e:
                _log("gather %s: %s" % (path, e))
                tracks = []
            self._job = ("gather", tracks, shuffle, name)
        threading.Thread(target=work, name="E2iPTV-Music-Gather", daemon=True).start()

    def _up_folder(self):
        if self._busy:
            return False
        if self._plv is not None:
            self._plv_up()
            return True
        if self._search is not None:
            if self._album_path is not None:
                if self._path == self._album_path or \
                        not self._path.startswith(self._album_path + "/"):
                    self._album_path = None
                    self._s_show(self._album_back)
                    return True
            else:
                self._search_leave()
                return True
        elif self._mode != "folders" and self._lib is not None:
            if self._album_path is not None:
                if self._path == self._album_path or \
                        not self._path.startswith(self._album_path + "/"):
                    self._album_path = None
                    self._v_show(self._album_back)
                    return True
            elif self._vpath:
                _f, key = self._vpath.pop()
                sel = self._vstack.pop() if self._vstack else None
                self._v_show(sel)
                if sel is None:             # (reopened here) the one we came from
                    for i, it in enumerate(self._items):
                        if it[1] == key:
                            self._sel = i
                            self._paint()
                            break
                return True
            else:
                return False
        if self._path == self._root or not self._path.startswith(self._root):
            return False
        self._load(os.path.dirname(self._path), select=os.path.basename(self._path))
        return True

    def _on_cancel(self):
        if self._up_folder():
            return
        self._open_rail()

    def _on_left(self):
        if self._focus == "files":
            self._focus = "places"
            self._paint()
            return
        self._open_rail()

    def _on_right(self):
        if self._focus == "places":
            self._focus = "files"
            self._paint()

    def _open_rail(self):
        # The rail over this screen's left edge - the music keeps playing.
        from .lab_rail import open_rail
        open_rail(self, "Music")

    def _open_player(self):
        if queue.active or queue.current():
            self.session.openWithCallback(self._player_closed, MusicPlayer)

    def _player_closed(self, *args):
        # Highlight the track that was playing, if it's in this folder.
        cur = queue.current()
        if cur:
            for i, it in enumerate(self._items):
                if it[1] == cur:
                    self._sel = i
                    break
        self._places_build()
        self._paint()
        self._show_status()

    def _media_changed(self):
        self._show_status()

    # -- browsing from the music index ---------------------------------------

    def _find_tracker_dir(self):
        """<music folder>/Tracker Files, if there is one."""
        root = self._root or music_root()
        if not root:
            return None
        p = os.path.join(root, "Tracker Files")
        try:
            return p if os.path.isdir(p) else None
        except Exception:
            return None

    def _virtual(self):
        """True while showing a list from the index or search results
        (not a real folder)."""
        return (self._mode != "folders" or self._search is not None
                or self._plv is not None) and self._album_path is None

    def _index_open(self):
        if not self._root:
            return
        if self._lib is not None:
            self._v_top()
            self._index_check()
            return
        if self._busy:
            return
        self._busy = True
        self["header"].setText("Music   (opening the music index...)")
        root, layout = self._root, self._layout

        def work():
            try:
                data = _mi.load_index(root, layout)
            except Exception as e:
                _log("index load: %s" % e)
                data = None
            self._job = ("index", data)
        threading.Thread(target=work, name="E2iPTV-Music-IndexLoad", daemon=True).start()

    def _index_loaded(self, data):
        if self._mode == "folders":
            return
        if data is None:
            self._lib = None
            self._items, self._item_notes = [], []
            self["header"].setText("Music")
            self._paint()
            if not _mi.indexer.busy:
                _mi.indexer.start(self._root, self._layout)
            self._say("Building the music index - the first time takes a while")
            return
        self._lib = _mi.Library(data)
        if not self._restore_place():
            self._v_top()
        self._index_check()

    def _restore_place(self):
        """Open where Music was last time (see _remember_place)."""
        pl = _settings().get("place") or {}
        if pl.get("mode") != self._mode or self._lib is None:
            return False
        try:
            vpath = [tuple(v) for v in pl.get("vpath") or []]
            levels = self._levels()
            if len(vpath) > len(levels) or \
                    any(f != levels[i] for i, (f, _k) in enumerate(vpath)):
                return False
            if vpath and not self._lib.select(vpath):
                return False                # gone since
            self._vpath, self._vstack = vpath, [None] * len(vpath)
            album, path, item = pl.get("album"), pl.get("path"), pl.get("item")
            root = self._root.rstrip("/")
            if album and path and (path == album or path.startswith(album + "/")) \
                    and (album == root or album.startswith(root + "/")) \
                    and os.path.isdir(path):
                self._v_show(None)
                self._album_back = self._sel
                for i, it in enumerate(self._items):
                    if it[1] == album:
                        self._album_back = i
                        break
                self._album_path = album
                self._places_build()
                self._load(path, select=item)
                return True
            self._v_show(None)
            for i, it in enumerate(self._items):
                if it[0] == item:
                    self._sel = i
                    self._paint()
                    break
            return True
        except Exception as e:
            _log("restore place: %s" % e)
            return False

    def _index_check(self):
        """A look for new or changed music, at most every 10 minutes."""
        ix = _mi.indexer
        if not ix.busy and time.time() - ix.last_check > 600:
            ix.start(self._root, self._layout)

    def _index_tick(self):
        ix = _mi.indexer
        if ix.done_count != self._seen_index:
            self._seen_index = ix.done_count
            data = ix.result
            if ix.error:
                self._say("Music index failed: %s" % ix.error)
            elif data is not None and data.get("root") == self._root \
                    and data.get("layout") == self._layout:
                self._lib = _mi.Library(data)
                self._notice = ""
                if self._mode != "folders" and self._album_path is None \
                        and self._search is None and self._plv is None:
                    self._v_refresh()
            self._show_status()
        elif ix.busy:
            self._notice = "%s the music index...  %d albums   %s" % (
                "Building" if ix.kind == "build" else "Checking",
                ix.albums_seen, ix.progress)
            if not queue.active:
                self["status"].setText(self._notice)

    def _levels(self):
        return _mi.MODE_LEVELS.get(self._mode, ())

    def _v_title(self):
        lib = self._lib
        if self._plv is not None:
            return self._plv["open"] or "Playlists"
        if self._search is not None:
            return "Search results"
        if lib is None:
            return "Music"
        if self._vpath:
            f, k = self._vpath[-1]
            return lib.name(f, k)
        return _mi.MODE_NAMES.get(self._mode, "Music")

    def _v_count_text(self):
        lib = self._lib
        if self._plv is not None:
            n = len(self._items)
            what = "track" if self._plv["open"] else "playlist"
            return "%d %s%s" % (n, what, "" if n == 1 else "s")
        if self._search is not None:
            return self._s_count_text()
        if lib is None:
            return ""
        idx = lib.select(self._vpath)
        n = len(idx)
        t = sum(lib.albums[i][_mi.A_TRACKS] for i in idx)
        return "%d album%s   \u00b7   %d track%s" % (
            n, "" if n == 1 else "s", t, "" if t == 1 else "s")

    def _v_show(self, sel=None):
        lib = self._lib
        if lib is None:
            return
        levels = self._levels()
        depth = len(self._vpath)
        idx = lib.select(self._vpath)
        items, notes = [], []
        if self._vpath:
            items.append(("..", None, "up"))
            notes.append("")
        chosen = dict(self._vpath)
        crumbs = ["Music"] + [lib.name(f, k) for f, k in self._vpath]
        if depth < len(levels):
            field = levels[depth]
            crumbs.append(_mi.FIELD_NAMES.get(field, field))
            for key, shown, n in lib.groups(idx, field, self._sort):
                items.append((shown, key, "group"))
                notes.append("%d album%s" % (n, "" if n == 1 else "s"))
        else:
            crumbs.append("Albums")
            for i in lib.sorted_albums(idx, self._sort):
                a = lib.albums[i]
                bits = []
                if "artist" not in chosen:
                    bits.append(a[_mi.A_ARTIST])
                if "year" not in chosen:
                    bits.append(a[_mi.A_YEAR])
                if "genre" not in chosen and a[_mi.A_GENRE]:
                    bits.append(lib.name("genre", lib.keys[i]["genre"]))
                items.append((a[_mi.A_ALBUM], os.path.join(self._root, a[_mi.A_REL]),
                              "album"))
                notes.append("  \u00b7  ".join(b for b in bits if b))
        self._items, self._item_notes = items, notes
        first = 1 if (items and items[0][2] == "up" and len(items) > 1) else 0
        self._sel = sel if (sel is not None and 0 <= sel < len(items)) else first
        self._scroll = 0
        self["header"].setText("  \u203a  ".join(crumbs))
        self._places_build()
        self._paint()
        if not lib.albums:
            self._say("No albums found - check Browse, sort and rescan -> Folder layout")

    def _v_refresh(self):
        """The index has changed: redraw, keeping the highlight on the
        same entry if it's still there."""
        cur = self._items[self._sel][1] if self._items else None
        # Drop choices that no longer exist
        while self._vpath and not self._lib.select(self._vpath):
            self._vpath.pop()
            if self._vstack:
                self._vstack.pop()
        self._v_show(None)
        for i, it in enumerate(self._items):
            if it[1] == cur and cur is not None:
                self._sel = i
                self._paint()
                break

    def _v_enter(self, key):
        levels = self._levels()
        depth = len(self._vpath)
        if depth >= len(levels):
            return
        self._vstack.append(self._sel)
        self._vpath.append((levels[depth], key))
        self._v_show(None)

    def _v_top(self):
        self._vpath, self._vstack, self._album_path = [], [], None
        self._v_show(None)

    def _album_open(self, path):
        self._album_back = self._sel
        self._album_path = path
        self._places_build()
        self._load(path)

    def _v_play(self, key, shuffle):
        """Play / shuffle every album in this list (or in the highlighted
        Artist / Genre / Year), track names from the index."""
        if self._plv is not None:
            if self._plv["open"]:
                self._pl_play(self._plv["open"], shuffle)
            elif self._items:
                self._pl_play(self._items[self._sel][1], shuffle)
            return
        lib = self._lib
        if lib is None or self._busy:
            return
        if self._search is not None:
            self._s_play(shuffle)
            return
        vp = list(self._vpath)
        label = self._v_title()
        if key is not None:
            levels = self._levels()
            if len(vp) < len(levels):
                vp.append((levels[len(vp)], key))
                label = lib.name(levels[len(vp) - 1], key)
        albums = [lib.albums[i] for i in lib.sorted_albums(lib.select(vp), self._sort)]
        root = self._root
        self._busy = True
        self["status"].setText("Finding the music in '%s'..." % label)

        def work():
            try:
                tracks = _mi.album_tracks(root, albums, MAX_TRACKS)
            except Exception as e:
                _log("index gather: %s" % e)
                tracks = []
            self._job = ("gather", tracks, shuffle, label)
        threading.Thread(target=work, name="E2iPTV-Music-Gather", daemon=True).start()

    # -- Search ---------------------------------------------------------------

    def _search_open(self):
        """Red, or Search in the left panel: needs the music index."""
        if not self._root or self._busy:
            return
        if self._lib is not None:
            self._index_check()
            self._search_menu()
            return
        self._busy = True
        self["status"].setText("Opening the music index...")
        root, layout = self._root, self._layout

        def work():
            try:
                data = _mi.load_index(root, layout)
            except Exception as e:
                _log("index load: %s" % e)
                data = None
            self._job = ("sindex", data)
        threading.Thread(target=work, name="E2iPTV-Music-IndexLoad", daemon=True).start()

    def _search_index_loaded(self, data):
        self._show_status()
        if data is None:
            if not _mi.indexer.busy:
                _mi.indexer.start(self._root, self._layout)
            self._say("Building the music index first - Search works once it's done")
            return
        self._lib = _mi.Library(data)
        self._index_check()
        self._search_menu()

    def _crit_text(self, field):
        c, lib = self._crit, self._lib
        if field in ("artist", "genre"):
            keys = c.get(field) or []
            if not keys:
                return "Any"
            names = [lib.name(field, k) for k in keys] if lib else keys
            text = ", ".join(names[:2])
            return text + (" +%d" % (len(names) - 2) if len(names) > 2 else "")
        if field == "year":
            y1, y2 = c.get("y1"), c.get("y2")
            if not y1 and not y2:
                return "Any"
            if y1 == y2:
                return y1
            return "%s \u2013 %s" % (y1 or "...", y2 or "...")
        return c.get(field) or "Any"

    def _search_menu(self):
        from .side_menu import SideMenu
        opts = [("Search everything", "any", self._crit_text("any")),
                ("Artist", "artist", self._crit_text("artist")),
                ("Genre", "genre", self._crit_text("genre")),
                ("Year", "year", self._crit_text("year")),
                ("Album name", "album", self._crit_text("album")),
                ("Track name", "track", self._crit_text("track")),
                ("Search now", "go", ""),
                ("Clear all", "clear", "")]
        self.session.openWithCallback(
            self._search_chosen, SideMenu, "Search music", opts,
            "Filled rows must all match  \u00b7  a comma means 'or'",
            current=self._s_menu_pos, value_only=True)

    def _search_chosen(self, choice):
        keys = ["any", "artist", "genre", "year", "album", "track", "go", "clear"]
        if choice not in keys:
            return                                # Exit: menu closed
        self._s_menu_pos = keys.index(choice)
        if choice in ("any", "album", "track"):
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            title = {"any": "Search everything (artist, genre, year, album, track)",
                     "album": "Album name contains (a comma means 'or')",
                     "track": "Track name contains (a comma means 'or')"}[choice]
            self.session.openWithCallback(
                lambda text, f=choice: self._typed(f, text),
                VirtualKeyBoard, title=title, text=self._crit.get(choice) or "")
        elif choice in ("artist", "genre"):
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            what = "Artist" if choice == "artist" else "Genre"
            self.session.openWithCallback(
                lambda text, f=choice: self._pick_filter(f, text),
                VirtualKeyBoard,
                title="%s name contains (leave empty for all)" % what, text="")
        elif choice == "year":
            self._pick_year_from()
        elif choice == "clear":
            self._crit = dict(_SEARCH_CRIT)
            self._crit["artist"], self._crit["genre"] = [], []
            self._s_menu_pos = 0
            self._search_menu()
        elif choice == "go":
            self._search_run()

    def _typed(self, field, text):
        if text is not None:
            self._crit[field] = " ".join(text.split())
        self._search_menu()

    # Artist / Genre: type part of a name, then tick the ones wanted
    def _pick_filter(self, field, text):
        if text is None:
            self._search_menu()
            return
        lib = self._lib
        want = _mi.norm(text)
        counts = {}
        for k in lib.keys:
            key = k[field]
            if key and (not want or want in key):
                counts[key] = counts.get(key, 0) + 1
        keys = sorted(counts, key=lambda k: _mi._natural_key(_mi.norm(lib.name(field, k))))
        # ones already ticked stay in the list even if they don't match
        for k in self._crit.get(field) or []:
            if k not in counts:
                keys.insert(0, k)
                counts[k] = 0
        self._pick_list = (field, keys[:400], counts, len(keys) > 400)
        self._pick_show(0)

    def _pick_show(self, pos):
        from .side_menu import SideMenu
        field, keys, counts, cut = self._pick_list
        lib = self._lib
        chosen = set(self._crit.get(field) or [])
        what = "Artists" if field == "artist" else "Genres"
        opts = [("Done", "_done", "%d ticked" % len(chosen))]
        for k in keys:
            opts.append((("\u2713  " if k in chosen else "     ") + lib.name(field, k), k,
                         "%d album%s" % (counts[k], "" if counts[k] == 1 else "s")
                         if counts[k] else ""))
        if chosen:
            opts.append(("Untick all", "_none", ""))
        detail = "OK ticks / unticks"
        if not keys:
            detail = "Nothing matches - try fewer letters"
        elif cut:
            detail = "First 400 shown - type more letters to narrow it"
        self.session.openWithCallback(
            lambda v, p=pos: self._pick_chosen(v), SideMenu, what, opts, detail,
            current=pos, value_only=True)

    def _pick_chosen(self, value):
        field, keys, counts, cut = self._pick_list
        if value is None or value == "_done":
            self._search_menu()
            return
        cur = list(self._crit.get(field) or [])
        if value == "_none":
            self._crit[field] = []
            self._pick_show(0)
            return
        if value in cur:
            cur.remove(value)
        else:
            cur.append(value)
        self._crit[field] = cur
        self._pick_show(keys.index(value) + 1 if value in keys else 0)

    # Year: from / to, from the years in the collection
    def _years(self):
        return sorted(k for k in self._lib.names["year"] if k)

    def _pick_year_from(self):
        from .side_menu import SideMenu
        years = self._years()
        opts = [("Any year", "_any", "")] + [(y, y, "") for y in years]
        cur = self._crit.get("y1")
        pos = years.index(cur) + 1 if cur in years else 0
        self.session.openWithCallback(self._year_from_chosen, SideMenu,
                                      "Year from", opts, "The first year to include",
                                      current=pos, value_only=True)

    def _year_from_chosen(self, value):
        if value is None:
            self._search_menu()
            return
        if value == "_any":
            self._crit["y1"] = self._crit["y2"] = ""
            self._search_menu()
            return
        self._crit["y1"] = value
        from .side_menu import SideMenu
        years = [y for y in self._years() if y >= value]
        opts = [("Just %s" % value, value, "")] + [(y, y, "") for y in years if y != value] \
            + [("Any later year", "_open", "")]
        cur = self._crit.get("y2")
        pos = next((i for i, o in enumerate(opts) if o[1] == cur), 0)
        self.session.openWithCallback(self._year_to_chosen, SideMenu,
                                      "Year to", opts, "The last year to include",
                                      current=pos, value_only=True)

    def _year_to_chosen(self, value):
        if value is not None:
            self._crit["y2"] = "" if value == "_open" else value
        self._search_menu()

    # Running it and showing the results
    def _search_summary(self):
        bits = []
        for f in ("any", "artist", "genre", "year", "album", "track"):
            t = self._crit_text(f)
            if t != "Any":
                bits.append(t)
        return "  \u00b7  ".join(bits)

    def _search_run(self):
        if not _mi.criteria_used(self._crit):
            self._say("Fill in at least one row to search")
            self._search_menu()
            return
        if self._busy:
            return
        lib, root, crit, sort = self._lib, self._root, dict(self._crit), self._sort
        self._busy = True
        self["status"].setText("Searching...")
        t0 = time.time()

        def work():
            try:
                res = _mi.search(lib, root, crit, sort)
            except Exception as e:
                _log("search: %s" % e)
                res = ([], [], False)
            _log("search %s: %d albums, %d tracks in %.1f s" % (
                self._search_summary(), len(res[0]), len(res[1]), time.time() - t0))
            self._job = ("search", res, self._search_summary())
        threading.Thread(target=work, name="E2iPTV-Music-Search", daemon=True).start()

    def _search_done(self, res, label):
        albums, tracks, more = res
        if self._search is None:
            # remember where we were, to come back to on Exit
            self._s_back = (list(self._vpath), list(self._vstack), self._sel,
                            self._path, self._album_path)
        self._search = {"albums": albums, "tracks": tracks, "more": more,
                        "label": label}
        self._album_path = None
        self._focus = "files"
        self._s_show(None)
        if not albums and not tracks:
            self._say("Nothing found for: %s" % label)
        else:
            self._notice = ""
            self._show_status()

    def _s_count_text(self):
        sr = self._search
        na, nt = len(sr["albums"]), len(sr["tracks"])
        bits = []
        if na:
            bits.append("%d album%s" % (na, "" if na == 1 else "s"))
        if nt:
            bits.append("%s%d track%s" % ("first " if sr["more"] else "", nt,
                                          "" if nt == 1 else "s"))
        return "   \u00b7   ".join(bits) or "Nothing found"

    def _s_show(self, sel=None):
        sr, lib = self._search, self._lib
        items, notes = [], []
        for i in sr["albums"]:
            a = lib.albums[i]
            items.append((a[_mi.A_ALBUM], os.path.join(self._root, a[_mi.A_REL]), "album"))
            notes.append("  \u00b7  ".join(b for b in (
                a[_mi.A_ARTIST], a[_mi.A_YEAR],
                lib.name("genre", lib.keys[i]["genre"]) if a[_mi.A_GENRE] else "") if b))
        for path, title, i in sr["tracks"]:
            a = lib.albums[i]
            items.append((title, path, False))
            notes.append("%s  \u00b7  %s" % (a[_mi.A_ARTIST], a[_mi.A_ALBUM]))
        self._items, self._item_notes = items, notes
        self._sel = sel if (sel is not None and 0 <= sel < len(items)) else 0
        self._scroll = 0
        self["header"].setText("Music  \u203a  Search:  %s   (%s)" % (
            sr["label"], self._s_count_text()))
        self._places_build()
        self._paint()

    def _s_play(self, shuffle):
        sr = self._search
        tracks = [t[0] for t in sr["tracks"]]
        albums = [self._lib.albums[i] for i in sr["albums"]]
        root = self._root
        self._busy = True
        self["status"].setText("Finding the music in the search results...")

        def work():
            out = list(tracks)
            try:
                if albums:
                    out = _mi.album_tracks(root, albums, MAX_TRACKS) + out
            except Exception as e:
                _log("search gather: %s" % e)
            self._job = ("gather", out[:MAX_TRACKS], shuffle, "search results")
        threading.Thread(target=work, name="E2iPTV-Music-Gather", daemon=True).start()

    def _search_leave(self):
        """Exit from the results: back to where Search was started."""
        self._search = None
        back, self._s_back = self._s_back, None
        self._go_back(back)

    def _here(self):
        """Where we are now, to come back to (see _go_back)."""
        return (list(self._vpath), list(self._vstack), self._sel,
                self._path, self._album_path)

    def _go_back(self, back):
        self._notice = ""
        if back is None:
            back = ([], [], 0, self._root, None)
        vpath, vstack, sel, path, album_path = back
        if self._mode != "folders" and self._lib is not None:
            if album_path:
                self._vpath, self._vstack = vpath, vstack
                self._album_path = album_path
                self._load(path)
            else:
                self._vpath, self._vstack = vpath, vstack
                self._v_show(sel)
        else:
            self._album_path = None
            self._load(path or self._root)

    # -- Playlists ------------------------------------------------------------

    def _plv_open(self):
        """The list of playlists, in the right panel."""
        if self._busy:
            return
        if self._plv is None:
            if self._search is not None:
                back = self._s_back             # back to before the search
                self._search, self._s_back = None, None
            else:
                back = self._here()
            self._plv = {"open": None, "back": back, "sel": 0}
        else:
            self._plv["open"] = None
        self._album_path = None
        self._plv_show(self._plv.get("sel"))

    def _plv_show(self, sel=None):
        pv = self._plv
        if pv is None:
            return                     # left the playlists meanwhile
        items, notes = [], []
        if pv["open"] is None:
            for name, n in _pls.counts():
                items.append((name, name, "plist"))
                notes.append("%d track%s" % (n, "" if n == 1 else "s"))
            self["header"].setText("Music  \u203a  Playlists")
        else:
            for path in _pls.tracks(self._root, pv["open"]):
                items.append((track_name(path), path, False))
                notes.append(self._track_note(path))
            self["header"].setText("Music  \u203a  Playlists  \u203a  %s" % pv["open"])
        self._items, self._item_notes = items, notes
        self._sel = sel if (sel is not None and 0 <= sel < len(items)) else 0
        self._scroll = 0
        self._places_build()
        self._paint()
        if not items:
            self._say("No playlists yet - press Menu on a track, album or artist "
                      "to add it to one" if pv["open"] is None
                      else "This playlist is empty")
        else:
            self._notice = ""
            self._show_status()

    def _plv_up(self):
        pv = self._plv
        if pv["open"] is not None:
            pv["open"] = None
            self._plv_show(pv.get("sel"))
            return
        self._plv = None
        self._go_back(pv["back"])

    def _track_note(self, path):
        """'Artist  ·  Album' from where the track is in the music folder."""
        root = self._root.rstrip("/") + "/"
        if not path.startswith(root):
            return os.path.dirname(path)
        parts = path[len(root):].split("/")
        levels = {"agal": 3, "gaal": 3, "aal": 2}.get(self._layout, 3)
        if len(parts) <= levels:
            return ""
        artist = parts[1] if self._layout == "gaal" else parts[0]
        album = _mi.split_album(parts[levels - 1])[1]
        return "%s  \u00b7  %s" % (artist, album)

    def _pl_play(self, name, shuffle):
        tracks = _pls.tracks(self._root, name)
        if not tracks:
            self._say("This playlist is empty")
            return
        self._job = ("gather", tracks, shuffle, name)

    # Menu: the options for the highlighted entry
    def _item_menu(self):
        from .side_menu import SideMenu
        if self._busy or self._focus != "files":
            return
        it = self._items[self._sel] if self._items else None
        pv = self._plv
        opts = []
        if pv is not None and pv["open"] and it is not None:
            opts = [("Remove from this playlist", "rm"), ("Move up", "up"),
                    ("Move down", "down"), ("Add to another playlist", "add")]
        elif pv is not None:
            if it is not None:
                opts = [("Play", "play"), ("Shuffle", "shuffle"),
                        ("Rename", "rename"), ("Delete this playlist", "del")]
        else:
            if it is not None and it[2] != "up":
                what = {"group": "all of %s" % it[0], "album": "this album",
                        True: "this folder", False: "this track"}.get(it[2], "this")
                opts.append(("Add %s to a playlist" % what, "add"))
            if any(i[2] != "up" for i in self._items) or self._search is not None:
                opts.append(("Add the whole list to a playlist", "addall"))
            opts.append(("Open my playlists", "lists"))
            if it is not None and it[2] is False:
                opts.insert(0, ("Play", "playtrack"))
                opts.append(("Delete this track", "deltrack"))
        if not opts:
            return
        self.session.openWithCallback(
            self._item_chosen, SideMenu, "Music options", opts,
            it[0] if it is not None else "", value_only=True)

    def _item_chosen(self, choice):
        it = self._items[self._sel] if self._items else None
        pv = self._plv
        if choice == "add" and it is not None:
            self._pl_target = ("item", it)
            self._pl_pick()
        elif choice == "addall":
            self._pl_target = ("all", None)
            self._pl_pick()
        elif choice == "lists":
            self._plv_open()
        elif choice == "playtrack" and it is not None:
            self._on_ok()
        elif choice == "deltrack" and it is not None and it[2] is False:
            from .side_menu import SideMenu
            self.session.openWithCallback(
                lambda v, path=it[1]: self._delete_track(path, v), SideMenu,
                "Delete '%s'?" % it[0],
                [("No, keep it", "no"), ("Yes, delete it", "yes")],
                "It goes into the '%s' folder in your music folder - "
                "empty that yourself when you're sure" % DELETED_DIR,
                value_only=True)
        elif choice == "rm" and pv and pv["open"]:
            _pls.remove_track(pv["open"], self._sel)
            self._plv_show(self._sel)
        elif choice in ("up", "down") and pv and pv["open"]:
            j = _pls.move_track(pv["open"], self._sel, -1 if choice == "up" else 1)
            self._plv_show(j)
        elif choice in ("play", "shuffle") and it is not None:
            self._pl_play(it[1], choice == "shuffle")
        elif choice == "rename" and it is not None:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            self.session.openWithCallback(
                lambda new, old=it[1]: self._pl_renamed(old, new),
                VirtualKeyBoard, title="New name for the playlist", text=it[1])
        elif choice == "del" and it is not None:
            from .side_menu import SideMenu
            self.session.openWithCallback(
                lambda v, name=it[1]: self._pl_delete(name, v), SideMenu,
                "Delete '%s'?" % it[1],
                [("No, keep it", "no"), ("Yes, delete it", "yes")],
                "Only the playlist goes - the music stays", value_only=True)

    def _delete_track(self, path, answer):
        """Move the track into <music folder>/Deleted (keeping where it
        was, so nothing clashes), and take it out of the lists."""
        if answer != "yes":
            return
        root = self._root.rstrip("/")
        if path.startswith(root + "/"):
            rel = path[len(root) + 1:]
        else:
            rel = os.path.basename(path)
        dst = os.path.join(root, DELETED_DIR, rel)
        base, ext = os.path.splitext(dst)
        n = 2
        while os.path.exists(dst):
            dst = "%s (%d)%s" % (base, n, ext)
            n += 1
        # Not while it's playing
        if queue.active and queue.current() == path:
            if queue.index + 1 < len(queue.tracks):
                queue.next()
            else:
                queue.stop()
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                os.rename(path, dst)
            except OSError:
                import shutil
                shutil.move(path, dst)
        except Exception as e:
            _log("delete %s: %s" % (path, e))
            self._say("Couldn't delete it: %s" % e)
            return
        _log("deleted (moved to %s): %s" % (DELETED_DIR, path))
        # Out of the queue, the playlists and this list
        if path in queue.tracks:
            i = queue.tracks.index(path)
            if i != queue.index:
                del queue.tracks[i]
                if i < queue.index:
                    queue.index -= 1
        try:
            _pls.remove_path(self._root, path)
        except Exception as e:
            _log("delete: playlists: %s" % e)
        for i, item in enumerate(self._items):
            if item[1] == path:
                del self._items[i]
                if i < len(self._item_notes):
                    del self._item_notes[i]
                break
        if self._search is not None:
            self._search["tracks"] = [t for t in self._search["tracks"] if t[0] != path]
        self._sel = max(0, min(self._sel, len(self._items) - 1))
        self._paint()
        self._say("Deleted '%s'  (in the %s folder)" % (track_name(path), DELETED_DIR))

    def _pl_renamed(self, old, new):
        new = " ".join((new or "").split())
        if new and new != old:
            if not _pls.rename(old, new):
                self._say("There's already a playlist called '%s'" % new)
        self._plv_show(self._sel)

    def _pl_delete(self, name, answer):
        if answer == "yes":
            _pls.delete(name)
            self._say("Deleted the playlist '%s'" % name)
        self._plv_show(max(0, self._sel - 1) if answer == "yes" else self._sel)

    def _pl_pick(self):
        from .side_menu import SideMenu
        opts = [("New playlist...", "_new", "")] + [
            (n, n, "%d track%s" % (c, "" if c == 1 else "s")) for n, c in _pls.counts()
            if not (self._plv and self._plv["open"] == n)]
        self.session.openWithCallback(self._pl_picked, SideMenu,
                                      "Add to playlist", opts, value_only=True)

    def _pl_picked(self, name):
        if not name:
            return
        if name == "_new":
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            self.session.openWithCallback(self._pl_named, VirtualKeyBoard,
                                          title="Name for the new playlist", text="")
            return
        self._pl_add(name)

    def _pl_named(self, name):
        name = " ".join((name or "").split())
        if name:
            self._pl_add(name)

    def _pl_add(self, name):
        """Find the tracks for what was chosen (in the background), then
        add them (_pl_added)."""
        if self._busy:
            return
        kind, it = self._pl_target or ("all", None)
        lib, root = self._lib, self._root
        albums, tracks, folder = [], [], None
        if kind == "item":
            label, path, is_dir = it
            if is_dir is False:
                tracks = [path]
            elif is_dir == "group" and lib is not None:
                vp = list(self._vpath)
                levels = self._levels()
                if len(vp) < len(levels):
                    vp.append((levels[len(vp)], path))
                albums = [lib.albums[i] for i in lib.sorted_albums(lib.select(vp), self._sort)]
            elif is_dir in ("album", True):
                folder = path
        else:
            if self._plv is not None and self._plv["open"]:
                tracks = [i[1] for i in self._items if i[2] is False]
            elif self._search is not None:
                tracks = [t[0] for t in self._search["tracks"]]
                albums = [lib.albums[i] for i in self._search["albums"]]
            elif self._virtual() and lib is not None:
                idx = lib.select(self._vpath)
                albums = [lib.albums[i] for i in lib.sorted_albums(idx, self._sort)]
            else:
                folder = self._path
        self._busy = True
        self["status"].setText("Adding to '%s'..." % name)

        def work():
            out = []
            try:
                if albums:
                    out += _mi.album_tracks(root, albums, MAX_TRACKS)
                if folder:
                    out += gather_tracks(folder)
                out += tracks
            except Exception as e:
                _log("playlist gather: %s" % e)
            self._job = ("pladd", name, out[:MAX_TRACKS])
        threading.Thread(target=work, name="E2iPTV-Music-PlAdd", daemon=True).start()

    def _pl_added(self, name, paths):
        if not paths:
            self._say("No music found to add")
            return
        try:
            n = _pls.add(self._root, name, paths)
        except Exception as e:
            _log("playlist add: %s" % e)
            self._say("Couldn't save the playlist: %s" % e)
            return
        skipped = len(paths) - n
        _log("playlist '%s': added %d tracks" % (name, n))
        self._say("Added %d track%s to '%s'%s" % (
            n, "" if n == 1 else "s", name,
            ("  (%d already in it)" % skipped) if skipped else ""))
        if self._plv is not None:
            self._plv_show(self._sel)

    # -- Browse, sort and rescan (the menu) -----------------------------------

    def _library_menu(self):
        from .side_menu import SideMenu
        ix = _mi.indexer
        if ix.busy:
            detail = "%s the index: %d albums so far" % (
                "Building" if ix.kind == "build" else "Checking", ix.albums_seen)
        elif self._lib is not None:
            detail = "%d albums  \u00b7  %d tracks" % (len(self._lib.albums), self._lib.tracks)
        else:
            detail = "Music index not built yet"
        opts = [("Browse by", "mode", _mi.MODE_NAMES[self._mode]),
                ("Sort albums", "sort", _mi.SORT_NAMES[self._sort]),
                ("Folder layout", "layout", _mi.LAYOUT_NAMES[self._layout]),
                ("Rescan music now", "rescan", "")]
        self.session.openWithCallback(self._library_chosen, SideMenu,
                                      "Music library", opts, detail, value_only=True)

    def _pick(self, title, choices, cur, callback):
        from .side_menu import SideMenu
        keys = [k for k, _n in choices]
        self.session.openWithCallback(
            callback, SideMenu, title,
            [(("\u2713  " if k == cur else "     ") + n, k) for k, n in choices],
            current=keys.index(cur) if cur in keys else 0, value_only=True)

    def _library_chosen(self, choice):
        if choice == "mode":
            self._pick("Browse by", _mi.MODES, self._mode, self._mode_chosen)
        elif choice == "sort":
            self._pick("Sort albums", _mi.SORTS, self._sort, self._sort_chosen)
        elif choice == "layout":
            self._pick("Folder layout", _mi.LAYOUTS, self._layout, self._layout_chosen)
        elif choice == "rescan":
            if not self._root:
                return
            if _mi.indexer.busy:
                self._say("The music index is already being updated")
                return
            _mi.indexer.start(self._root, self._layout, full=True)
            _log("rescan asked for")

    def _mode_chosen(self, key):
        if not key or key not in _mi.MODE_NAMES or key == self._mode:
            return
        self._mode = key
        _save_settings(browse=key)
        self._focus = "files"
        self._search, self._s_back = None, None
        self._plv = None
        self._vpath, self._vstack, self._album_path = [], [], None
        if key == "folders":
            self._notice = ""
            self._load(self._root or self._path)
        else:
            self._index_open()

    def _sort_chosen(self, key):
        if not key or key not in _mi.SORT_NAMES:
            return
        self._sort = key
        _save_settings(sort=key)
        if self._virtual() and self._lib is not None:
            self._v_refresh()

    def _layout_chosen(self, key):
        if not key or key not in _mi.LAYOUT_NAMES or key == self._layout:
            return
        self._layout = key
        _save_settings(layout=key)
        self._lib = None
        self._search, self._s_back = None, None
        self._plv = None
        self._vpath, self._vstack, self._album_path = [], [], None
        if self._mode != "folders" and self._root:
            if not _mi.indexer.busy:
                _mi.indexer.start(self._root, key, full=True)
            self._items, self._item_notes = [], []
            self._paint()
            self._say("Rebuilding the music index for the new layout...")

    # -- the rail -----------------------------------------------------------

    def _handle_child_close(self, result=None):
        if not result or not str(result).startswith("goto:"):
            self._check_root()
            return
        if result == "goto:music":
            self._check_root()          # already here
            return
        self.close(result)

    def _closing(self):
        self._timer.stop()
        self._keys_unbind()
        queue.leave()
        self._remember_place()

    def _remember_place(self):
        """Where Music was, to open there next time: the Browse by place
        (or folder) and the highlighted entry. In Search or Playlists,
        the place they were opened from."""
        try:
            if self._plv is not None:
                back = self._plv.get("back")
            elif self._search is not None:
                back = self._s_back
            else:
                back = None
            if back is not None:
                vpath, _vs, sel, path, album = back
                item = None
            else:
                vpath, path, album = self._vpath, self._path, self._album_path
                item = self._items[self._sel][0] if self._items else None
            _save_settings(place={"mode": self._mode,
                                  "vpath": [list(v) for v in vpath],
                                  "album": album, "path": path, "item": item})
        except Exception as e:
            _log("remember place: %s" % e)

    def get_mirror_b2(self):
        return [(n, None, False) for n, _p, _d in self._items], self._sel

    get_mirror_b2_clamped = True

    def get_mirror_b3(self):
        return [], 0, 0


# =============================================================================
# Now Playing
# =============================================================================

COVER = 600


class MusicPlayer(_MediaKeys, Screen):
    """OK or Play/Pause: pause. Left/Right or |<< >>|: previous/next
    track. 4/6 or << >>: back/forward 10 s (30 s on << >>).
    Stop: stop. Exit: back to the folders, music keeps playing."""

    skin = (
        '<screen name="E2iPTVMusicPlayer" position="0,0" size="1920,1080" flags="wfNoBorder"'
        ' backgroundColor="E2iBg">'
        '<eLabel position="0,0" size="1920,1080" backgroundColor="E2iBg" zPosition="0"/>'
        '<eLabel position="110,150" size="%d,%d" backgroundColor="E2iPanel2" zPosition="1"/>'
        '<widget name="cover" position="110,150" size="%d,%d" alphatest="blend" zPosition="2"/>'
        '<widget name="nocover" position="110,150" size="%d,%d" font="E2iRegular;60"'
        ' foregroundColor="#3a5a8f" halign="center" valign="center" transparent="1" zPosition="2"/>'
        '<widget name="state" position="800,150" size="1000,40" font="E2iRegular;28"'
        ' foregroundColor="#f5c542" transparent="1" zPosition="2"/>'
        '<widget name="title" position="800,200" size="1020,140" font="E2iRegular;54"'
        ' foregroundColor="#ffffff" transparent="1" valign="top" zPosition="2"/>'
        '<widget name="artist" position="800,350" size="1020,50" font="E2iRegular;38"'
        ' foregroundColor="#9fc4ff" transparent="1" noWrap="1" zPosition="2"/>'
        '<widget name="album" position="800,405" size="1020,50" font="E2iRegular;34"'
        ' foregroundColor="#9fc4ff" transparent="1" noWrap="1" zPosition="2"/>'
        '<widget name="trackno" position="800,470" size="1020,40" font="E2iRegular;28"'
        ' foregroundColor="#7f9cc8" transparent="1" zPosition="2"/>'
        '<eLabel position="800,620" size="1000,12" backgroundColor="E2iPanel2" zPosition="1"/>'
        '<widget name="bar" position="800,620" size="1,12" backgroundColor="E2iHi" zPosition="2"/>'
        '<widget name="elapsed" position="800,642" size="300,40" font="E2iRegular;28"'
        ' foregroundColor="#ffffff" transparent="1" zPosition="2"/>'
        '<widget name="length" position="1500,642" size="300,40" font="E2iRegular;28"'
        ' foregroundColor="#ffffff" halign="right" transparent="1" zPosition="2"/>'
        '<widget name="nextup" position="800,710" size="1020,40" font="E2iRegular;28"'
        ' foregroundColor="#7f9cc8" transparent="1" noWrap="1" zPosition="2"/>'
        '<widget name="hint" position="110,1000" size="1700,40" font="E2iRegular;24"'
        ' foregroundColor="#7f9cc8" transparent="1" zPosition="2"/>'
        '</screen>' % ((COVER, COVER) * 3))

    # -- VU meter (below the progress bar) ---------------------------------
    VU_X, VU_W, VU_H = 800, 1000, 32
    VU_Y = (785, 833)                  # left, right
    VU_SEG = 20                        # px per lit segment in the images
    VU_MIN, VU_MAX = -40.0, 0.0        # dB across the meter

    @classmethod
    def _vu_x(cls, db):
        return max(0.0, min(1.0, (db - cls.VU_MIN) / (cls.VU_MAX - cls.VU_MIN))) * cls.VU_W

    # -- spectrum (under the cover) ------------------------------------------
    SP_X, SP_Y, SP_H = 114, 782, 180   # left, top, height of the columns
    SP_PITCH, SP_W = 37, 31            # column spacing and width
    SP_SEG = 12                        # px per segment in the images
    SP_MIN, SP_MAX = -55.0, -15.0      # dB from empty to full

    @classmethod
    def _sp_skin(cls):
        xml = ""
        for i in range(SPEC_BANDS):
            x = cls.SP_X + i * cls.SP_PITCH
            xml += (
                '<ePixmap position="%d,%d" size="%d,%d" pixmap="%smusic_spec_lit.png"'
                ' alphatest="on" zPosition="2"/>'
                '<widget name="sp_dim%d" position="%d,%d" size="%d,%d"'
                ' pixmap="%smusic_spec_dim.png" alphatest="on" zPosition="3"/>'
                '<widget name="sp_cap%d" position="%d,%d" size="%d,3"'
                ' backgroundColor="#ffffff" zPosition="4"/>'
                % (x, cls.SP_Y, cls.SP_W, cls.SP_H, B1_ICON_PATH,
                   i, x, cls.SP_Y, cls.SP_W, cls.SP_H, B1_ICON_PATH,
                   i, x, cls.SP_Y + cls.SP_H, cls.SP_W))
        for i, text in ((0, "50"), (3, "150"), (6, "450"), (9, "1.4k"),
                        (12, "4k"), (15, "12k")):
            x = cls.SP_X + i * cls.SP_PITCH + cls.SP_W // 2
            xml += ('<eLabel text="%s" position="%d,%d" size="70,24" font="E2iRegular;19"'
                    ' foregroundColor="#7f9cc8" backgroundColor="E2iBg"'
                    ' halign="center" transparent="1" zPosition="2"/>'
                    % (text, x - 35, cls.SP_Y + cls.SP_H + 4))
        return xml

    @classmethod
    def _vu_skin(cls):
        xml = ""
        for side, y in zip(("L", "R"), cls.VU_Y):
            xml += (
                '<eLabel text="%s" position="%d,%d" size="30,%d" font="E2iRegular;26"'
                ' foregroundColor="#7f9cc8" backgroundColor="E2iBg" halign="center"'
                ' valign="center" transparent="1" zPosition="2"/>'
                '<ePixmap position="%d,%d" size="%d,%d" pixmap="%smusic_vu_dim.png"'
                ' alphatest="on" zPosition="2"/>'
                '<widget name="vu_%s" position="%d,%d" size="%d,%d"'
                ' pixmap="%smusic_vu_lit.png" alphatest="on" zPosition="3"/>'
                '<widget name="pk_%s" position="%d,%d" size="4,%d"'
                ' backgroundColor="#ffffff" zPosition="4"/>'
                % (side, cls.VU_X - 45, y, cls.VU_H,
                   cls.VU_X, y, cls.VU_W, cls.VU_H, B1_ICON_PATH,
                   side, cls.VU_X, y, cls.VU_W, cls.VU_H, B1_ICON_PATH,
                   side, cls.VU_X, y, cls.VU_H))
        y = cls.VU_Y[1] + cls.VU_H + 6
        for db in (-40, -30, -20, -12, -6, -3, 0):
            x = cls.VU_X + int(cls._vu_x(db))
            xml += ('<eLabel text="%d" position="%d,%d" size="60,30" font="E2iRegular;22"'
                    ' foregroundColor="#7f9cc8" backgroundColor="E2iBg"'
                    ' halign="center" transparent="1" zPosition="2"/>'
                    % (db, x - 30, y))
        xml += ('<eLabel text="dB" position="%d,%d" size="60,30" font="E2iRegular;22"'
                ' foregroundColor="#7f9cc8" backgroundColor="E2iBg"'
                ' halign="left" transparent="1" zPosition="2"/>'
                % (cls.VU_X + cls.VU_W + 30, y))
        return xml

    BAR_W = 1000

    def __init__(self, session):
        Screen.__init__(self, session)
        for name in ("state", "title", "artist", "album", "trackno",
                     "elapsed", "length", "nextup"):
            self[name] = Label("")
        self["nocover"] = Label("Music")
        self["bar"] = Label("")
        self["cover"] = Pixmap()
        self["hint"] = Label(
            "OK: pause     < >: previous / next     4 / 6: back / forward 10 s"
            "     Stop: stop     Exit: back to the folders")
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "NumberActions"],
            {
                "ok":     self._pause,
                "cancel": self.close,
                "left":   self._prev,
                "right":  self._next,
                "up":     lambda: None,
                "down":   lambda: None,
                "4":      lambda: self._seek(-10),
                "6":      lambda: self._seek(10),
            }, -1)
        self._shown_path = None
        self._seen_change = -1
        self._job = None
        self._timer = eTimer()
        self._timer.callback.append(self._tick)
        # VU meter
        for side in ("L", "R"):
            self["vu_" + side] = Pixmap()
            self["pk_" + side] = Label("")
        self._vu = {"L": [MusicPlayer.VU_MIN, MusicPlayer.VU_MIN, 0.0, -1, -1],
                    "R": [MusicPlayer.VU_MIN, MusicPlayer.VU_MIN, 0.0, -1, -1]}
        # (shown level, peak hold, when the peak was set, last width, last x)
        self._vu_last_pos = None
        for i in range(SPEC_BANDS):
            self["sp_dim%d" % i] = Pixmap()
            self["sp_cap%d" % i] = Label("")
        # per band: [shown dB, cap dB, when the cap was set, last dim height, last cap y]
        self._sp = [[self.SP_MIN - 10, self.SP_MIN - 10, 0.0, -1, -1]
                    for _i in range(SPEC_BANDS)]
        try:
            self._vu_offset = float(_settings().get("vu_offset", 0.0))
        except Exception:
            self._vu_offset = 0.0
        self._vu_timer = eTimer()
        self._vu_timer.callback.append(self._vu_tick)
        self.onLayoutFinish.append(self._start)
        self.onClose.append(self._closing)
        self._keys_bind()

    def _start(self):
        try:
            self["cover"].hide()
        except Exception:
            pass
        self._tick()
        self._timer.start(500, False)
        self._vu_draw()
        self._sp_draw()
        self._vu_timer.start(40, False)

    def _closing(self):
        self._timer.stop()
        self._vu_timer.stop()
        self._keys_unbind()

    # -- VU meter -------------------------------------------------------------

    def _vu_tick(self):
        """25 times a second: read the levels at the player's position and
        move the bars - quick to rise, slower to fall, like a real meter,
        with a peak marker that holds for a moment."""
        t0 = time.time()
        try:
            self._vu_step()
            took = time.time() - t0
            if took > 0.1:
                self._slow = getattr(self, "_slow", 0) + 1
                if self._slow <= 5:
                    _log("meter: slow screen update, %.0f ms" % (took * 1000))
        except Exception as e:
            if not getattr(self, "_vu_err", None):
                self._vu_err = str(e)
                _log("meter error: %r" % (e,))
                import traceback
                _log(traceback.format_exc())

    def _vu_step(self):
        lv = queue.levels
        target = None
        if lv is not None and queue.active and not queue.paused:
            pos = queue.position()[0] - self._vu_offset
            last = self._vu_last_pos
            if last is None or pos < last or pos - last > 0.5:
                last = pos - 0.04
            self._vu_last_pos = pos
            queue.check_readers(pos)
            lv = queue.levels or lv
            target = lv.at(max(0.0, last), max(0.0, pos))
            sp = queue.spectrum
            bands = sp.at(max(0.0, last), max(0.0, pos)) if sp is not None else None
            # One report per track, a few seconds in (Music log).
            if pos > 4 and getattr(self, "_vu_reported", None) != lv:
                self._vu_reported = lv
                _log("meter check: position %.1f s, levels decoded %.1f s, "
                     "reading %s, bars %s" % (
                         pos, len(lv.rms_l) * LEVEL_STEP,
                         tuple(round(v, 1) for v in target) if target else None,
                         [(self._vu[k][3], self._vu[k][4]) for k in ("L", "R")]))
        else:
            self._vu_last_pos = None
            bands = None
        now = time.time()
        for i, st in enumerate(self._sp):
            lvl = bands[i] if bands else self.SP_MIN - 10
            # Columns jump up and drop back steadily; caps hold, then fall.
            st[0] = lvl if lvl > st[0] else max(lvl, st[0] - 1.6)
            if lvl >= st[1]:
                st[1], st[2] = lvl, now
            elif now - st[2] > 0.6:
                st[1] = max(self.SP_MIN - 10, st[1] - 1.0)
        self._sp_draw()
        for n, side in enumerate(("L", "R")):
            st = self._vu[side]
            if target is None:
                rms, pk = self.VU_MIN - 10, self.VU_MIN - 10
            else:
                rms, pk = target[n], target[2 + n]
            shown = st[0]
            st[0] = shown + (rms - shown) * (0.6 if rms > shown else 0.2)
            if pk >= st[1]:
                st[1], st[2] = pk, now
            elif now - st[2] > 1.2:
                st[1] = max(self.VU_MIN - 10, st[1] - 1.0)
        self._vu_draw()

    def _sp_h(self, db):
        """Lit height in px for a level, whole segments only."""
        f = max(0.0, min(1.0, (db - self.SP_MIN) / (self.SP_MAX - self.SP_MIN)))
        return int(f * self.SP_H / self.SP_SEG) * self.SP_SEG

    def _sp_draw(self):
        for i, st in enumerate(self._sp):
            dim = self.SP_H - self._sp_h(st[0])        # unlit part, from the top
            if dim != st[3]:
                st[3] = dim
                try:
                    w = self["sp_dim%d" % i]
                    if dim <= 0:
                        w.hide()
                    else:
                        w.instance.resize(eSize(self.SP_W, dim))
                        w.show()
                except Exception as e:
                    _log("spectrum draw: %r" % (e,))
            cap_h = self._sp_h(st[1]) if st[1] > self.SP_MIN else -1
            if cap_h != st[4]:
                st[4] = cap_h
                try:
                    c = self["sp_cap%d" % i]
                    if cap_h <= 0:
                        c.hide()
                    else:
                        c.instance.move(ePoint(self.SP_X + i * self.SP_PITCH,
                                               self.SP_Y + self.SP_H - cap_h - 4))
                        c.show()
                except Exception:
                    pass

    def _vu_draw(self):
        for side, y in zip(("L", "R"), self.VU_Y):
            st = self._vu[side]
            w = int(self._vu_x(st[0]) / self.VU_SEG) * self.VU_SEG
            if w != st[3]:
                st[3] = w
                try:
                    if w <= 0:
                        self["vu_" + side].hide()
                    else:
                        self["vu_" + side].instance.resize(eSize(w, self.VU_H))
                        self["vu_" + side].show()
                except Exception as e:
                    _log("meter draw: %r" % (e,))
            x = int(self._vu_x(st[1]))
            if x != st[4]:
                st[4] = x
                try:
                    if st[1] <= self.VU_MIN:
                        self["pk_" + side].hide()
                    else:
                        self["pk_" + side].instance.move(
                            ePoint(self.VU_X + max(0, x - 4), y))
                        self["pk_" + side].show()
                except Exception:
                    pass

    # keys
    def _pause(self):
        queue.toggle_pause()
        self._tick()

    def _prev(self):
        queue.prev()
        self._tick()

    def _next(self):
        queue.next()
        self._tick()

    def _seek(self, secs):
        queue.seek_by(secs)

    def _media_changed(self):
        self._tick()

    # updates
    def _tick(self):
        job, self._job = self._job, None
        if job is not None and job[0] == queue.current():
            self._show_details(job[1], job[2])
        cur = queue.current()
        if cur != self._shown_path:
            self._shown_path = cur
            self._new_track(cur)
        if queue.changed != self._seen_change:
            self._seen_change = queue.changed
            self["state"].setText(
                "Paused" if queue.paused and queue.active else
                "Playing" if queue.active else "Stopped")
            up = queue.upcoming()
            self["nextup"].setText(("Next:  " + track_name(up)) if up else "")
        self._show_time()

    def _new_track(self, path):
        if not path:
            for name in ("title", "artist", "album", "trackno", "nextup"):
                self[name].setText("")
            return
        artist, album = path_info(path)
        self["title"].setText(track_name(path))
        self["artist"].setText(artist)
        self["album"].setText(album)
        self["trackno"].setText("Track %d of %d" % (queue.index + 1, len(queue.tracks)))
        try:
            self["cover"].hide()
            self["nocover"].show()
        except Exception:
            pass

        def work():
            tags = read_tags(path)
            cover = make_cover(path, COVER)
            self._job = (path, tags, cover)
        threading.Thread(target=work, name="E2iPTV-Music-Tags", daemon=True).start()

    def _show_details(self, tags, cover):
        if tags.get("title"):
            self["title"].setText(tags["title"])
        if tags.get("artist") or tags.get("album_artist"):
            self["artist"].setText(tags.get("artist") or tags.get("album_artist"))
        if tags.get("album"):
            self["album"].setText(tags["album"])
        if cover:
            try:
                pm = LoadPixmap(cover, cached=False,
                                desired_width=COVER, desired_height=COVER)
            except TypeError:
                pm = LoadPixmap(cover, cached=False)
            except Exception:
                pm = None
            if pm:
                try:
                    self["cover"].instance.setPixmap(pm)
                    self["cover"].show()
                    self["nocover"].hide()
                except Exception:
                    pass

    def _show_time(self):
        if not queue.current():
            self["elapsed"].setText("")
            self["length"].setText("")
            w = 1
        else:
            pos, length = queue.position()
            self["elapsed"].setText(fmt_time(pos))
            self["length"].setText(fmt_time(length) if length else "")
            w = int(self.BAR_W * min(1.0, pos / length)) if length else 1
        try:
            self["bar"].instance.resize(eSize(max(1, w), 12))
        except Exception:
            pass


# The meter's widgets go into the skin once the class (and its sizes)
# exist.
MusicPlayer.skin = MusicPlayer.skin.replace(
    "</screen>", MusicPlayer._vu_skin() + MusicPlayer._sp_skin() + "</screen>")


def open_music(session, on_close_callback):
    """Entry point - the rail's "music" action."""
    session.openWithCallback(on_close_callback, MusicBrowser)
