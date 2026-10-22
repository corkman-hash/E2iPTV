# =============================================================================
# E2iPTV
# Recording Subsystem
# ID Generation
# =============================================================================

from datetime import datetime
import uuid


class RecordingIds(object):
    """
    Centralised ID generation for the recording subsystem.

    RULE IDs
        RULExxxxxxxx

    Recording IDs
        RIDxxxxxxxx

    Session IDs
        SIDxxxxxxxx
    """

    @staticmethod
    def new_rule_id():

        return "RULE%s" % uuid.uuid4().hex[:8].upper()

    @staticmethod
    def new_recording_id():

        return "RID%s" % uuid.uuid4().hex[:8].upper()

    @staticmethod
    def new_session_id():

        return "SID%s" % uuid.uuid4().hex[:8].upper()

    @staticmethod
    def timestamp():

        return datetime.utcnow().isoformat()
