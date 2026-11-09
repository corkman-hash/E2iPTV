# =============================================================================
# E2iPTV - differences between images
#
# OpenATV / OpenViX can draw list rows with rounded corners
# (MultiContentEntryText(cornerRadius=...)); OpenPLi can't, and stops with
# "unexpected keyword argument 'cornerRadius'". Files import
# MultiContentEntryText from here: where the image has no rounded corners,
# the rows are simply drawn square.
# =============================================================================

from Components.MultiContent import MultiContentEntryText as _MultiContentEntryText


def _rounded_ok():
    try:
        _MultiContentEntryText(pos=(0, 0), size=(1, 1), text="", cornerRadius=1)
        return True
    except TypeError:
        return False
    except Exception:
        return True


ROUNDED_ROWS = _rounded_ok()
if not ROUNDED_ROWS:
    print("[E2iPTV] this image has no rounded list rows - drawing them square")


def MultiContentEntryText(**kw):
    if not ROUNDED_ROWS:
        kw.pop("cornerRadius", None)
        kw.pop("cornerEdges", None)
    return _MultiContentEntryText(**kw)
