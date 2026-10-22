# =============================================================================
# IE2iPTV Backend
# Series Info Store
# =============================================================================

from ..paths import HDD  # data folder / debug logs
from ..paths import DATA  # E2iPTV_Data folder
import json
import os
import threading

from .store_base import StoreBase
from .series_info_reader import SeriesInfoReader
from .series_info import SeriesInfo
from .season import Season
from .episode import Episode

# Series details (seasons/episodes) saved to disk, so a series opened
# before opens instantly — even after a restart — instead of waiting on
# the provider every new session. The saved copy is shown straight away
# and quietly refreshed in the background, so new episodes still appear
# (from the next time the series is opened).
SERIES_INFO_DIR = DATA + "/cache/series_info"


def _file_for(provider, series):
    key = "%s_%s" % (
        getattr(provider, "provider_key", "") or getattr(series, "source_id", ""),
        getattr(series, "series_id", ""),
    )
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(key))
    return os.path.join(SERIES_INFO_DIR, safe + ".json")


def _save(provider, series, info):
    try:
        data = dict(info.__dict__)
        data["seasons"] = [dict(s.__dict__) for s in (info.seasons or [])]
        data["episodes"] = [dict(e.__dict__) for e in (info.episodes or [])]
        if not os.path.isdir(SERIES_INFO_DIR):
            os.makedirs(SERIES_INFO_DIR)
        path = _file_for(provider, series)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, default=str)
        os.rename(tmp, path)
    except Exception as e:
        print("[E2iPTV] series info cache save failed:", e)


def _load(provider, series):
    try:
        with open(_file_for(provider, series)) as f:
            data = json.load(f)
    except Exception:
        return None
    try:
        info = SeriesInfo()
        seasons = data.pop("seasons", []) or []
        episodes = data.pop("episodes", []) or []
        info.__dict__.update(data)
        info.seasons = []
        for d in seasons:
            s = Season()
            s.__dict__.update(d)
            info.seasons.append(s)
        info.episodes = []
        for d in episodes:
            e = Episode()
            e.__dict__.update(d)
            info.episodes.append(e)
        return info
    except Exception:
        return None


def _has_content(info):
    return info is not None and bool(getattr(info, "episodes", None))


class SeriesInfoStore(StoreBase):

    def __init__(self):

        super(SeriesInfoStore, self).__init__()

        self.reader = SeriesInfoReader()

    # -------------------------------------------------------------

    def load(self, provider, series):

        key = "%s_%s" % (series.source_id, series.series_id)

        # Saved copy first: instant, then refreshed in the background.
        cached = _load(provider, series)
        if _has_content(cached):
            self.put(key, [cached])

            def refresh():
                try:
                    fresh = self.reader.read(provider, series)
                except Exception:
                    return
                if _has_content(fresh):
                    _save(provider, series, fresh)
                    self.put(key, [fresh])

            t = threading.Thread(target=refresh, name="E2iPTV-SeriesInfo-Refresh")
            t.daemon = True
            t.start()
            return cached

        info = self.reader.read(provider, series)
        if _has_content(info):
            _save(provider, series, info)

        # Composite key — series_id alone isn't unique across
        # providers (same reasoning as the category_id fix in
        # MovieStore/SeriesStore/get_live_channels), so without
        # source_id here, opening a series on one provider then
        # switching to another provider and opening a *different*
        # series that happens to share a series_id would silently
        # return the first provider's cached episodes/seasons instead
        # of fetching this series' actual detail — showing the
        # correct title (read straight from the current provider's own
        # series list) but nothing else, since the mismatched cached
        # info wouldn't have any seasons matching what's expected.
        key = "%s_%s" % (series.source_id, series.series_id)

        self.put(key, [info])

        return info
