#!/usr/bin/env python3
# =============================================================================
# E2iPTV — guide matching report (changes nothing)
#
#   python3 epg_match_report.py "<provider name>" "<xmltv file .xml/.xml.gz>"
#
# For each Live TV channel of that provider (from the plugin's saved
# channel lists): is its own guide full, patchy or empty for the next 24
# hours - and if it isn't full, could the guide come from
#   - the same channel at one of your other providers, or
#   - the XMLTV file (e.g. epgshare01's epg_ripper_UK1.xml.gz)?
# Channels are matched by the guide id the provider sends, or by name with
# the clutter removed ("UK: BBC ONE FHD" = "BBC One HD"). A name that fits
# more than one different channel counts as unclear, never as a match.
#
# Prints a summary; the full list goes to /tmp/epg_match_report.txt.
# Read-only: no guides or settings are changed.
# =============================================================================

import glob
import gzip
import importlib
import json
import os
import re
import sys
import time
import types
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
REPORT = "/tmp/epg_match_report.txt"
HOURS = 24


# -- the plugin's own data ------------------------------------------------------

def _plugin_paths():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(PLUGIN))))
    from Plugins.Extensions.E2iPTVLab.paths import DATA
    return DATA


def _providers(data_dir):
    """[(name, provider)] from playlists.txt, without loading the rest of
    the plugin."""
    pkg = types.ModuleType("e2b")
    pkg.__path__ = [os.path.join(PLUGIN, "backend")]
    sys.modules["e2b"] = pkg
    reader = importlib.import_module("e2b.playlist_reader")
    path = "/etc/enigma2/E2iPTVLab/playlists.txt"
    try:
        with open(os.path.join(data_dir, "cache", "app_settings.json")) as f:
            path = json.load(f).get("playlists_path") or path
    except Exception:
        pass
    out = []
    for item in reader.load_providers(path) or []:
        p = item[1] if isinstance(item, tuple) else item
        if getattr(p, "provider_key", ""):
            out.append((getattr(p, "name", "?"), p))
    return out


def _channels_by_provider(data_dir):
    """{provider_key: {stream_id: channel dict}} from the saved lists."""
    out = {}
    for path in glob.glob(os.path.join(data_dir, "cache", "ch_*.json")):
        try:
            with open(path) as f:
                rows = json.load(f)
        except Exception:
            continue
        for r in rows or []:
            if not isinstance(r, dict) or not str(r.get("name", "")).strip():
                continue
            key = str(r.get("provider_key") or "")
            if key:
                out.setdefault(key, {})[str(r.get("stream_id"))] = r
    return out


def _own_guide(data_dir, key, stream_id, now, until):
    """(hours covered in now..until, covers now?) from the saved guide."""
    path = os.path.join(data_dir, "epg", "%s_%s.json" % (key, stream_id))
    try:
        with open(path) as f:
            rows = json.load(f)
    except Exception:
        return 0.0, False
    spans = []
    for r in rows or []:
        try:
            a, b = int(r.get("start", 0)), int(r.get("end", 0))
        except Exception:
            continue
        a, b = max(a, now), min(b, until)
        if b > a:
            spans.append((a, b))
    spans.sort()
    covered, last = 0, now
    for a, b in spans:
        a = max(a, last)
        if b > a:
            covered += b - a
            last = b
    on_now = any(a <= now < b for a, b in spans)
    return covered / 3600.0, on_now


# -- matching -----------------------------------------------------------------

_PREFIX = re.compile(r"^\s*(\[[^\]]*\]|\|[^|]*\||[a-z]{2,3}\s*[:|\-]|uk\b|gb\b)\s*", re.I)
_DROP = {"hd", "fhd", "uhd", "sd", "4k", "hevc", "h265", "h264", "50fps", "60fps",
         "1080p", "720p", "1080", "720", "backup", "vip", "raw", "multi", "audio",
         "uk", "gb", "eng", "en", "tv", "channel"}
