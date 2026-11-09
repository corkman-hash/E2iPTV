# =============================================================================
# E2iPTV - Music playlists (your own, made on the box)
#
# Kept in <data>/cache/music_playlists.json:
#   {"lists": [{"name": "Love songs", "tracks": ["Artist/Genre/2018 - Album/01 - X.mp3", ...]}]}
# Tracks inside the music folder are kept relative to it, so the lists
# still work if the NAS is mounted somewhere else and the music folder is
# changed to match; anything outside it is kept as a full path.
# =============================================================================

import json
import os
import time

from .paths import DATA

PATH = os.path.join(DATA, "cache", "music_playlists.json")


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("lists"), list):
            return data
    except Exception:
        pass
    return {"lists": []}


def _save(data):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    os.replace(tmp, PATH)


def _rel(root, path):
    if root and path.startswith(root.rstrip("/") + "/"):
        return path[len(root.rstrip("/")) + 1:]
    return path


def full(root, entry):
    return entry if entry.startswith("/") else os.path.join(root, entry)


def names():
    return [p["name"] for p in _load()["lists"]]


def counts():
    return [(p["name"], len(p.get("tracks", []))) for p in _load()["lists"]]


def tracks(root, name):
    for p in _load()["lists"]:
        if p["name"] == name:
            return [full(root, t) for t in p.get("tracks", [])]
    return []


def add(root, name, paths):
    """Add tracks to a playlist (made if new). Tracks already in it are
    not added twice. Returns how many were added."""
    data = _load()
    pl = next((p for p in data["lists"] if p["name"] == name), None)
    if pl is None:
        pl = {"name": name, "tracks": [], "created": int(time.time())}
        data["lists"].append(pl)
    have = set(pl["tracks"])
    n = 0
    for path in paths:
        r = _rel(root, path)
        if r not in have:
            pl["tracks"].append(r)
            have.add(r)
            n += 1
    _save(data)
    return n


def remove_path(root, path):
    """Take a (deleted) track out of every playlist."""
    r = _rel(root, path)
    data = _load()
    changed = False
    for p in data["lists"]:
        keep = [t for t in p.get("tracks", []) if t != r and t != path]
        if len(keep) != len(p.get("tracks", [])):
            p["tracks"] = keep
            changed = True
    if changed:
        _save(data)
    return changed


def remove_track(name, index):
    data = _load()
    for p in data["lists"]:
        if p["name"] == name and 0 <= index < len(p["tracks"]):
            del p["tracks"][index]
            _save(data)
            return True
    return False


def move_track(name, index, d):
    """Move a track up (-1) or down (+1). Returns its new place."""
    data = _load()
    for p in data["lists"]:
        if p["name"] == name:
            t = p["tracks"]
            j = index + d
            if 0 <= index < len(t) and 0 <= j < len(t):
                t[index], t[j] = t[j], t[index]
                _save(data)
                return j
            return index
    return index


def rename(old, new):
    data = _load()
    if any(p["name"] == new for p in data["lists"]):
        return False
    for p in data["lists"]:
        if p["name"] == old:
            p["name"] = new
            _save(data)
            return True
    return False


def delete(name):
    data = _load()
    data["lists"] = [p for p in data["lists"] if p["name"] != name]
    _save(data)
