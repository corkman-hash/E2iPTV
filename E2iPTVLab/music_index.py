# =============================================================================
# E2iPTV - Music index
#
# A list of every album and track in the music folder, so Music can be
# browsed by Artist / Genre / Year / Album (and, later, searched) without
# going back to the NAS for every screen.
#
# Everything comes from the folder and file names - the music files are
# never opened:
#     Artist / Genre / 2018 - Album / 01 - Title.mp3      (layout "agal")
#     Genre / Artist / 2018 - Album / 01 - Title.mp3      (layout "gaal")
#     Artist / 2018 - Album / 01 - Title.mp3              (layout "aal")
# The year is taken from a "2018 - " at the start of the album folder's
# name, if there is one. Audio in folders inside an album folder (CD1,
# CD 01/Album name, Pack1/..., up to DEEP levels down) counts as part of
# that album, in folder order - so a box set is one album, disc by disc.
# A check only notices new files directly in the album folder; ones
# added deeper show after "Rescan music now".
#
# Kept in <data>/music_index/:
#   albums.json     the albums (artist, genre, year, album, folder, number
#                   of tracks) and the "last changed" time of every folder
#   tracks_X.json   the track names, one file per first letter of the top
#                   folder (A..Z, # for the rest) - only read for playing a
#                   whole list and, later, for searching track names
#
# Keeping it up to date: every folder's "last changed" time moves when
# something is added or removed directly inside it. A check reads those
# times only (no listing) and re-reads just the folders whose time has
# moved. The first build reads everything once. Both run in the
# background and write their timings to e2iptv_music.log.
# =============================================================================

import json
import os
import re
import threading
import time
import unicodedata

from .paths import DATA

INDEX_DIR = os.path.join(DATA, "music_index")
ALBUMS_PATH = os.path.join(INDEX_DIR, "albums.json")
FORMAT = 1
TRACKER_TOP = "tracker files"   # this top folder: see _tracker_top
DEEP = 4          # folder levels looked into inside an album folder

LAYOUTS = (
    ("agal", "Artist / Genre / Album"),
    ("gaal", "Genre / Artist / Album"),
    ("aal", "Artist / Album"),
)
LAYOUT_NAMES = dict(LAYOUTS)
DEFAULT_LAYOUT = "agal"

# What each folder level is, for each layout (the last one is the album)
_LEVELS = {
    "agal": ("artist", "genre", "album"),
    "gaal": ("genre", "artist", "album"),
    "aal": ("artist", "album"),
}

# Album record (a list, to keep albums.json small):
A_REL, A_ARTIST, A_GENRE, A_YEAR, A_ALBUM, A_TRACKS, A_MTIME = range(7)

_YEAR_RE = re.compile(r"^\s*((?:19|20)\d\d)\s*[-‐‑‒–—―]\s*(.+)$")
_TRACKNO_RE = re.compile(r"^\s*(\d{1,3})\s*[-._)]?\s+")
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"',
           ord("”"): '"', ord("´"): "'", ord("`"): "'"}


def _log(msg):
    try:
        from .music import _log as mlog
        mlog("index: " + msg)
    except Exception:
        pass


# -- names ----------------------------------------------------------------------

def norm(text):
    """For matching and grouping: no capitals, all dashes and quotes the
    same, accents removed, spaces tidied."""
    t = (text or "").translate(_DASHES).translate(_QUOTES)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.casefold().split())


def tidy(text):
    """How a genre is shown: 'hard rock' becomes 'Hard Rock'; names
    with any capitals of their own (EDM, R&B, Alt Metal) are left alone."""
    t = " ".join((text or "").split())
    if t and t == t.lower():
        return " ".join(w[:1].upper() + w[1:].lower() for w in t.split(" "))
    return t


def split_album(name):
    """'2018 - Vale' -> ('2018', 'Vale'); 'Vale' -> ('', 'Vale')."""
    m = _YEAR_RE.match(name)
    if m:
        return m.group(1), m.group(2).strip()
    return "", name.strip()