_NUM = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5"}


def norm_name(name):
    s = str(name or "").lower()
    for _i in range(3):                          # "UK | [VIP] BBC One"
        s2 = _PREFIX.sub("", s)
        if s2 == s:
            break
        s = s2
    s = s.replace("+1", " plus1 ").replace("+ 1", " plus1 ").replace("&", " and ")
    s = re.sub(r"[\(\)\[\]\|:.,_\-/']", " ", s)
    words = [_NUM.get(w, w) for w in s.split()]
    words = [w for w in words if w not in _DROP]
    return "".join(words)


def norm_id(xid):
    """'BBC.One.HD.uk' / 'bbcone.uk' -> 'bbc1'."""
    s = str(xid or "").lower()
    s = re.sub(r"\.(uk|gb|ie|com|tv)$", "", s)
    return norm_name(s.replace(".", " "))


def read_xmltv(path, now, until):
    """{xml id: (names, programmes in now..until)} - read in stages so a
    big file doesn't fill the box's memory."""
    opener = gzip.open if path.endswith(".gz") else open
    chans, progs = {}, {}

    import calendar

    def ts(v):
        """XMLTV time '20261013180000 +0100' -> seconds."""
        try:
            v = v.strip()
            secs = calendar.timegm(time.strptime(v[:14], "%Y%m%d%H%M%S"))
            m = re.search(r"([+-])(\d\d)(\d\d)", v[14:])
            if m:
                off = int(m.group(2)) * 3600 + int(m.group(3)) * 60
                secs -= off if m.group(1) == "+" else -off
            return secs
        except Exception:
            return 0

    with opener(path, "rb") as f:
        for _ev, el in ET.iterparse(f, events=("end",)):
            if el.tag == "channel":
                cid = el.get("id") or ""
                names = [d.text or "" for d in el.findall("display-name")]
                chans[cid] = names
                el.clear()
            elif el.tag == "programme":
                a, b = ts(el.get("start", "")), ts(el.get("stop", ""))
                if b > now and a < until:
                    cid = el.get("channel") or ""
                    progs[cid] = progs.get(cid, 0) + 1
                el.clear()
    return dict((cid, (names, progs.get(cid, 0))) for cid, names in chans.items())


def build_index(items):
    """{normalised key: set(of source ids)} for name/id lookups."""
    idx = {}
    for sid, keys in items:
        for k in keys:
            if k:
                idx.setdefault(k, set()).add(sid)
    return idx


