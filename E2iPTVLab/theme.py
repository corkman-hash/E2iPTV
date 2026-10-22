# =============================================================================
# E2iPTV Lab - colour themes
#
# Every screen takes its main colours from here instead of having them
# written in. Two ways they are used:
#   - in skins, by name: backgroundColor="E2iPanel" (named colours, which
#     the box's skin code looks up - registered by register() below)
#   - in code, as numbers: _T.PANEL (rows drawn by the plugin itself)
#
# The theme is read once when the plugin loads, so a change of theme
# takes effect after a GUI restart (Settings -> Appearance offers it).
#
# Roles (name in skins / number in code):
#   bg      E2iBg      _T.BG       screen background, top line
#   panel   E2iPanel   _T.PANEL    panels and lists
#   rail    E2iRail    _T.RAIL     icon rail, pop-up menus, season tabs
#   panel2  E2iPanel2  _T.PANEL2   poster / picture placeholders
#   muted   E2iMuted   _T.MUTED    grey highlight (not focused)
#   hi      E2iHi      _T.HI       the highlight, headings, accents
#   sep     E2iSep     _T.SEP      thin dividing lines
#   dim     E2iDim     _T.DIM      dates, notes, second lines
#   hist    -          _T.HIST     EPG grid tiles already shown (catch-up)
# =============================================================================

import json
import os

ROLES = ("bg", "panel", "rail", "panel2", "muted", "hi", "sep", "dim", "hist")

THEMES = {
    # The Lab look (TiviMate / TiviAtv style)
    "dark": {
        "label": "Dark (default)",
        "bg": 0x0a0f13, "panel": 0x14181d, "rail": 0x25292f, "panel2": 0x1b2026,
        "muted": 0x3a4048, "hi": 0x3ea6ff, "sep": 0x2a3038, "dim": 0x9aa4ae,
        "hist": 0x4a5a70,
    },
    # The original E2iPTV colours
    "skyblue": {
        "label": "Sky Blue (original E2iPTV)",
        "bg": 0x0b1a33, "panel": 0x0a4aa8, "rail": 0x1a98ff, "panel2": 0x1b2d4f,
        "muted": 0x2a4a7a, "hi": 0xd0e8ff, "sep": 0x2a4a7a, "dim": 0xaaddff,
        "hist": 0x009999,
    },
}
ORDER = ("dark", "skyblue")
DEFAULT = "dark"

_NAMES = {"bg": "E2iBg", "panel": "E2iPanel", "rail": "E2iRail",
          "panel2": "E2iPanel2", "muted": "E2iMuted", "hi": "E2iHi",
          "sep": "E2iSep", "dim": "E2iDim"}


def _settings_path():
    try:
        from .paths import DATA
        return DATA + "/cache/app_settings.json"
    except Exception:
        return None


def current_key():
    p = _settings_path()
    try:
        with open(p) as f:
            k = json.load(f).get("theme")
        return k if k in THEMES else DEFAULT
    except Exception:
        return DEFAULT


def save_key(key):
    if key not in THEMES:
        return
    p = _settings_path()
    data = {}
    try:
        with open(p) as f:
            data = json.load(f)
    except Exception:
        pass
    data["theme"] = key
    try:
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(p, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


KEY = current_key()
_t = THEMES[KEY]

BG, PANEL, RAIL, PANEL2 = _t["bg"], _t["panel"], _t["rail"], _t["panel2"]
MUTED, HI, SEP, DIM = _t["muted"], _t["hi"], _t["sep"], _t["dim"]
HIST = _t["hist"]          # EPG grid: programmes already shown (catch-up)


def hexs(role):
    """"14181d"-style text for a role (for skins built with "#%s")."""
    return "%06x" % _t[role]


def register():
    """Make the named colours (E2iPanel ...) known to the box's skin code,
    so the plugin's skins can use them. Called when the plugin loads."""
    try:
        import skin
        from enigma import gRGB
        table = getattr(skin, "colors", None)
        if table is None:
            return
        for role, name in _NAMES.items():
            table[name] = gRGB(_t[role])
        print("[E2iPTV] theme: %s" % KEY)
    except Exception as e:
        print("[E2iPTV] theme setup failed:", e)
