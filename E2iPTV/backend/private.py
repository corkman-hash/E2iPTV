# =============================================================================
# E2iPTV — Private categories
#
# Any Live TV / Movies / Series category can be marked Private (Settings ->
# Playlists -> provider -> Live TV / Movies / Series -> Left/Right on a
# category). Private categories are left out of every list unless they've
# been shown with the hidden key: press 5 twice quickly on Live TV, the EPG
# grid, Movies or Series. Pressing it twice again hides them, and they're
# always hidden again whenever the plugin is opened. No PIN: this is for
# discretion, not security.
#
# Stored apart from the ordinary hidden categories (disabled_categories
# .json) so hiding/unhiding there never touches the Private marks.
# =============================================================================

import json
import os
import time

from ..paths import DATA

PATH = os.path.join(DATA, "cache", "private_categories.json")

_shown = False           # in memory only: every plugin start begins hidden
_cache = None


def _kind(value):
    v = str(value or "live").strip().casefold()
    if v in ("movie", "movies"):
        return "movies"
    if v in ("series", "serieses"):
        return "series"
    return "live"


def _load():
    global _cache
    if _cache is None:
        try:
            with open(PATH) as f:
                data = json.load(f)
            _cache = data if isinstance(data, dict) else {}
        except Exception:
            _cache = {}
    return _cache


def _save():
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_load(), f, indent=1, sort_keys=True)
        os.replace(tmp, PATH)
    except Exception as e:
        print("[E2iPTV] private categories save:", e)


def ids(provider, kind="live"):
    """Category ids marked Private for this provider and content kind."""
    key = str(getattr(provider, "provider_key", "") or "")
    if not key:
        return set()
    return set(str(x) for x in (_load().get(key, {}).get(_kind(kind)) or []))


def is_private(provider, kind, category_id):
    return str(category_id) in ids(provider, kind)


def mark(provider, kind, category_id, private=True):
    key = str(getattr(provider, "provider_key", "") or "")
    if not key:
        return
    data = _load()
    cur = set(str(x) for x in (data.setdefault(key, {}).get(_kind(kind)) or []))
    if private:
        cur.add(str(category_id))
    else:
        cur.discard(str(category_id))
    data[key][_kind(kind)] = sorted(cur)
    _save()


def hidden(provider, kind="live"):
    """Category ids to leave out right now (none while Private is shown)."""
    if _shown:
        return set()
    return ids(provider, kind)


def shown():
    return _shown


def set_shown(value):
    global _shown
    _shown = bool(value)


def hide():
    set_shown(False)


# -- the hidden key: 5 pressed twice quickly ----------------------------------

_last_press = [0.0]
_DOUBLE = 1.5            # seconds between the two presses


def key_pressed(session, on_change=None):
    """Call on every press of 5. On the second press within 1.5 s, flip
    Private shown/hidden, say so briefly, and call on_change()."""
    now = time.time()
    if now - _last_press[0] > _DOUBLE:
        _last_press[0] = now
        return
    _last_press[0] = 0.0
    set_shown(not _shown)
    try:
        from Screens.MessageBox import MessageBox
        session.open(MessageBox, "Private shown" if _shown else "Private hidden",
                     MessageBox.TYPE_INFO, timeout=2)
    except Exception:
        pass
    if on_change:
        try:
            on_change()
        except Exception as e:
            print("[E2iPTV] private refresh:", e)
