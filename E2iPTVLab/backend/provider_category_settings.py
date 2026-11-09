# E2iPTV
# Stable provider-keyed category settings
# Separate Live TV / Movies / Series category settings

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import json
import os

CACHE_DIR = DATA + "/cache"
PATH = os.path.join(CACHE_DIR, "disabled_categories.json")


def _provider_key(provider):
    key = str(getattr(provider, "provider_key", "") or "").strip()
    if key:
        return key
    return ""


def _legacy_provider_key(provider):
    return str(getattr(provider, "name", "") or "").strip().casefold()


def _load_all():
    try:
        with open(PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_all(data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = PATH + ".tmp"

        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, PATH)

    except Exception:
        pass


def _normalise_type(category_type):
    value = str(category_type or "live").strip().casefold()

    if value in ("movie", "movies"):
        return "movies"

    if value in ("series", "serieses"):
        return "series"

    return "live"


def _read_provider_data(data, key, legacy_key=None):
    if key in data:
        return data[key], key

    if legacy_key and legacy_key in data:
        return data[legacy_key], legacy_key

    return None, None


def load(provider, category_type="live"):
    key = _provider_key(provider)

    if not key:
        return set()

    category_type = _normalise_type(category_type)
    data = _load_all()

    provider_data, source_key = _read_provider_data(
        data,
        key,
        _legacy_provider_key(provider)
    )

    if provider_data is None:
        return set()

    # Old format:
    # provider_key -> [category, category, ...]
    #
    # These are existing Live TV settings, so preserve them.
    if isinstance(provider_data, list):
        if category_type == "live":
            return {str(x) for x in provider_data}
        return set()

    # New format:
    # provider_key -> {
    #     "live": [...],
    #     "movies": [...],
    #     "series": [...]
    # }
    if isinstance(provider_data, dict):
        try:
            values = provider_data.get(category_type, [])
            if isinstance(values, list):
                return {str(x) for x in values}
        except Exception:
            pass

    return set()


def save(provider, categories, category_type="live"):
    key = _provider_key(provider)

    if not key:
        return

    category_type = _normalise_type(category_type)
    data = _load_all()

    existing = data.get(key)

    # Convert the old Live TV list format to the new format,
    # preserving the existing Live TV settings.
    if isinstance(existing, list):
        existing = {
            "live": sorted({str(x) for x in existing})
        }

    if not isinstance(existing, dict):
        existing = {}

    existing[category_type] = sorted({str(x) for x in categories})

    data[key] = existing

    _save_all(data)


def remove(provider, category_type=None):
    key = _provider_key(provider)

    if not key:
        return

    data = _load_all()

    if key not in data:
        return

    if category_type is None:
        del data[key]
        _save_all(data)
        return

    category_type = _normalise_type(category_type)
    existing = data.get(key)

    if isinstance(existing, list):
        if category_type == "live":
            del data[key]
            _save_all(data)
        return

    if isinstance(existing, dict):
        existing.pop(category_type, None)

        if existing:
            data[key] = existing
        else:
            del data[key]

        _save_all(data)