def split_track(filename):
    """'01 - So Cold.mp3' -> (1, 'So Cold')."""
    base = os.path.splitext(os.path.basename(filename))[0]
    m = _TRACKNO_RE.match(base)
    if m:
        title = base[m.end():].strip() or base
        return int(m.group(1)), title
    return 0, base


def letter_of(name):
    c = norm(name)[:1]
    return c.upper() if "a" <= c <= "z" else "#"


def _natural_key(name):
    return [int(t) if t.isdigit() else t.casefold()
            for t in re.split(r"(\d+)", name)]


def _tracker_title(path):
    from .music_tracker import header_title
    return header_title(path)


try:
    from .music_tracker import TRACKER_EXT as _TRACKER_EXT
except Exception:
    _TRACKER_EXT = ()


def _in_tracker_folder(path):
    from .music_tracker import in_tracker_folder
    return in_tracker_folder(path)


def _maybe_tracker(full, name):
    from .music_tracker import maybe_tracker, sniff
    return maybe_tracker(name) and bool(sniff(full))


def _is_audio(name):
    from .music import is_audio
    return is_audio(name)


# -- files ----------------------------------------------------------------------

def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _tracks_path(letter):
    return os.path.join(INDEX_DIR, "tracks_%s.json" % ("0" if letter == "#" else letter))


def load_index(root, layout):
    """The saved index, or None if there's none for this folder/layout."""
    data = _read_json(ALBUMS_PATH)
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return None
    if data.get("root") != root or data.get("layout") != layout:
        return None
    return data


def load_tracks(letter):
    """{album folder: [[file, number, title, tidied title], ...]} for one
    letter (indexes made before the tidied title was added lack it until
    the next check)."""
    data = _read_json(_tracks_path(letter))
    return data if isinstance(data, dict) else {}


def album_tracks(root, albums, limit=10000):
    """Full paths of the tracks of these albums, in album order."""
    by_letter = {}
    out = []
    for a in albums:
        rel = a[A_REL]
        letter = letter_of(rel.split("/", 1)[0])
        if letter not in by_letter:
            by_letter[letter] = load_tracks(letter)
        for t in by_letter[letter].get(rel, ()):
            out.append(os.path.join(root, rel, t[0]))
            if len(out) >= limit:
                return out
    return out


# -- building / updating --------------------------------------------------------

