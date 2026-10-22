# =============================================================================
# E2iPTV — the plugin's own font name, "E2iRegular"
#
# All E2iPTV screens use "E2iRegular" instead of the skin's "Regular".
# Lab: a normal-weight font, not the bold one (nmsbd.ttf) used before:
#   1. the file the current skin uses for "Regular", unless that is a
#      bold font
#   2. otherwise a "...-Regular" font in the skin's own folder
#      (E2iSkin-FHD has Inter-Regular.ttf)
#   3. otherwise a non-bold font in /usr/share/fonts
#   4. otherwise any font, so text is never invisible
# Nothing is copied or packaged: only fonts already on the box are used.
# =============================================================================

import os
import re

NAME = "E2iRegular"
PREFERRED = "/usr/share/fonts/nmsbd.ttf"
_done = False


def _skin_regular():
    """Path of the font file the current skin calls "Regular", or None."""
    try:
        skin = "skin.xml"
        with open("/etc/enigma2/settings") as f:
            for line in f:
                if line.startswith("config.skin.primary_skin="):
                    skin = line.split("=", 1)[1].strip()
        skin_dir = os.path.dirname(os.path.join("/usr/share/enigma2", skin))
        files = [os.path.join("/usr/share/enigma2", skin)]
        files += [os.path.join(skin_dir, n) for n in os.listdir(skin_dir)
                  if n.endswith(".xml")]
        for path in files:
            try:
                with open(path, errors="replace") as f:
                    text = f.read()
            except Exception:
                continue
            for tag in re.findall(r"<font\s[^>]*>", text):
                if re.search(r'name="Regular"', tag):
                    m = re.search(r'filename="([^"]+)"', tag)
                    if not m:
                        continue
                    name = m.group(1)
                    for cand in (name, os.path.join(skin_dir, name),
                                 os.path.join(skin_dir, "fonts", name),
                                 os.path.join("/usr/share/fonts", os.path.basename(name))):
                        if os.path.isfile(cand):
                            return cand
    except Exception:
        pass
    return None


def _is_bold(path):
    n = os.path.basename(path).lower()
    return "bold" in n or "bd" in os.path.splitext(n)[0][-3:] or "black" in n \
        or "heavy" in n


def _skin_dir():
    try:
        skin = "skin.xml"
        with open("/etc/enigma2/settings") as f:
            for line in f:
                if line.startswith("config.skin.primary_skin="):
                    skin = line.split("=", 1)[1].strip()
        return os.path.dirname(os.path.join("/usr/share/enigma2", skin))
    except Exception:
        return None


def _skin_folder_regular():
    d = _skin_dir()
    if not d:
        return None
    for sub in (d, os.path.join(d, "fonts")):
        try:
            for n in sorted(os.listdir(sub)):
                if n.lower().endswith(("-regular.ttf", "-regular.otf")):
                    return os.path.join(sub, n)
        except Exception:
            pass
    return None


def _plain_system_font():
    try:
        names = sorted(os.listdir("/usr/share/fonts"))
    except Exception:
        return None
    fonts = [os.path.join("/usr/share/fonts", n) for n in names
             if n.lower().endswith((".ttf", ".otf"))]
    for p in fonts:
        if "regular" in os.path.basename(p).lower() and not _is_bold(p):
            return p
    for p in fonts:
        if not _is_bold(p):
            return p
    return None


def _normal_font():
    skin = _skin_regular()
    if skin and not _is_bold(skin):
        return skin
    return _skin_folder_regular() or _plain_system_font() or skin


def _any_font():
    try:
        for n in sorted(os.listdir("/usr/share/fonts")):
            if n.lower().endswith((".ttf", ".otf")):
                return os.path.join("/usr/share/fonts", n)
    except Exception:
        pass
    return None


def register():
    """Set up E2iRegular once per start of the box's GUI."""
    global _done
    if _done:
        return
    _done = True
    path = _normal_font() or _any_font()
    if not path:
        return
    try:
        from enigma import addFont
        addFont(path, NAME, 100, False)
        print("[E2iPTV] font %s = %s" % (NAME, path))
    except Exception as e:
        print("[E2iPTV] font setup failed:", e)
