# =============================================================================
# E2iPTV
# Recording Rule Writer
# =============================================================================

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import json
import os


RULES_FILE = DATA + "/recordings/rules.json"


class RecordingRuleWriter(object):

    def write(self, rules):

        directory = os.path.dirname(RULES_FILE)

        if not os.path.exists(directory):
            os.makedirs(directory)

        data = []

        for rule in rules:

            data.append(
                dict(rule.__dict__)
            )

        try:

            with open(RULES_FILE, "w") as f:

                json.dump(
                    data,
                    f,
                    indent=4,
                    sort_keys=True
                )

            return True

        except Exception:

            return False