class _Indexer(object):
    """One build or check at a time, in the background. The Music screen
    looks at .busy / .progress / .done_count to show how it's going and
    picks up .result when it finishes."""

    def __init__(self):
        self.busy = False
        self.kind = ""            # "build" or "check"
        self.progress = ""
        self.albums_seen = 0
        self.done_count = 0       # goes up each time one finishes
        self.result = None        # the new index (dict), or None
        self.error = ""
        self.last_check = 0
        self._stop = False

    def start(self, root, layout, full=False):
        if self.busy or not root:
            return False
        old = None if full else load_index(root, layout)
        self.busy = True
        self.kind = "check" if old else "build"
        self.progress = ""
        self.albums_seen = 0
        self.error = ""
        self._stop = False
        threading.Thread(target=self._run, args=(root, layout, old),
                         name="E2iPTV-Music-Index", daemon=True).start()
        return True

    def stop(self):
        self._stop = True

    def _run(self, root, layout, old):
        t0 = time.time()
        stats = {"stat": 0, "list": 0, "albums_read": 0}
        try:
            data = self._update(root, layout, old, stats)
            if data is None:
                _log("%s stopped" % self.kind)
                self.result = None
            else:
                secs = time.time() - t0
                data["seconds"] = round(secs, 1)
                _write_json(ALBUMS_PATH, data)
                ntracks = sum(a[A_TRACKS] for a in data["albums"])
                _log("%s: %d albums, %d tracks, %d folders checked, "
                     "%d folders read (%d albums re-read) in %.1f s"
                     % (self.kind, len(data["albums"]), ntracks,
                        stats["stat"], stats["list"], stats["albums_read"], secs))
                self.result = data
            self.last_check = time.time()
        except Exception as e:
            self.error = str(e)
            _log("%s failed: %s" % (self.kind, e))
            self.result = None
        self.busy = False
        self.done_count += 1

    def _update(self, root, layout, old, stats):
        levels = _LEVELS.get(layout, _LEVELS[DEFAULT_LAYOUT])
        depth = len(levels)                      # albums are at this depth
        old_dirs = (old or {}).get("dirs", {})   # rel -> [mtime, [children]]
        old_albums = {a[A_REL]: a for a in (old or {}).get("albums", [])}
        new_dirs = {}
        new_albums = []
        # Album folders with no music in them: remembered (with their
        # "last changed" time) so a check only re-reads them if they change
        # (Kept only if they were looked for as deep as now.)
        self._old_empty = (old or {}).get("empty", {}) \
            if (old or {}).get("deep") == DEEP else {}
        self._new_empty = {}

        def mtime(full):
            stats["stat"] += 1
            return os.stat(full).st_mtime

        def subdirs(full):
            """(folders in it, True if music files sit loose in it)."""
            stats["list"] += 1
            out, loose = [], False
            for n in os.listdir(full):
                if n.startswith("."):
                    continue
                if _is_audio(n):
                    loose = True
                    continue
                try:
                    if os.path.isdir(os.path.join(full, n)):
                        out.append(n)
                except Exception:
                    pass
            out.sort(key=_natural_key)
            return out, loose

        # The top level, then one letter's worth of top folders at a time,
        # so only one tracks_X.json is in memory at once.
        m_root = mtime(root)
        prev = old_dirs.get("")
        top = prev[1] if (prev and prev[0] == m_root) else subdirs(root)[0]
        new_dirs[""] = [m_root, top]

        letters = {}
        for name in top:
            if norm(name) == "deleted":
                continue                  # Music's own bin (Delete this track)
            letters.setdefault(letter_of(name), []).append(name)

        written = set()
        for letter in sorted(letters):
            if self._stop:
                return None
            old_tracks = load_tracks(letter) if old else {}
            new_tracks = {}
            for name in letters[letter]:
                self.progress = name
                self._visit(root, name, 1, depth, levels, [name],
                            old_dirs, old_albums, old_tracks,
                            new_dirs, new_albums, new_tracks,
                            mtime, subdirs, stats)
                if self._stop:
                    return None
            _write_json(_tracks_path(letter), new_tracks)
            written.add(_tracks_path(letter))

        # Letters that no longer have anything
        try:
            for n in os.listdir(INDEX_DIR):
                p = os.path.join(INDEX_DIR, n)
                if n.startswith("tracks_") and n.endswith(".json") and p not in written:
                    os.remove(p)
        except Exception:
            pass

        if sorted(self._new_empty) != sorted(self._old_empty):
            names = sorted(self._new_empty)
            _log("%d album folders have no music in them%s" % (
                len(names), ":" if names else ""))
            for n in names[:300]:
                _log("    no music: %s" % n)
            if len(names) > 300:
                _log("    ... and %d more" % (len(names) - 300))
        return {"format": FORMAT, "root": root, "layout": layout,
                "built": int(time.time()), "dirs": new_dirs,
                "albums": new_albums, "empty": self._new_empty,
                "deep": DEEP}

    def _visit(self, root, rel, level, depth, levels, names,
               old_dirs, old_albums, old_tracks,
               new_dirs, new_albums, new_tracks, mtime, subdirs, stats):
        full = os.path.join(root, rel)
        try:
            m = mtime(full)
        except Exception:
            return                                # gone since it was listed
        if level == depth:
            self._album(full, rel, m, levels, names, old_albums, old_tracks,
                        new_albums, new_tracks, stats)
            return
        prev = old_dirs.get(rel)
        try:
            if prev and prev[0] == m and len(prev) > 2:
                kids, loose = prev[1], prev[2]
            else:
                kids, loose = subdirs(full)
        except Exception:
            return
        new_dirs[rel] = [m, kids, loose]
        tracker = level == 1 and norm(names[0]) == TRACKER_TOP
        if loose:
            # Music sitting loose above album level: an album of its own,
            # named after its folder (only these files, not the folders in it)
            fixed = (names[0], "Tracker", names[0]) if tracker else None
            self._album(full, rel, m, levels, names, old_albums, old_tracks,
                        new_albums, new_tracks, stats, deep=0, fixed=fixed)
        if tracker:
            self._tracker_top(root, rel, kids, names, old_albums, old_tracks,
                              new_albums, new_tracks, mtime, stats)
            return
        for k in kids:
            if self._stop:
                return
            self._visit(root, rel + "/" + k, level + 1, depth, levels, names + [k],
                        old_dirs, old_albums, old_tracks,
                        new_dirs, new_albums, new_tracks, mtime, subdirs, stats)

    def _tracker_top(self, root, rel, kids, names, old_albums, old_tracks,
                     new_albums, new_tracks, mtime, stats):
        """Music/Tracker Files/<folder>/...: each folder in it is an album
        (MOD, S3M, IT ...), artist 'Tracker Files', genre 'Tracker' -
        tracker files can be copied straight in, with no Artist / Genre /
        Album folders to make."""
        for k in kids:
            if self._stop:
                return
            krel = rel + "/" + k
            full = os.path.join(root, krel)
            try:
                km = mtime(full)
            except Exception:
                continue
            self._album(full, krel, km, None, names + [k], old_albums, old_tracks,
                        new_albums, new_tracks, stats,
                        fixed=(names[0], "Tracker", k))

    def _album(self, full, rel, m, levels, names, old_albums, old_tracks,
               new_albums, new_tracks, stats, deep=DEEP, fixed=None):
        prev = old_albums.get(rel)
        if prev is not None and prev[A_MTIME] == m and rel in old_tracks:
            new_albums.append(prev)
            kept = old_tracks[rel]
            for t in kept:
                if len(t) < 4:                 # index from before 4th field
                    t.append(norm(t[2]))
            new_tracks[rel] = kept
            self.albums_seen += 1
            return
        if prev is None and self._old_empty.get(rel) == m:
            self._new_empty[rel] = m           # still the same empty folder
            return
        # New or changed: list it (and any folders inside it, CD1 etc.)
        stats["list"] += 1
        stats["albums_read"] += 1
        files = []

        tracker_tree = _in_tracker_folder(full + "/")

        def walk(folder, prefix, level):
            try:
                entries = sorted(os.listdir(folder), key=_natural_key)
            except Exception:
                return
            subs = []
            for n in entries:
                if n.startswith("."):
                    continue
                if _is_audio(n) or (tracker_tree and _maybe_tracker(os.path.join(folder, n), n)):
                    files.append(prefix + n)
                elif level < deep:
                    try:
                        if os.path.isdir(os.path.join(folder, n)):
                            subs.append(n)
                    except Exception:
                        pass
            for n in subs:
                stats["list"] += 1
                walk(os.path.join(folder, n), prefix + n + "/", level + 1)

        walk(full, "", 0)
        if not files:
            self._new_empty[rel] = m
            return
        if fixed:
            artist, genre, folder = fixed
        else:
            info = dict(zip(levels, names))
            artist, genre = info.get("artist", ""), info.get("genre", "")
            folder = info.get("album", names[-1])
        year, album = split_album(folder)
        tracks = []
        for f in files:
            num, title = split_track(f)
            if f.lower().endswith(_TRACKER_EXT) or tracker_tree:
                # the song title stored inside the file, if it has one
                own = _tracker_title(os.path.join(full, f))
                if not own:
                    from .music import track_name
                    own = " ".join(track_name(f).replace("_", " ").split())
                title = own or title
            tracks.append([f, num, title, norm(title)])
        rec = [rel, artist, genre, year, album, len(tracks), m]
        new_albums.append(rec)
        new_tracks[rel] = tracks
        self.albums_seen += 1


