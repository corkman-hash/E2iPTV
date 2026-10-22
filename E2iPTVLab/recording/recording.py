# =============================================================================
# E2iPTV
# Recording Instance
# =============================================================================

import uuid

from .status import RecordingState

class RecordingMode(object):

    LIVE_NOW = "live_now"

    LIVE_TO_END = "live_to_end"

    FUTURE = "future"

    CATCHUP = "catchup"

class Recording(object):

    def __init__(self):

        # -----------------------------------------------------------------
        # Identity
        # -----------------------------------------------------------------

        self.recording_id = str(uuid.uuid4())

        self.rule_id = ""

        # -----------------------------------------------------------------
        # Source
        # -----------------------------------------------------------------

        self.provider_id = 0

        self.stream_id = ""

        self.channel_name = ""

        self.event_title = ""

        # -----------------------------------------------------------------
        # Timing
        # -----------------------------------------------------------------

        self.scheduled_start = 0

        self.scheduled_end = 0

        self.actual_start = 0

        self.actual_end = 0

        # -----------------------------------------------------------------
        # Recording Mode
        # -----------------------------------------------------------------

        self.mode = RecordingMode.FUTURE

        # -----------------------------------------------------------------
        # Recording
        # -----------------------------------------------------------------

        self.filename = ""

        self.full_path = ""

        self.filesize = 0

        # -----------------------------------------------------------------
        # Runtime
        # -----------------------------------------------------------------

        self.state = RecordingState.SCHEDULED


        # Recording engine handle
        self.rec_key = None

        self.pid = 0

        self.exit_code = 0

        # -----------------------------------------------------------------
        # Diagnostics
        # -----------------------------------------------------------------

        self.reason = ""

        self.error = ""

        self.logfile = ""

        # -----------------------------------------------------------------
        # Statistics
        # -----------------------------------------------------------------

        self.bytes_written = 0

        self.bitrate = 0