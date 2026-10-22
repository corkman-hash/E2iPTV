# =============================================================================
# E2iPTV
# Recording Rule Reader
# =============================================================================

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import json
import os

from .rule import RecordingRule


RULES_FILE = DATA + "/recordings/rules.json"


class RecordingRuleReader(object):

    def read(self):

        rules = []

        if not os.path.exists(RULES_FILE):
            return rules

        try:
            with open(RULES_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            return rules

        if not isinstance(data, list):
            return rules

        for item in data:

            try:

                rule = RecordingRule()

                for key, value in item.items():

                    if hasattr(rule, key):
                        setattr(rule, key, value)

                rules.append(rule)

            except Exception:
                pass

        return rules