indexer = _Indexer()


# -- browsing ---------------------------------------------------------------------

MODES = (
    ("folders", "Folders (as on disk)"),
    ("artist", "Artist › Album"),
    ("genre", "Genre › Artist › Album"),
    ("year", "Year › Artist › Album"),
    ("album", "All albums"),
)
MODE_NAMES = dict(MODES)
MODE_LEVELS = {"artist": ("artist",), "genre": ("genre", "artist"),
               "year": ("year", "artist"), "album": ()}
FIELD_NAMES = {"artist": "Artists", "genre": "Genres", "year": "Years"}

SORTS = (
    ("az", "A–Z"),
    ("za", "Z–A"),
    ("old", "Oldest first"),
    ("new", "Newest first"),
)
SORT_NAMES = dict(SORTS)

_FIELD_COL = {"artist": A_ARTIST, "genre": A_GENRE, "year": A_YEAR}


class Library(object):
    """The index ready for browsing: the albums, each with a grouping
    key per field (no capitals, dashes alike), and one display name per
    key (the first one met; genres tidied)."""

    def __init__(self, data):
        self.albums = data.get("albums", []) if data else []
        self.built = data.get("built", 0) if data else 0
        self.tracks = sum(a[A_TRACKS] for a in self.albums)
        self.keys = []                        # per album: {field: key}
        self.names = {"artist": {}, "genre": {}, "year": {}}
        for a in self.albums:
            k = {}
            for field, col in _FIELD_COL.items():
                raw = a[col] or ""
                key = norm(raw)
                k[field] = key
                if key not in self.names[field]:
                    if not raw:
                        shown = "Unknown " + field
                    else:
                        shown = tidy(raw) if field == "genre" else raw
                    self.names[field][key] = shown
            self.keys.append(k)

    def name(self, field, key):
        return self.names.get(field, {}).get(key, key)

    def select(self, vpath):
        """Indexes of the albums matching every (field, key) chosen."""
        out = []
        for i, k in enumerate(self.keys):
            if all(k[f] == v for f, v in vpath):
                out.append(i)
        return out

    def groups(self, idx, field, sort):
        """[(key, shown, number of albums)] for one field."""
        count = {}
        for i in idx:
            key = self.keys[i][field]
            count[key] = count.get(key, 0) + 1
        keys = list(count)
        if field == "year":
            known = sorted(k for k in keys if k)
            if sort in ("za", "new"):
                known.reverse()
            keys = known + [k for k in keys if not k]
        else:
            keys.sort(key=lambda k: (k == "", _natural_key(norm(self.name(field, k)))),
                      reverse=False)
            if sort == "za":
                keys = [k for k in keys if k][::-1] + [k for k in keys if not k]
        return [(k, self.name(field, k), count[k]) for k in keys]

    def sorted_albums(self, idx, sort):
        al = self.albums

        def by_name(i):
            return (_natural_key(norm(al[i][A_ALBUM])), al[i][A_YEAR])

        def by_year(i):
            return (al[i][A_YEAR] or "9999", _natural_key(norm(al[i][A_ALBUM])))

        if sort in ("az", "za"):
            out = sorted(idx, key=by_name, reverse=(sort == "za"))
        else:
            known = sorted((i for i in idx if al[i][A_YEAR]), key=by_year,
                           reverse=(sort == "new"))
            out = known + sorted((i for i in idx if not al[i][A_YEAR]), key=by_name)
        return out


