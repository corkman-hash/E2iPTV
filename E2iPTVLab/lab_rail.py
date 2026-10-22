# =============================================================================
# E2iPTV Lab - the icon rail over the new two-panel screens
#
# Recordings, Music, Favourites, Movies and Series open the Menu-button
# rail (rail_menu.py) over their own left edge, so the screen keeps its
# own look underneath while you choose (and Music keeps playing).
# Picking another section closes the screen with "goto:<action>", which
# Live TV (screen1) acts on, the same as the old rail did.
# =============================================================================

from .state import state


def open_rail(screen, own_label):
    from .rail_menu import RailMenu
    screen.session.openWithCallback(
        lambda choice=None: _picked(screen, own_label, choice),
        RailMenu, own_label)


def _picked(screen, own_label, choice):
    if not choice:
        return                      # Exit / Right: stay where we were
    label, action = choice
    if label == own_label:
        return
    # Live TV and EPG Grid share the "live" hand-off (as b1common does).
    state.live_choice = ("full" if label == "Live TV" else
                         "grid" if label == "EPG Grid" else None)
    if action != "exit":
        try:
            from .b1common import save_b1_last_choice
            save_b1_last_choice(label)
        except Exception:
            pass
    screen.close("goto:%s" % action)
