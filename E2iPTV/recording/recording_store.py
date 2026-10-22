# =============================================================================
# E2iPTV
# Recording Store
# =============================================================================

from .recording import Recording
from .status import RecordingState


class RecordingStore(object):

    def __init__(self):

        self.clear()

    # -------------------------------------------------------------------------

    def clear(self):

        self._recordings = []

    # -------------------------------------------------------------------------

    def add(self, recording):

        self._recordings.append(recording)

    # -------------------------------------------------------------------------

    def remove(self, recording):

        if recording in self._recordings:
            self._recordings.remove(recording)

    # -------------------------------------------------------------------------

    def all(self):

        return self._recordings

    # -------------------------------------------------------------------------

    def count(self):

        return len(self._recordings)

    # -------------------------------------------------------------------------

    def get(self, recording_id):

        for recording in self._recordings:

            if recording.recording_id == recording_id:
                return recording

        return None

    # -------------------------------------------------------------------------

    def get_by_rec_key(self, rec_key):

        for recording in self._recordings:

            if recording.rec_key == rec_key:
                return recording

        return None

    # -------------------------------------------------------------------------

    def active(self):

        return [
            recording
            for recording in self._recordings
            if recording.state in (
                RecordingState.SCHEDULED,
                RecordingState.WAITING,
                RecordingState.STARTING,
                RecordingState.RECORDING,
            )
        ]

    # -------------------------------------------------------------------------

    def finished(self):

        return [
            recording
            for recording in self._recordings
            if recording.state in (
                RecordingState.COMPLETED,
                RecordingState.FAILED,
                RecordingState.CANCELLED,
                RecordingState.MISSED,
                RecordingState.ABORTED,
            )
        ]
