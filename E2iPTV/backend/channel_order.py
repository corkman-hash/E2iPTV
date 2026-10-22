# =============================================================================
# E2iPTV Backend — Custom channel order
#
# Each Live TV category has a plain text file on the hard disk that sets
# the order its channels are shown in:
#
#     /hdd/e2iptv_order/<Provider>__<provider_key>/<category_id> - <Category>.txt
#
# One channel per line: its ID, then its name (the name is only there so
# you can see what you're moving — matching is done on the ID):
#
#     1234  UK: BBC One FHD
#     1240  UK: ITV 1 HD
#
# To reorder, edit the file on a computer (FTP / network share) and move
# lines around, then save it back. The next time that category loads,
# the channels appear in the file's order. The order is applied every
# time a category's channels are loaded, so it survives provider
# refreshes, restarts and the overnight EPG job.
#
# To HIDE a channel, put a - at the start of its line:
#
#     - 1250  UK: Some Channel I Never Watch
#
# It stays in the file (so you can show it again by removing the -) but
# isn't shown anywhere: not in the lists, the EPG grid or channel up/down.
#
# The file is created automatically, in the provider's order, the first
# time a category loads. After that:
#   - new channels from the provider go to the bottom and are added to
#     the file;
#   - channels the provider removes are dropped from the file;
#   - if the provider renumbers a channel, it is found again by name;
#   - blank lines, lines starting with #, unknown IDs and duplicates are
#     ignored.
# =============================================================================

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import os
import re
import threading

ORDER_ROOT = DATA + "/order"

_HEADER = (
    "# E2iPTV channel order for: %s / %s\n"
    "# One channel per line: ID then name. Move lines to change the order.\n"
    "# Matching is by ID (by name if the provider renumbers a channel).\n"
    "# New channels are added at the bottom automatically.\n"
    "# Put - at the start of a line to hide that channel; remove it to show it again.\n"
    "# Lines starting with # are ignored.\n"
)

_lock = threading.Lock()


def _safe(text):
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(text or "")).strip(" .")
    return text[:80] or "_"


def _provider_dir(provider):
    key = str(getattr(provider, "provider_key", "") or "")
    try:
        for name in os.listdir(ORDER_ROOT):
            if name.endswith("__" + key) and os.path.isdir(os.path.join(ORDER_ROOT, name)):
                return os.path.join(ORDER_ROOT, name)   # keep it even if renamed
    except OSError:
        pass
    return os.path.join(ORDER_ROOT, "%s__%s" % (_safe(getattr(provider, "name", "")), key))


def _category_file(provider, category):
    folder = _provider_dir(provider)
    cid = _safe(getattr(category, "category_id", ""))
    try:
        for name in os.listdir(folder):
            if name.endswith(".txt") and (name == cid + ".txt" or name.startswith(cid + " - ")):
                return os.path.join(folder, name)       # keep it even if renamed
    except OSError:
        pass
    return os.path.join(
        folder, "%s - %s.txt" % (cid, _safe(getattr(category, "category_name", "")))
    )


def _read(path):
    """[(id, name, hidden), ...] from the file, or None if there is no file."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except (IOError, OSError):
        return None
    text = raw.decode("utf-8", "replace").lstrip("\ufeff")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        hidden = line.startswith("-")
        if hidden:
            line = line[1:].strip()
            if not line:
                continue
        parts = line.split(None, 1)
        rows.append((parts[0], parts[1].strip() if len(parts) > 1 else "", hidden))
    return rows


def _rows_for(entries):
    """[(channel, hidden), ...] -> [(id, name, hidden), ...]"""
    return [(str(c.stream_id), (c.name or "").strip(), bool(h)) for c, h in entries]


def _write(path, provider, category, rows):
    folder = os.path.dirname(path)
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
        body = _HEADER % (getattr(provider, "name", ""), getattr(category, "category_name", ""))
        body += "".join(
            "%s%s  %s\n" % ("- " if hidden else "", cid, name.replace("\n", " "))
            for cid, name, hidden in rows
        )
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.rename(tmp, path)
    except Exception as e:
        print("[E2iPTV] channel order: could not write %s: %s" % (path, e))


def apply(provider, category, channels):
    """Return `channels` sorted by this category's order file, creating
    or updating the file as needed. Never raises: on any problem the
    provider's own order is returned unchanged."""
    if not channels:
        return channels
    try:
        with _lock:
            return _apply(provider, category, channels)
    except Exception as e:
        print("[E2iPTV] channel order: %s" % e)
        return channels


def _apply(provider, category, channels):
    path = _category_file(provider, category)
    rows = _read(path)

    if rows is None:
        _write(path, provider, category, _rows_for([(c, False) for c in channels]))
        return channels

    by_id = {}
    by_name = {}
    for c in channels:
        by_id.setdefault(str(c.stream_id), c)
        by_name.setdefault((c.name or "").strip().casefold(), c)

    ordered = []          # (channel, hidden)
    used = set()
    for cid, name, hidden in rows:
        ch = by_id.get(cid)
        if ch is None and name:
            ch = by_name.get(name.casefold())        # provider renumbered it
        if ch is None or id(ch) in used:
            continue
        ordered.append((ch, hidden))
        used.add(id(ch))

    full = ordered + [(c, False) for c in channels if id(c) not in used]

    # Keep the file in step with the provider: added channels at the
    # bottom, removed/renumbered ones tidied up, names current.
    current = _rows_for(full)
    if current != rows:
        _write(path, provider, category, current)

    return [c for c, hidden in full if not hidden]

def save(provider, category, channels):
    """Write this category's order exactly as `channels` (the visible
    channels) is now — used by the EPG grid's Move Channel. Hidden
    channels aren't in that list, so they're kept, still hidden, at the
    bottom of the file."""
    if not channels or provider is None or category is None:
        return
    with _lock:
        path = _category_file(provider, category)
        old = _read(path) or []
        hidden = [r for r in old if r[2]]
        hidden_ids = set(r[0] for r in hidden)
        rows = [r for r in _rows_for([(c, False) for c in channels])
                if r[0] not in hidden_ids]
        _write(path, provider, category, rows + hidden)


def hide(provider, category, channel):
    """Mark one channel hidden in this category's file (EPG grid's
    Hide Channel). Shown again by removing the - from its line."""
    if provider is None or category is None or channel is None:
        return
    with _lock:
        path = _category_file(provider, category)
        rows = _read(path) or []
        cid = str(channel.stream_id)
        found = False
        for i, (rid, name, hidden) in enumerate(rows):
            if rid == cid:
                rows[i] = (rid, name, True)
                found = True
        if not found:
            rows.append((cid, (channel.name or "").strip(), True))
        _write(path, provider, category, rows)


def hidden(provider, category):
    """[(id, name), ...] of this category's hidden channels."""
    if provider is None or category is None:
        return []
    with _lock:
        rows = _read(_category_file(provider, category)) or []
    return [(rid, name) for rid, name, h in rows if h]


def unhide(provider, category, channel_ids):
    """Show hidden channels again (EPG grid's Show Hidden Channels).
    They keep their place in the file."""
    ids = set(str(i) for i in channel_ids)
    if provider is None or category is None or not ids:
        return
    with _lock:
        path = _category_file(provider, category)
        rows = _read(path) or []
        rows = [(rid, name, h and rid not in ids) for rid, name, h in rows]
        _write(path, provider, category, rows)
