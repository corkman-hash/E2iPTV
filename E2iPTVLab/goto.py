# =============================================================================
# E2iPTV — "go to section" hand-off for the Menu pop-up on the player
#
# The full-screen player can't open Series (etc.) itself without stacking
# screens on top of it. Instead it leaves a short-lived note here and
# closes the normal way. Each screen underneath that becomes active again
# acts on the note: the EPG grid steps back, Series/Movies hands it on,
# and Live TV opens the chosen section exactly as its own rail would.
# The note expires after a few seconds, so it can never fire later.
# =============================================================================

import time

_pending = None             # (action, time set)
_MAX_AGE = 8                # seconds


def request(action):
    global _pending
    _pending = (action, time.time())


def peek():
    """The pending action if still fresh, else None (and forget it)."""
    global _pending
    if _pending is None:
        return None
    action, when = _pending
    if time.time() - when > _MAX_AGE:
        _pending = None
        return None
    return action


def take():
    """The pending action if still fresh, and clear it."""
    global _pending
    action = peek()
    _pending = None
    return action


def nav_log(msg):
    """Menu / Live TV hand-off steps (always on, small: /tmp/e2iptv_nav.log)."""
    try:
        from .paths import LOG
        with open(LOG("e2iptv_nav.log"), "a") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass
