from .paths import HDD, cap  # data folder / debug logs
# The plugin's own font name (fonts.py) - set up before any screen.
try:
    from .fonts import register as _register_font
    _register_font()
except Exception as _e:
    print('[E2iPTV] font:', _e)
from .paths import DATA  # E2iPTV_Data folder
from Plugins.Plugin import PluginDescriptor
from enigma import eTimer
import time

from .screen1 import Screen1
from .state import state

# By the time this import completes, .state and .player are both fully
# loaded (Screen1 pulls them in transitively) — so it's safe to call
# this here, once per process, rather than from inside state.py's own
# AppState.__init__ (which would circular-import .player mid-construction).
# See reconcile_active_recordings()'s docstring in player.py for why this
# matters: without it, a recording still in progress when the plugin last
# stopped running just silently vanishes from the UI on the next start.
from .player import reconcile_active_recordings
reconcile_active_recordings()


EPG_SCRIPT = "/usr/lib/enigma2/python/Plugins/Extensions/E2iPTV/bulk_epg_fetch.py"
EPG_LOG = DATA + "/epg/nightly_refresh.log"


class EpgScheduler(object):

    def __init__(self):
        self.timer = eTimer()
        self.timer.callback.append(self._run)
        self._schedule()

    def _schedule(self):

        import json
        import time

        fetch_hour = 20

        try:
            with open(
                DATA + "/cache/app_settings.json",
                "r"
            ) as f:
                settings = json.load(f)

            fetch_hour = int(
                settings.get("epg_fetch_hour", 20)
            )

        except Exception:
            fetch_hour = 20

        fetch_hour = max(0, min(23, fetch_hour))

        now = time.localtime()

        seconds = (
            ((fetch_hour - now.tm_hour) % 24) * 3600
            - now.tm_min * 60
            - now.tm_sec
        )

        if seconds <= 0:
            seconds += 86400

        self.timer.stop()
        self.timer.start(
            seconds * 1000,
            True
        )

    def _run(self):

        import os
        import subprocess

        try:

            os.makedirs(
                DATA + "/epg",
                exist_ok=True
            )

            cap(EPG_LOG, 5 * 1024 * 1024)  # start afresh past 5 MB (keeps one .old)
            with open(
                EPG_LOG,
                "a"
            ) as log:

                log.write(
                    "\n===== EPG SCHEDULED REFRESH =====\n"
                )

                env = os.environ.copy()
                plugin_parent = "/usr/lib/enigma2/python/Plugins/Extensions"
                env["PYTHONPATH"] = (
                    plugin_parent
                    + ":"
                    + env.get("PYTHONPATH", "")
                )

                process = subprocess.Popen(
                    [
                        "python3",
                        EPG_SCRIPT,
                        "--playlists",
                        state.playlists_path
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=env
                )

                log.write(
                    "Started bulk EPG fetch PID %d\n"
                    % process.pid
                )

        except Exception as ex:

            try:

                with open(
                    EPG_LOG,
                    "a"
                ) as log:

                    log.write(
                        "FAILED TO START EPG FETCH: %s\n"
                        % ex
                    )

            except Exception:
                pass

        self._schedule()


_epg_scheduler = None


def _autostart(reason, **kwargs):

    global _epg_scheduler

    try:
        with open(
            DATA + "/epg/scheduler_start.log",
            "a"
        ) as log:
            log.write(
                "%s AUTOSTART reason=%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), reason)
            )
    except Exception:
        pass

    if reason == 0:

        # Nightly guide refresh (at Settings -> EPG -> Refresh Time).
        if _epg_scheduler is None:
            _epg_scheduler = EpgScheduler()
            try:
                with open(
                    DATA + "/epg/scheduler_start.log",
                    "a"
                ) as log:
                    log.write(
                        "%s SCHEDULER CREATED\n" % time.strftime("%Y-%m-%d %H:%M:%S")
                    )
            except Exception:
                pass


def main(session, **kwargs):
    session.open(Screen1)


# -----------------------------------------------------------------------------
# Open at start-up (Settings -> Playback -> Open E2iPTV at start-up)
# -----------------------------------------------------------------------------

def autostart_enabled():
    import json
    try:
        with open(DATA + "/cache/app_settings.json") as f:
            return bool(json.load(f).get("open_at_startup", False))
    except Exception:
        return False


_startup_timer = None


def _sessionstart(reason, session=None, **kwargs):
    """Once the TV screen is up after a box/GUI start: open E2iPTV, if
    switched on. A few seconds' delay lets the box finish starting; if it
    started in standby, wait until it's switched on."""
    global _startup_timer
    if reason != 0 or session is None or not autostart_enabled():
        return

    def _open():
        try:
            from Screens import Standby
            if Standby.inStandby:
                # open when the box leaves standby (once)
                Standby.inStandby.onClose.append(lambda: _later(3000))
                return
        except Exception:
            pass
        try:
            session.open(Screen1)
        except Exception as e:
            print("[E2iPTV] open at start-up failed:", e)

    def _later(ms):
        global _startup_timer
        _startup_timer = eTimer()
        _startup_timer.callback.append(_open)
        _startup_timer.start(ms, True)

    _later(5000)


def Plugins(**kwargs):

    return [
        PluginDescriptor(
            name="E2iPTV",
            description="IPTV Browser",
            where=PluginDescriptor.WHERE_PLUGINMENU,
            fnc=main
        ),

        PluginDescriptor(
            name="E2iPTV start-up",
            description="Opens E2iPTV when the box starts (if switched on)",
            where=PluginDescriptor.WHERE_SESSIONSTART,
            fnc=_sessionstart
        ),

        PluginDescriptor(
            name="E2iPTV EPG Scheduler",
            description="E2iPTV EPG background scheduler",
            where=PluginDescriptor.WHERE_AUTOSTART,
            fnc=_autostart
        )
    ]
