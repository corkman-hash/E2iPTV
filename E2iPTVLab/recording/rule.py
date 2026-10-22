# =============================================================================
# E2iPTV
# Recording Rule
# =============================================================================

import uuid

from .status import (
    RuleType,
    RuleState,
    TriggerType,
)


class RecordingRule(object):

    def __init__(self):

        # -----------------------------------------------------------------
        # Identity
        # -----------------------------------------------------------------

        self.rule_id = str(uuid.uuid4())

        self.name = ""

        # -----------------------------------------------------------------
        # State
        # -----------------------------------------------------------------

        self.enabled = True
        self.state = RuleState.ENABLED

        # -----------------------------------------------------------------
        # Schedule
        # -----------------------------------------------------------------

        self.rule_type = RuleType.ONCE

        # For DAILY / WEEKLY / CUSTOM

        self.days = []

        # HH:MM

        self.start_time = "00:00"

        # minutes

        self.duration = 60

        # -----------------------------------------------------------------
        # Source
        # -----------------------------------------------------------------

        self.provider_id = 0

        # The provider's own identity (provider.provider_key). provider_id
        # is only its position in playlists.txt, which changes when the
        # file is reordered - see RecordingManager.sync_rule_providers().
        self.provider_key = ""

        self.category_id = ""

        self.stream_id = ""

        self.channel_name = ""

        # -----------------------------------------------------------------
        # Recording
        # -----------------------------------------------------------------

        self.record_path = ""

        self.filename_template = ""

        self.pre_padding = 0

        self.post_padding = 0

        # -----------------------------------------------------------------
        # Trigger
        # -----------------------------------------------------------------

        self.trigger = TriggerType.MANUAL

        # -----------------------------------------------------------------
        # EPG linkage
        # -----------------------------------------------------------------

        self.event_id = ""

        self.event_title = ""

        self.event_start = 0

        self.event_end = 0

        # -----------------------------------------------------------------
        # Runtime
        # -----------------------------------------------------------------

        self.last_run = 0

        self.next_run = 0

        self.last_result = ""

        # -----------------------------------------------------------------
        # User
        # -----------------------------------------------------------------

        self.notes = ""