def main(want, xml_path):
    data_dir = _plugin_paths()
    now = int(time.time())
    until = now + HOURS * 3600
    provs = _providers(data_dir)
    target = [(n, p) for n, p in provs if want.lower() in n.lower()]
    if not target:
        print("No provider called '%s'. Providers: %s" % (want, ", ".join(n for n, _p in provs)))
        return 1
    tname, tprov = target[0]
    tkey = tprov.provider_key
    names = dict((p.provider_key, n) for n, p in provs)
    chans = _channels_by_provider(data_dir)
    mine = chans.get(tkey, {})
    if not mine:
        print("No saved channel lists for %s yet - open some of its categories first." % tname)
        return 1
    print("Provider: %s - %d channels in its saved lists" % (tname, len(mine)))

    # other providers' channels that have a guide covering now
    other_items = []
    for key, rows in chans.items():
        if key == tkey or key not in names:
            continue
        for sid, r in rows.items():
            hrs, on_now = _own_guide(data_dir, key, sid, now, until)
            if hrs >= 1:
                ref = (key, sid, r.get("name", ""), hrs)
                other_items.append((ref, [norm_name(r.get("name")),
                                          norm_id(r.get("epg_channel_id"))]))
    other_idx = build_index(other_items)

    xml = {}
    if xml_path:
        if not os.path.exists(xml_path):
            print("XMLTV file not found: %s" % xml_path)
        else:
            t0 = time.time()
            print("Reading %s ..." % xml_path)
            xml = read_xmltv(xml_path, now, until)
            with_data = sum(1 for v in xml.values() if v[1])
            print("  %d channels in it, %d with programmes in the next %d h (%.0f s)"
                  % (len(xml), with_data, HOURS, time.time() - t0))
    xml_idx = build_index([(cid, [norm_id(cid)] + [norm_name(n) for n in v[0]])
                           for cid, v in xml.items() if v[1]])

    rows = []
    tally = {"full": 0, "gaps": 0, "none": 0, "fill_other": 0, "fill_xml": 0,
             "unclear": 0, "nomatch": 0}
    for sid, r in sorted(mine.items(), key=lambda kv: str(kv[1].get("name", "")).lower()):
        hrs, on_now = _own_guide(data_dir, tkey, sid, now, until)
        state = "full" if hrs >= HOURS * 0.9 else ("gaps" if hrs > 0 else "none")
        tally[state] += 1
        name = r.get("name", "")
        if state == "full":
            rows.append("%-40s own guide OK (%.0f h)" % (name[:40], hrs))
            continue
        keys = [k for k in (norm_id(r.get("epg_channel_id")), norm_name(name)) if k]
        found_o = set()
        found_x = set()
        for k in keys:
            found_o |= other_idx.get(k, set())
            found_x |= xml_idx.get(k, set())
        # other providers: several copies of the same channel are fine,
        # but they must all normalise to the same name
        o_names = set(norm_name(ref[2]) for ref in found_o)
        own = "own %s" % ("%.0f h" % hrs if hrs else "none")
        if found_o and len(o_names) == 1:
            best = max(found_o, key=lambda ref: ref[3])
            tally["fill_other"] += 1
            rows.append("%-40s %-9s -> %s: %s (%.0f h)" % (
                name[:40], own, names.get(best[0], "?"), best[2], best[3]))
        elif found_x and len(set(norm_id(c) for c in found_x)) == 1:
            # one channel, perhaps listed twice (SD and HD): the fuller one
            cid = max(found_x, key=lambda c: xml[c][1])
            tally["fill_xml"] += 1
            rows.append("%-40s %-9s -> XMLTV: %s (%d programmes)" % (
                name[:40], own, cid, xml[cid][1]))
        elif found_o or found_x:
            tally["unclear"] += 1
            cands = sorted(set([ref[2] for ref in found_o] + list(found_x)))[:4]
            rows.append("%-40s %-9s ?? unclear: %s" % (name[:40], own, " / ".join(cands)))
        else:
            tally["nomatch"] += 1
            rows.append("%-40s %-9s -- no match" % (name[:40], own))

    lines = [
        "Guide matching report for %s  (%s)" % (tname, time.strftime("%d %b %Y %H:%M")),
        "Next %d hours. Nothing has been changed." % HOURS,
        "",
        "Own guide full:        %d" % tally["full"],
        "Own guide with gaps:   %d" % tally["gaps"],
        "No own guide:          %d" % tally["none"],
        "",
        "Of the %d with gaps or none:" % (tally["gaps"] + tally["none"]),
        "  could fill from another provider: %d" % tally["fill_other"],
        "  could fill from the XMLTV file:   %d" % tally["fill_xml"],
        "  unclear (more than one match):    %d" % tally["unclear"],
        "  no match anywhere:                %d" % tally["nomatch"],
        "",
    ]
    with open(REPORT, "w") as f:
        f.write("\n".join(lines + rows) + "\n")
    print("")
    print("\n".join(lines))
    print("Full list, channel by channel: %s" % REPORT)
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print('Usage: python3 %s "<provider name>" ["<xmltv file>"]' % os.path.abspath(__file__))
        sys.exit(1)
    sys.exit(main(args[0], args[1] if len(args) > 1 else ""))
