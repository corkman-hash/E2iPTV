# =============================================================================
# E2iPTV Backend — EPG Reader
# Uses start/end time strings from Xtream API parsed as local time.
# This avoids UTC/local ambiguity in start_timestamp across providers.
# The per-provider EPG offset is applied when the guide is read
# (epg_store.py), so times are stored exactly as the provider sent them.
# =============================================================================

import base64
import time
import calendar

from .epg import Epg
from .xtream_client import XtreamClient
from .stalker_client import StalkerClient


class EpgReader(object):

    def _parse_time(self, item, key_str, key_ts):
        """
        Parse EPG time from the string field (local time) preferring
        the timestamp field as fallback. Using the string avoids
        providers that store timestamps in local vs UTC inconsistently.
        """
        s = item.get(key_str, "")
        if s:
            try:
                struct = time.strptime(s, "%Y-%m-%d %H:%M:%S")
                # mktime treats struct as LOCAL time — gives correct UTC epoch
                return int(time.mktime(struct))
            except Exception:
                pass
        # Fallback to raw timestamp
        try:
            return int(item.get(key_ts, 0))
        except Exception:
            return 0

    def read(self, source, channel):

        if source.source_type == "STALKER":
            return self._read_stalker(source, channel)

        client = XtreamClient(source)
        manual_offset = 0  # EPG offset is applied when read — see epg_store.py

        data = client.call(
            "get_simple_data_table",
            stream_id=channel.stream_id
        )

        entries = []

        for item in data.get("epg_listings", []):

            e = Epg()
            e.stream_id = channel.stream_id

            try:
                e.title = base64.b64decode(
                    item.get("title", "")
                ).decode("utf-8").strip()
            except Exception:
                e.title = ""

            try:
                e.description = base64.b64decode(
                    item.get("description", "")
                ).decode("utf-8").strip()
            except Exception:
                e.description = ""

            e.start = self._parse_time(item, "start", "start_timestamp") + manual_offset
            e.end   = self._parse_time(item, "end",   "stop_timestamp")  + manual_offset
            entries.append(e)

        # Fix missing end times — use next entry's start
        listings = data.get("epg_listings", [])
        for i, e in enumerate(entries):
            if not e.end or e.end <= e.start:
                if i + 1 < len(entries):
                    e.end = entries[i + 1].start
                else:
                    e.end = e.start + 3600

        return sorted([e for e in entries if e.start and e.end and e.start < e.end],
                      key=lambda x: x.start)

    def _build_stalker_entries(self, source, stream_id, items):
        """The parsing half of _read_stalker, split out so a bulk
        harvest (one API response covering hundreds of channels,
        parsed many times over) or a fast in-process per-channel
        fetch can reuse the exact same title/time handling as the
        normal per-channel path, rather than a second, separately
        -maintained copy of it drifting out of sync."""
        manual_offset = 0  # EPG offset is applied when read — see epg_store.py

        entries = []

        for item in items:

            e = Epg()
            e.stream_id = stream_id

            # Stalker EPG text fields come back as plain text, not
            # base64 like Xtream's — no decode step needed here.
            e.title = (item.get("name", "") or "").strip()
            e.description = (
                item.get("descr", "") or item.get("description", "")
            ).strip()

            e.start = self._parse_time(item, "time", "start_timestamp") + manual_offset
            e.end   = self._parse_time(item, "time_to", "stop_timestamp") + manual_offset
            entries.append(e)

        # Fix missing end times — use next entry's start
        for i, e in enumerate(entries):
            if not e.end or e.end <= e.start:
                if i + 1 < len(entries):
                    e.end = entries[i + 1].start
                else:
                    e.end = e.start + 3600

        return sorted([e for e in entries if e.start and e.end and e.start < e.end],
                      key=lambda x: x.start)

    def _read_stalker(self, source, channel):
        items = StalkerClient(source).get_epg(channel.stream_id)
        return self._build_stalker_entries(source, channel.stream_id, items)
