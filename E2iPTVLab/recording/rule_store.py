# =============================================================================
# E2iPTV
# Recording Rule Store
# =============================================================================

from .rule import RecordingRule


class RecordingRuleStore(object):

    def __init__(self):

        self.clear()

    # -------------------------------------------------------------------------

    def clear(self):

        self._rules = []

    # -------------------------------------------------------------------------

    def add(self, rule):

        self._rules.append(rule)

    # -------------------------------------------------------------------------

    def remove(self, rule):

        if rule in self._rules:
            self._rules.remove(rule)

    # -------------------------------------------------------------------------

    def all(self):

        return self._rules

    # -------------------------------------------------------------------------

    def count(self):

        return len(self._rules)

    # -------------------------------------------------------------------------

    def get(self, rule_id):

        for rule in self._rules:

            if rule.rule_id == rule_id:
                return rule

        return None

    # -------------------------------------------------------------------------

    def enabled(self):

        return [
            rule
            for rule in self._rules
            if rule.enabled
        ]