# =============================================================================
# E2iPTV — where the plugin keeps its files
#
# HDD   Data disk. Chosen once at start-up:
#         1. the folder set in Settings -> Other -> Data Folder, saved in
#            /etc/enigma2/E2iPTVLab/data_folder.txt;
#         2. otherwise the first of /media/hdd, /media/usb, /media/mmc
#            that is a real mounted disk (not the box's own flash);
#         3. otherwise /tmp/e2iptv_data — in memory, so nothing is lost
#            from flash, but it is all lost at every restart.
#       NO_DISK is True in case 3, so the plugin can say so.
#
# DATA  <HDD>/E2iPTVLab_Data — all the plugin's own folders (cache, epg,
#       bouquets, order, recordings). Older loose folders are moved in
#       automatically the first time.
#
# LOG() Debug logs in /tmp. Off unless /etc/enigma2/E2iPTVLab/debug exists
#       (an empty file is enough); the few useful ones are always on.
#       All are capped in size.
# =============================================================================

import os

CONFIG_DIR = "/etc/enigma2/E2iPTVLab"
DATA_FOLDER_FILE = os.path.join(CONFIG_DIR, "data_folder.txt")
_CANDIDATES = ("/media/hdd", "/media/usb", "/media/mmc", "/hdd")
_FALLBACK = "/tmp/e2iptv_data"


def _is_real_disk(path):
    """True if `path` is a mounted disk of its own, not a folder on the
    box's flash (an empty /media/hdd folder exists on many boxes)."""
    try:
        real = os.path.realpath(path)
        return os.path.isdir(real) and os.path.ismount(real)
    except Exception:
        return False


def _choose_data_root():
    try:
        with open(DATA_FOLDER_FILE) as f:
            chosen = f.read().strip().rstrip("/")
        if chosen and os.path.isdir(chosen):
            return chosen, False
    except Exception:
        pass
    for cand in _CANDIDATES:
        if _is_real_disk(cand):
            return cand, False
    try:
        os.makedirs(_FALLBACK, exist_ok=True)
    except Exception:
        pass
    return _FALLBACK, True


HDD, NO_DISK = _choose_data_root()

# Everything the plugin keeps goes in one folder on the data disk:
#     <disk>/E2iPTVLab_Data/cache, epg, bouquets, order, recordings
# (recordings themselves still go to the box's normal movie folder, and
# logos to the picons folder set in Settings -> Other).
DATA = HDD + "/E2iPTVLab_Data"

_OLD_TO_NEW = (("e2iptv_cache", "cache"), ("e2iptv_epg", "epg"),
               ("e2iptv_bouquets", "bouquets"), ("e2iptv_order", "order"),
               ("e2iptv_recordings", "recordings"))


def _move_old_folders():
    """One-off: move the folders older versions kept loose on the disk
    (<disk>/e2iptv_cache etc.) into E2iPTV_Data. Same disk, so each move
    is instant. A folder already present in E2iPTV_Data is left alone."""
    try:
        os.makedirs(DATA, exist_ok=True)
    except Exception:
        return
    for old, new in _OLD_TO_NEW:
        src = os.path.join(HDD, old)
        dst = os.path.join(DATA, new)
        if os.path.isdir(src) and not os.path.exists(dst):
            try:
                os.rename(src, dst)
                msg = "moved %s -> %s" % (src, dst)
            except Exception as e:
                msg = "could not move %s: %s" % (src, e)
            try:
                with open(os.path.join(DATA, "moved_folders.log"), "a") as f:
                    f.write(msg + "\n")
            except Exception:
                pass


# (Lab copy: old-folder move switched off - the disk is shared with E2iPTV)


def set_data_folder(path):
    """Save a new data folder (takes effect at the next GUI restart)."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(DATA_FOLDER_FILE, "w") as f:
        f.write(path.rstrip("/") + "\n")


# -----------------------------------------------------------------------------
# Debug logs
# -----------------------------------------------------------------------------

DEBUG = os.path.exists(os.path.join(CONFIG_DIR, "debug"))
_ALWAYS = ("e2iptv_catchup.log", "e2iptv_slow.log", "e2iptv_xtream.log",
           "e2iptv_rules.log", "e2iptv_recordings.log", "e2iptv_music.log",
           "e2iptv_live.log")
_LOG_MAX = 512 * 1024


def cap(path, max_bytes):
    """Start `path` afresh (keeping one .old copy) once it passes max_bytes."""
    try:
        if os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".old")
    except OSError:
        pass


def LOG(name):
    """Path to write debug log `name` to: /tmp/<name> if it's switched on
    (and capped at 512 KB), otherwise /dev/null so nothing is kept."""
    if not (DEBUG or name in _ALWAYS):
        return os.devnull
    path = "/tmp/" + name
    cap(path, _LOG_MAX)
    return path


# -----------------------------------------------------------------------------
# Example playlist for a fresh install
# -----------------------------------------------------------------------------

_EXAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "playlists.example.txt")


def ensure_playlist(path):
    """If the playlist file doesn't exist yet, create it from the example
    (which lists no working providers). Returns True if it was created."""
    if os.path.exists(path):
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(_EXAMPLE) as src, open(path, "w") as dst:
            dst.write(src.read())
        return True
    except Exception:
        return False
