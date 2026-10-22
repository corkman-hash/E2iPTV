# =============================================================================
# E2iPTV
# Recorder
# =============================================================================

from ..paths import LOG  # data folder / debug logs
import time

from .status import RecordingState
from .recording import RecordingMode


class Recorder(object):

    def __init__(self, manager):

        self.manager = manager

        self._active = {}

    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------

    def start(
        self,
        recording,
        session,
        provider,
        channel,
        event,
        recording_path
    ):

        if recording.recording_id in self._active:
            return False

        recording.actual_start = int(time.time())

        recording.state = RecordingState.STARTING

        self.manager.recording_store.add(recording)

        self._active[
            recording.recording_id
        ] = recording

        self._log(
            "START  %s  %s" % (
                recording.channel_name,
                recording.recording_id
            )
        )

        try:

            if recording.mode == RecordingMode.FUTURE:

                from ..player import record_future

                # Record the scheduler's full window - the start and end
                # with the recording's offsets already added - not just
                # the guide programme's own times (which ignored the
                # offsets). Also works when the programme is no longer in
                # the guide (event None), which used to fail outright.
                ev = event
                if recording.scheduled_start and recording.scheduled_end:
                    from ..backend.epg import Epg
                    ev = Epg()
                    ev.stream_id = getattr(channel, "stream_id", "")
                    ev.start = int(recording.scheduled_start)
                    ev.end = int(recording.scheduled_end)
                    ev.title = (getattr(event, "title", "")
                                or recording.event_title or channel.name)
                    ev.description = getattr(event, "description", "") or ""

                recording.rec_key = record_future(
                    session,
                    provider,
                    channel,
                    ev,
                    recording_path,
                    0,
                    0
                )

            else:

                raise NotImplementedError(
                    "Recording mode %s not implemented"
                    % recording.mode
                )

            recording.state = RecordingState.RECORDING

            return True

        except Exception as e:

            recording.state = RecordingState.FAILED

            recording.reason = str(e)

            self._log(
                "FAILED %s %s" % (
                    recording.channel_name,
                    e
                )
            )

            return False

    # -------------------------------------------------------------------------

    def stop(self, recording):

        if recording.recording_id not in self._active:
            return False

        try:

            if recording.rec_key:

                from ..player import stop_recording

                stop_recording(
                    recording.rec_key
                )

        except Exception:

            pass

        recording.actual_end = int(time.time())

        recording.state = RecordingState.COMPLETED

        self.manager.recording_store.remove(
            recording
        )

        self._log(
            "STOP   %s  %s" % (
                recording.channel_name,
                recording.recording_id
            )
        )

        del self._active[
            recording.recording_id
        ]

        return True

    # -------------------------------------------------------------------------

    def active(self):

        return list(
            self._active.values()
        )

    # -------------------------------------------------------------------------

    def is_recording(self, recording_id):

        return (
            recording_id in self._active
        )

    # -------------------------------------------------------------------------

    def _log(self, text):

        try:

            with open(
                LOG("e2iptv_recording.log"),
                "a"
            ) as f:

                f.write(
                    "%d %s\n" % (
                        int(time.time()),
                        text
                    )
                )

        except Exception:
            pass