# -- searching --------------------------------------------------------------------

# The tidied track names of every album, one string per album (names on
# separate lines), kept in memory after the first search: about 5 MB for
# 170,000 tracks. Made again when the index changes.
_cache = {"lib": None, "names": None, "hays": None}


def _title_cache(lib):
    if _cache["lib"] is lib and _cache["names"] is not None:
        return _cache["names"]
    names = {}
    letters = sorted(set(letter_of(a[A_REL].split("/", 1)[0]) for a in lib.albums))
    for letter in letters:
        for rel, tracks in load_tracks(letter).items():
            names[rel] = "\n".join(t[3] if len(t) > 3 else norm(t[2]) for t in tracks)
    _cache["lib"], _cache["names"], _cache["hays"] = lib, names, None
    return names


def _album_hays(lib):
    """Per album: its artist, genre, year and album name, tidied, for
    Search everything."""
    if _cache["lib"] is lib and _cache["hays"] is not None:
        return _cache["hays"]
    hays = [norm(" ".join((a[A_ARTIST], a[A_GENRE], a[A_YEAR], a[A_ALBUM])))
            for a in lib.albums]
    _cache["hays"] = hays
    return hays


def terms(text):
    """'love, heart' -> ['love', 'heart'] (a comma means OR)."""
    return [t for t in (norm(x) for x in (text or "").split(",")) if t]


