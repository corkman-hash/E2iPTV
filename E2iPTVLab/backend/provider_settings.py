# =============================================================================
# IE2iPTV Backend
# Provider Settings
# =============================================================================

import json
import os

SETTINGS_FILE = "/etc/enigma2/e2iptvlab_provider_settings.json"


class ProviderSettings(object):

    def __init__(self):
        self._data = {}
        self.load()

    def load(self):

        self._data = {}

        if not os.path.exists(SETTINGS_FILE):
            return

        try:
            with open(SETTINGS_FILE, "r") as f:
                self._data = json.load(f)
        except Exception:
            self._data = {}

    def save(self):

        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self._data, f, indent=4, sort_keys=True)
        except Exception:
            pass

    def get_epg_offset(self, provider):

        key = str(getattr(provider, "provider_key", "") or "")

        if key and key in self._data:
            return int(
                self._data.get(key, {}).get("epg_offset", 0)
            )

        # Legacy numeric provider_id fallback - only for a provider with no
        # key (the number is its position, which changes on a reorder).
        if key:
            return 0
        legacy_id = getattr(provider, "provider_id", 0)

        return int(
            self._data.get(
                str(legacy_id),
                {}
            ).get(
                "epg_offset",
                0
            )
        )

    def set_epg_offset(self, provider, value):

        value = max(-12, min(12, int(value)))

        key = str(getattr(provider, "provider_key", "") or "")

        if not key:
            key = str(getattr(provider, "provider_id", 0))

        if key not in self._data:
            self._data[key] = {}

        self._data[key]["epg_offset"] = value

        self.save()
