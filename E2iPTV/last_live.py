# =============================================================================
# E2iPTV — the Live TV channel to come back to
#
# Saved the moment a live channel actually starts playing (full screen,
# channel up/down, the channel list, swap), not when Live TV is left —
# so it's right even after a restart that never went through Exit, and
# browsing the grid without playing anything doesn't change it.
#
#     <data>/E2iPTV_Data/cache/live_last_channel.json
#
# A My Bouquets channel is saved under its own provider, so it can be
# restarted straight away without loading the bouquet first.
# =============================================================================

import json
import os

from .paths import DATA

FILE = DATA + "/cache/live_last_channel.json"

_last = None


def load():
    try:
        with open(FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def remember(provider, channel, category_id=""):
    """Save `channel` (a live channel now playing) as the one to resume."""
    global _last
    if provider is None or channel is None:
        return
    # Where it sits in Live TV (a bouquet, if played from My Bouquets),
    # so Live TV reopens on that category with this channel highlighted.
    view_key = str(getattr(provider, "provider_key", "") or "")
    # The channel's own category first: it is always the one it was
    # listed in (the bouquet name for a My Bouquets channel), even if
    # the player's channel list has moved to another category since.
    view_cat = str(getattr(channel, "category_id", "") or category_id or "")
    real = getattr(channel, "_bq_provider", None)
    if real is not None:
        provider = real
        category_id = getattr(channel, "_bq_src_cat", "") or category_id
    if getattr(provider, "is_bouquet", False):
        return
    data = {
        "provider_key": str(getattr(provider, "provider_key", "") or ""),
        "category_id": str(category_id or getattr(channel, "category_id", "") or ""),
        "stream_id": str(getattr(channel, "stream_id", "") or ""),
        "name": getattr(channel, "name", "") or "",
        "direct_source": getattr(channel, "direct_source", "") or "",
        "view_provider_key": view_key,
        "view_category_id": view_cat,
    }
    if not data["provider_key"] or not data["stream_id"] or data == _last:
        return
    _last = data
    try:
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        tmp = FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.rename(tmp, FILE)
    except Exception:
        pass
