# =============================================================================
# E2iPTV
# Recording Subsystem
# Status Definitions
# =============================================================================


class RuleType(object):

    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    WEEKDAYS = "weekdays"
    WEEKENDS = "weekends"
    CUSTOM = "custom"


class RuleState(object):

    ENABLED = "enabled"
    DISABLED = "disabled"


class RecordingState(object):

    SCHEDULED = "scheduled"
    WAITING = "waiting"
    STARTING = "starting"
    RECORDING = "recording"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MISSED = "missed"
    ABORTED = "aborted"


class FailureReason(object):

    NONE = ""

    USER_CANCELLED = "user_cancelled"

    STREAM_UNAVAILABLE = "stream_unavailable"
    STREAM_TIMEOUT = "stream_timeout"
    STREAM_ENDED = "stream_ended"

    FILE_OPEN_FAILED = "file_open_failed"
    WRITE_ERROR = "write_error"
    DISK_FULL = "disk_full"

    SERVICE_LOST = "service_lost"

    EXCEPTION = "exception"

    UNKNOWN = "unknown"


class TriggerType(object):

    MANUAL = "manual"
    EPG = "epg"
    REPEAT = "repeat"
    NOW = "now"          # "Record from Now" — the only trigger type
                          # eligible for the "Extend +30 min" menu option
                          # (see get_ctx_items/execute_ctx_action in
                          # player.py), since it's the only kind with no
                          # real EPG end time to trust in the first place.


class Weekday(object):

    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6