def empty_criteria():
    return {"any": "", "artist": [], "genre": [], "y1": "", "y2": "",
            "album": "", "track": ""}


def criteria_used(c):
    return bool(c.get("any") or c.get("artist") or c.get("genre") or
                c.get("y1") or c.get("y2") or c.get("album") or c.get("track"))


def search(lib, root, c, sort="az", limit=2000, progress=None):
    """Every filled row must match (AND); within a row any one of the
    ticked values / comma-separated words will do (OR).

    Returns (albums, tracks, more): album indexes (in the current sort),
    tracks as [(path, title, album index)], and True if there were more
    than `limit` tracks. Without a Track name or Search everything, only
    albums are returned (the album list is the answer)."""
    artists = set(c.get("artist") or ())
    genres = set(c.get("genre") or ())
    y1, y2 = c.get("y1") or "", c.get("y2") or ""
    album_t = terms(c.get("album"))
    track_t = terms(c.get("track"))
    any_t = terms(c.get("any"))

    cand = []
    for i, a in enumerate(lib.albums):
        k = lib.keys[i]
        if artists and k["artist"] not in artists:
            continue
        if genres and k["genre"] not in genres:
            continue
        if y1 or y2:
            y = a[A_YEAR]
            if not y or (y1 and y < y1) or (y2 and y > y2):
                continue
        if album_t and not any(t in norm(a[A_ALBUM]) for t in album_t):
            continue
        cand.append(i)

    if not track_t and not any_t:
        return lib.sorted_albums(cand, sort), [], False

    names = _title_cache(lib)
    hays = _album_hays(lib)
    hits = []                                  # (album index, need any_t in title)
    for i in cand:
        rel = lib.albums[i][A_REL]
        blob = names.get(rel, "")
        hit_album = bool(any_t) and any(t in hays[i] for t in any_t)
        if hit_album and not track_t:
            hits.append((i, None))             # the album itself is the answer
            continue
        # Quick look at all the album's track names at once
        if track_t and not any(x in blob for x in track_t):
            continue
        if any_t and not hit_album and not any(x in blob for x in any_t):
            continue
        hits.append((i, hit_album))

    albums_out, tracks_out, more = [], [], False
    by_letter = {}
    for i, h in hits:
        if h is None:
            albums_out.append(i)
        else:
            letter = letter_of(lib.albums[i][A_REL].split("/", 1)[0])
            by_letter.setdefault(letter, []).append((i, h))
    done = 0
    for letter in sorted(by_letter):
        tracks = load_tracks(letter)
        for i, hit_album in by_letter[letter]:
            a = lib.albums[i]
            for t in tracks.get(a[A_REL], ()):
                title = t[3] if len(t) > 3 else norm(t[2])
                if track_t and not any(x in title for x in track_t):
                    continue
                if any_t and not hit_album and not any(x in title for x in any_t):
                    continue
                if len(tracks_out) >= limit:
                    more = True
                    break
                tracks_out.append((os.path.join(root, a[A_REL], t[0]), t[2], i))
        done += 1
        if progress:
            progress(done, len(by_letter))
    # Tracks: by artist, then year, then album, in track order
    order = {}
    for n, i in enumerate(lib.sorted_albums(list(set(t[2] for t in tracks_out)), "old")):
        order[i] = n
    tracks_out.sort(key=lambda t: (norm(lib.albums[t[2]][A_ARTIST]), order.get(t[2], 0)))
    return lib.sorted_albums(albums_out, sort), tracks_out, more
