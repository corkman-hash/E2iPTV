# =============================================================================
# E2iPTV — the plugin's own font name, "E2iRegular"
#
# All E2iPTV screens use "E2iRegular" instead of the skin's "Regular", so
# the plugin keeps one look whichever skin is chosen:
#   1. /usr/share/fonts/nmsbd.ttf when the box has it (the font the
#      TiviAtv skins use for "Regular")
#   2. otherwise the file the current skin uses for "Regular" - the plugin
#      then looks exactly as it did before this existed
#   3. otherwise any font in /usr/share/fonts, so text is never invisible
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
    path = PREFERRED if os.path.isfile(PREFERRED) else (_skin_regular() or _any_font())
    if not path:
        return
    try:
        from enigma import addFont
        addFont(path, NAME, 100, False)
        print("[E2iPTV] font %s = %s" % (NAME, path))
    except Exception as e:
        print("[E2iPTV] font setup failed:", e)
