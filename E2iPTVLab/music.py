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
from Components.GUIComponent import GUIComponent
from Tools.LoadPixmap import LoadPixmap
from enigma import (
    eTimer, eListbox, eListboxPythonMultiContent, eServiceReference,
    eActionMap, iPlayableService, gFont, eSize, ePoint,
    RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER,
)

from .paths import DATA, LOG
from .b1common import B1_ICON_PATH

DEFAULT_ROOT = ""                   # none: chosen in Settings -> Playback
SETTINGS_PATH = os.path.join(DATA, "cache", "music.json")
LOG_PATH = LOG("e2iptv_music.log")
FFMPEG = "/usr/bin/ffmpeg"

AUDIO_EXT = (".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus",
             ".wav", ".wma", ".ape", ".wv", ".aiff", ".aif", ".alac",
             ".mka", ".mp2", ".ac3", ".dsf", ".dff")
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
                folders.append((name, full))
            elif is_audio(name):
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
            if not name.startswith(".") and is_audio(name):
                out.append(os.path.join(dirpath, name))
                if len(out) >= limit:
                    return out
    return out


def track_name(path):
    """File name without the extension or a leading track number."""
    name = os.path.splitext(os.path.basename(path))[0]
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
        ref = eServiceReference(4097, 0, path)
        ref.setName(track_name(path))
        _log("play %d/%d: %s" % (self.index + 1, len(self.tracks),
                                 os.path.basename(path)))
        self._started_at = time.time()
        self._stop_readers()
        play_pos[0] = 0.0
        self.levels = Levels(path)
        self.spectrum = Spectrum(path)
        try:
            self.session.nav.playService(ref)
        except Exception as e:
            _log("play failed: %s" % e)
        self.changed += 1

    def stop(self):
        """Stop the music (stay silent)."""
        if self.active:
            _log("stop")
        self.active = False
        self.paused = False
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
        path = self.current()
        if not path or not self.active:
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
                and cur.getPath() == self.current()
        except Exception:
            return False

    def _event(self, evt):
        if not self.active:
            return
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
_PL_ROW_H, _PL_ROWS = 62, 5
_PL_Y = _P_Y + 16
_F_ROW_H, _F_ROWS = 66, 13
_F_Y = _P_Y + 16
_INFO_Y = _PL_Y + _PL_ROWS * _PL_ROW_H + 30
_THUMB = 240


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
        self._load(self._path)

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
        places.append(("play", "Play this folder"))
        places.append(("shuffle", "Shuffle this folder"))
        if queue.active:
            places.append(("stop", "Stop music"))
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
            if is_dir == "up":
                cells = [(24, w - 48, 0, "..", _P_ACCENT, RT_HALIGN_LEFT)]
            elif is_dir:
                cells = [(24, w - 48, 0, "▸  " + name, _P_TEXT, RT_HALIGN_LEFT)]
            else:
                base, ext = os.path.splitext(name)
                playing = path == cur
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
        self._load(root)

    def _show_status(self):
        if self._busy:
            return
        cur = queue.current() if queue.active else None
        if not cur:
            self["status"].setText(self._notice)
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
                return ("folder", self._path)
            if key == "root":
                return ("folder", self._root)
            return ("none", key)
        if not self._items:
            return ("none", None)
        name, path, is_dir = self._items[self._sel]
        if is_dir == "up":
            return ("folder", path)
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
        else:
            self._info_text(self._places_help(path), "", "", "")
        if kind in ("track", "folder") and path:
            self._info_due = time.time() + 0.4

    def _info_now(self):
        self._info_key = None
        self._info_soon()

    @staticmethod
    def _places_help(key):
        return {"stop": "Stop the music",
                "now": "Open Now Playing"}.get(key, "")

    def _info_text(self, title, artist, album, detail):
        for n, v in (("i_title", title), ("i_artist", artist),
                     ("i_album", album), ("i_detail", detail)):
            self[n].setText(v or "")

    def _info_start(self):
        target = self._info_key
        if not target:
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

    def _move(self, d):
        if self._focus == "places":
            if self._places:
                self._pl_sel = max(0, min(len(self._places) - 1, self._pl_sel + d))
                self._paint_places()
                self._info_soon()
            return
        if self._items:
            self._sel = max(0, min(len(self._items) - 1, self._sel + d))
            self._paint()

    def _on_ok(self):
        if self._focus == "places":
            self._place_ok()
            return
        if self._busy or not self._items:
            return
        name, path, is_dir = self._items[self._sel]
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
            self._play_folder(self._path, key == "shuffle")
        elif key == "stop":
            queue.stop()
            self._places_build()
            self._paint()
            self._show_status()
        elif key == "root" and self._root:
            self._focus = "files"
            self._load(self._root)

    def _play_all(self, shuffle):
        """Green / Yellow: the highlighted folder, or this folder."""
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
