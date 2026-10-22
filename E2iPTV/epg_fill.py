#!/usr/bin/env python3
# =============================================================================
# E2iPTV — fill guide gaps (Settings -> EPG -> Fill Guide Gaps)
#
# A channel whose own guide is empty or patchy for the next 24 hours gets
# a stand-in guide from
#   1. the same channel at another of your providers (whose guide covers
#      it), or
#   2. an XMLTV guide file - e.g. epgshare01's epg_ripper_UK1.xml.gz -
#      downloaded once a day, or a file already on the box.
# Channels are matched by the guide id the provider sends, else by the name
# with the clutter removed ("UK: BBC ONE FHD" = "BBC One HD" = BBC.One.HD.uk).
# If a name could be two different channels, the channel is left alone.
#
# The stand-in guides are kept apart from the providers' own, in
# E2iPTV_Data/epg/fill/<provider key>_<stream>.json (+ index.json), so
# nothing a provider sends is ever replaced: the screens put the two
# together when reading (backend/epg_store.py), the provider's own
# programmes always winning, the stand-in only filling the empty times.
#
# Runs after the nightly guide refresh (bulk_epg_fetch.py) and on demand:
#   python3 epg_fill.py            (Settings -> EPG -> Fill Gaps Now)
# Logs to E2iPTV_Data/epg/epg_fill.log ("Guide gap filling").
# =============================================================================

import calendar
import glob
import gzip
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

PLUGIN_ROOT = "/usr/lib/enigma2/python/Plugins/Extensions"
PYTHON_ROOT = "/usr/lib/enigma2/python"
for _p in (PLUGIN_ROOT, PYTHON_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from E2iPTV.paths import DATA
except Exception:                       # imported as part of the plugin
    from .paths import DATA

FILL_DIR = os.path.join(DATA, "epg", "fill")
INDEX = os.path.join(FILL_DIR, "index.json")
LOG_PATH = os.path.join(DATA, "epg", "epg_fill.log")
SETTINGS = os.path.join(DATA, "cache", "app_settings.json")

CHECK_HOURS = 24            # a guide covering 90% of this is "full"
KEEP_BEFORE = 24 * 3600     # stand-in programmes kept from a day ago ...
KEEP_AFTER = 4 * 86400      # ... to 4 days ahead
DOWNLOAD_EVERY = 20 * 3600  # a downloaded guide file is renewed after this

EPGSHARE_UK = "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz"
EPGSHARE_IE = "https://epgshare01.online/epgshare01/epg_ripper_IE1.xml.gz"
EPGSHARE_US = "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"

# Your own choices (a channel's menu -> Guide from...), kept for good:
#   {"<provider key>_<stream id>": {"type": "other", "key": .., "sid": ..,
#                                   "label": .., "name": <channel name>}
#                                 | {"type": "xml", "id": <guide file id>, ..}
#                                 | {"type": "none", ..}}
# A manual choice REPLACES the provider's own guide (you chose it); the
# automatic ones only fill its gaps. Found again by the channel's name if
# the provider renumbers its channels.
MAPPINGS = os.path.join(DATA, "cache", "epg_mappings.json")
RUNNING = os.path.join(FILL_DIR, "running.pid")    # while a fill is going


def is_running():
    """True while a fill is going (whoever started it: Settings, the
    nightly refresh, Guide from...)."""
    try:
        with open(RUNNING) as f:
            pid = int(f.read().strip() or 0)
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _mark_running(on):
    try:
        if on:
            os.makedirs(FILL_DIR, exist_ok=True)
            with open(RUNNING, "w") as f:
                f.write(str(os.getpid()))
        else:
            os.remove(RUNNING)
    except Exception:
        pass
XML_CHANNELS = os.path.join(FILL_DIR, "xml_channels.json")


def load_mappings():
    try:
        with open(MAPPINGS) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_mapping(key, sid, mapping):
    """mapping None = back to automatic."""
    d = load_mappings()
    name = "%s_%s" % (key, sid)
    if mapping is None:
        d.pop(name, None)
    else:
        d[name] = mapping
    os.makedirs(os.path.dirname(MAPPINGS), exist_ok=True)
    tmp = MAPPINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, MAPPINGS)


def _mapping_for(maps, key, sid, name):
    m = maps.get("%s_%s" % (key, sid))
    if m:
        return m
    # renumbered by the provider: same provider, same channel name
    want = norm_name(name)
    for k, v in maps.items():
        if k.startswith(key + "_") and want and norm_name(v.get("name")) == want:
            return v
    return None


# -- settings -------------------------------------------------------------------

def settings():
    """(on, [sources]) - sources are web addresses or files on the box."""
    try:
        with open(SETTINGS) as f:
            d = json.load(f)
    except Exception:
        d = {}
    on = d.get("epg_fill", True)
    src = d.get("epg_fill_sources")
    if not isinstance(src, list):
        src = []
    return bool(on), [str(s) for s in src if str(s).strip()]


def save_settings(**kw):
    try:
        with open(SETTINGS) as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.update(kw)
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, SETTINGS)


# -- names --------------------------------------------------------------------

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
    return "".join(w for w in words if w not in _DROP)


def norm_id(xid):
    """'BBC.One.HD.uk' / 'bbcone.uk' -> 'bbc1'."""
    s = str(xid or "").lower()
    s = re.sub(r"\.(uk|gb|ie|com|tv)$", "", s)
    return norm_name(s.replace(".", " "))


# -- the plugin's saved data ----------------------------------------------------

def _providers(log):
    """[(name, key, offset seconds, fill this provider?)] from playlists.txt
    and each provider's settings."""
    try:
        with open(SETTINGS) as f:
            path = json.load(f).get("playlists_path")
    except Exception:
        path = None
    path = path or "/etc/enigma2/E2iPTV/playlists.txt"
    try:
        from E2iPTV.backend.playlist_reader import load_providers
    except Exception:
        from .backend.playlist_reader import load_providers
    out = []
    for item in load_providers(path) or []:
        p = item[1] if isinstance(item, tuple) else item
        key = str(getattr(p, "provider_key", "") or "")
        if not key:
            continue
        cfg = {}
        try:
            with open(os.path.join(DATA, "cache", "provider_%s.json" % key)) as f:
                cfg = json.load(f) or {}
        except Exception:
            pass
        active = (getattr(p, "enabled", True) and not getattr(p, "disabled_in_file", False)
                  and cfg.get("enabled", True) is not False and cfg.get("show_live", True) is not False)
        try:
            off = int(cfg.get("epg_offset", 0) or 0) * 3600
        except Exception:
            off = 0
        out.append((getattr(p, "name", "?"), key, off, bool(active)))
    return out


def _channels_by_provider():
    out = {}
    for path in glob.glob(os.path.join(DATA, "cache", "ch_*.json")):
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


def _read_guide(key, sid):
    try:
        with open(os.path.join(DATA, "epg", "%s_%s.json" % (key, sid))) as f:
            rows = json.load(f)
        return [r for r in rows or [] if isinstance(r, dict)]
    except Exception:
        return []


def _coverage(rows, offset, now, until):
    """Hours of now..until covered by these programmes."""
    spans = []
    for r in rows:
        try:
            a, b = int(r.get("start", 0)) + offset, int(r.get("end", 0)) + offset
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
    return covered / 3600.0


# -- XMLTV ----------------------------------------------------------------------

def _xml_file(src, n, log):
    """A local copy of a guide file: downloaded (at most daily) or as given."""
    if not re.match(r"^https?://", src):
        return src if os.path.exists(src) else None
    local = os.path.join(FILL_DIR, "source_%d%s" % (n, ".xml.gz" if src.endswith(".gz") else ".xml"))
    try:
        if time.time() - os.path.getmtime(local) < DOWNLOAD_EVERY:
            return local
    except OSError:
        pass
    import urllib.request
    host = re.sub(r"^https?://([^/]+).*", r"\1", src)
    log("downloading guide file %d from %s" % (n, host))
    try:
        req = urllib.request.Request(src, headers={"User-Agent": "Enigma2"})
        tmp = local + ".part"
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, local)
        log("  %d KB" % (os.path.getsize(local) // 1024))
    except Exception as e:
        log("  download failed: %s%s" % (e.__class__.__name__,
            " - using the copy from before" if os.path.exists(local) else ""))
    return local if os.path.exists(local) else None


def _xml_ts(v):
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


def _iter_xml(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        for _ev, el in ET.iterparse(f, events=("end",)):
            if el.tag in ("channel", "programme"):
                yield el
                el.clear()


def _xml_channels(path, now, until):
    """{id: (names, programmes in now..until)} - first read."""
    chans, counts = {}, {}
    for el in _iter_xml(path):
        if el.tag == "channel":
            chans[el.get("id") or ""] = [d.text or "" for d in el.findall("display-name")]
        else:
            a, b = _xml_ts(el.get("start", "")), _xml_ts(el.get("stop", ""))
            if b > now and a < until:
                cid = el.get("channel") or ""
                counts[cid] = counts.get(cid, 0) + 1
    return dict((cid, (n, counts.get(cid, 0))) for cid, n in chans.items())


def _xml_programmes(path, wanted, lo, hi):
    """{id: [programme dicts]} for the wanted channels - second read."""
    out = dict((cid, []) for cid in wanted)
    for el in _iter_xml(path):
        if el.tag != "programme":
            continue
        cid = el.get("channel") or ""
        if cid not in out:
            continue
        a, b = _xml_ts(el.get("start", "")), _xml_ts(el.get("stop", ""))
        if b <= lo or a >= hi or b <= a:
            continue
        t = el.find("title")
        d = el.find("desc")
        out[cid].append({"start": a, "end": b,
                         "title": (t.text or "") if t is not None else "",
                         "description": (d.text or "") if d is not None else ""})
    return out


def _manual_choice(m, xml):
    """A saved choice as a plan entry (4 items = manual), "none", or None
    if its source isn't available right now."""
    t = m.get("type")
    if t == "none":
        return "none"
    if t == "other" and m.get("key") and m.get("sid") is not None:
        return ("other", str(m["key"]), str(m["sid"]), "manual")
    if t == "xml" and m.get("id"):
        for i, (_p, ch, _idx) in enumerate(xml):
            if m["id"] in ch:
                return ("xml", i, m["id"], "manual")
    return None


# -- the job ----------------------------------------------------------------------

def run(log=None):
    """Work out and write every stand-in guide. Returns a summary dict."""
    _mark_running(True)
    try:
        return _run(log)
    finally:
        _mark_running(False)


def _run(log=None):
    if log is None:
        log = _file_log
    on, sources = settings()
    os.makedirs(FILL_DIR, exist_ok=True)
    if not on:
        _clear_all(log)
        return {"off": True}
    t0 = time.time()
    now = int(time.time())
    until = now + CHECK_HOURS * 3600
    lo, hi = now - KEEP_BEFORE, now + KEEP_AFTER
    log("== filling guide gaps ==")
    provs = _providers(log)
    names = dict((k, n) for n, k, _o, _a in provs)
    offs = dict((k, o) for _n, k, o, _a in provs)
    active = set(k for _n, k, _o, a in provs if a)
    chans = _channels_by_provider()

    # every channel's own coverage, and an index of the well-covered ones
    cover = {}
    other_idx = {}
    for key, rows in chans.items():
        if key not in names:
            continue
        for sid, r in rows.items():
            hrs = _coverage(_read_guide(key, sid), offs.get(key, 0), now, until)
            cover[(key, sid)] = hrs
            if hrs >= 12:
                for k in (norm_id(r.get("epg_channel_id")), norm_name(r.get("name"))):
                    if k:
                        other_idx.setdefault(k, []).append((key, sid, hrs))

    # guide files
    xml = []                    # [(path, {id: (names, count)}, index)]
    for n, src in enumerate(sources, 1):
        path = _xml_file(src, n, log)
        if not path:
            log("guide file %d not available - skipped" % n)
            continue
        try:
            ch = _xml_channels(path, now, until)
        except Exception as e:
            log("guide file %d unreadable: %s" % (n, e))
            continue
        idx = {}
        for cid, (nm, cnt) in ch.items():
            if cnt:
                for k in [norm_id(cid)] + [norm_name(x) for x in nm]:
                    if k:
                        idx.setdefault(k, set()).add(cid)
        log("guide file %d: %d channels, %d with programmes" % (
            n, len(ch), sum(1 for v in ch.values() if v[1])))
        xml.append((path, ch, idx))
    try:
        listing = {}
        for _p, ch, _i in xml:
            for cid, (nm, cnt) in ch.items():
                listing[cid] = [nm[:3], cnt]
        tmp = XML_CHANNELS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(listing, f)
        os.replace(tmp, XML_CHANNELS)
    except Exception as e:
        log("couldn't save the guide file's channel list: %s" % e)

    # match each patchy channel of an active provider (your own choices first)
    maps = load_mappings()
    plan = {}                   # (key, sid) -> ("other", key2, sid2) | ("xml", i, cid)
    tally = {}
    for key, rows in chans.items():
        if key not in active:
            continue
        t = tally.setdefault(names[key], {"full": 0, "other": 0, "file": 0,
                                          "unclear": 0, "none": 0})
        for sid, r in rows.items():
            m = _mapping_for(maps, key, sid, r.get("name"))
            if m:
                c = _manual_choice(m, xml)
                if c == "none":
                    t["off"] = t.get("off", 0) + 1
                    continue
                if c:
                    plan[(key, sid)] = c
                    t["manual"] = t.get("manual", 0) + 1
                    continue
                log("%s: your choice for %s isn't available (%s) - automatic instead"
                    % (names[key], r.get("name"), m.get("label", "?")))
            if cover.get((key, sid), 0) >= CHECK_HOURS * 0.9:
                t["full"] += 1
                continue
            keys = [k for k in (norm_id(r.get("epg_channel_id")), norm_name(r.get("name"))) if k]
            choice = None
            unclear = False
            for k in keys:                                  # strongest first
                cands = [c for c in other_idx.get(k, []) if c[0] != key]
                if cands:
                    best = max(cands, key=lambda c: c[2])
                    choice = ("other", best[0], best[1])
                    break
            if choice is None:
                for k in keys:
                    for i, (_p, ch, idx) in enumerate(xml):
                        cands = idx.get(k)
                        if not cands:
                            continue
                        if len(set(norm_id(c) for c in cands)) == 1:
                            cid = max(cands, key=lambda c: ch[c][1])
                            choice = ("xml", i, cid)
                        else:
                            unclear = True
                        break
                    if choice or unclear:
                        break
            if choice:
                plan[(key, sid)] = choice
                t["other" if choice[0] == "other" else "file"] += 1
            else:
                t["unclear" if unclear else "none"] += 1

    # programmes from the guide files, for the channels that need them
    from_xml = {}
    for i, (path, _ch, _idx) in enumerate(xml):
        wanted = set(c[2] for c in plan.values() if c[0] == "xml" and c[1] == i)
        if wanted:
            try:
                from_xml[i] = _xml_programmes(path, wanted, lo, hi)
            except Exception as e:
                log("guide file %d unreadable: %s" % (i + 1, e))

    # write the stand-in guides
    keep = set()
    for (key, sid), c in plan.items():
        if c[0] == "other":
            off = offs.get(c[1], 0)
            rows = [{"start": int(r.get("start", 0)) + off, "end": int(r.get("end", 0)) + off,
                     "title": r.get("title", ""), "description": r.get("description", "")}
                    for r in _read_guide(c[1], c[2])]
            src = "%s: %s" % (names.get(c[1], "?"), chans.get(c[1], {}).get(c[2], {}).get("name", ""))
        else:
            rows = from_xml.get(c[1], {}).get(c[2], [])
            src = "guide file: %s" % c[2]
        rows = sorted((r for r in rows if r["end"] > lo and r["start"] < hi and r["end"] > r["start"]),
                      key=lambda r: r["start"])
        if not rows:
            continue
        name = "%s_%s" % (key, sid)
        keep.add(name)
        tmp = os.path.join(FILL_DIR, name + ".json.tmp")
        with open(tmp, "w") as f:
            json.dump({"source": src, "manual": len(c) > 3, "programmes": rows}, f)
        os.replace(tmp, os.path.join(FILL_DIR, name + ".json"))

    for path in glob.glob(os.path.join(FILL_DIR, "*.json")):
        base = os.path.basename(path)[:-5]
        if base not in ("index", "xml_channels") and base not in keep:
            try:
                os.remove(path)
            except OSError:
                pass
    summary = {"when": now, "keys": sorted(keep), "providers": tally,
               "files": len(xml), "seconds": int(time.time() - t0)}
    tmp = INDEX + ".tmp"
    with open(tmp, "w") as f:
        json.dump(summary, f)
    os.replace(tmp, INDEX)
    for pname, t in sorted(tally.items()):
        log("%-20s full %d, filled from other providers %d, from guide file %d, "
            "unclear %d, nothing found %d%s" % (
                pname[:20], t["full"], t["other"], t["file"], t["unclear"], t["none"],
                (", your choices %d" % t["manual"] if t.get("manual") else "")
                + (", switched off %d" % t["off"] if t.get("off") else "")))
    log("== %d channels given a stand-in guide (%d s) ==" % (len(keep), summary["seconds"]))
    return summary


def run_one(key, sid, log=None):
    """Apply your choice for one channel straight away (Guide from...),
    instead of waiting for the next full run. "Automatic" just removes
    your stand-in; the automatic one returns at the next full run."""
    if log is None:
        log = _file_log
    os.makedirs(FILL_DIR, exist_ok=True)
    name = "%s_%s" % (key, sid)
    path = os.path.join(FILL_DIR, name + ".json")
    m = load_mappings().get(name)
    rows, src = [], ""
    now = int(time.time())
    lo, hi = now - KEEP_BEFORE, now + KEEP_AFTER
    if m and m.get("type") == "other":
        off = 0
        try:
            with open(os.path.join(DATA, "cache", "provider_%s.json" % m["key"])) as f:
                off = int((json.load(f) or {}).get("epg_offset", 0) or 0) * 3600
        except Exception:
            pass
        rows = [{"start": int(r.get("start", 0)) + off, "end": int(r.get("end", 0)) + off,
                 "title": r.get("title", ""), "description": r.get("description", "")}
                for r in _read_guide(m["key"], m["sid"])]
        src = m.get("label", "")
    elif m and m.get("type") == "xml":
        _on, sources = settings()
        for n, s_ in enumerate(sources, 1):
            p_ = _xml_file(s_, n, log)
            if not p_:
                continue
            try:
                got = _xml_programmes(p_, {m["id"]}, lo, hi).get(m["id"]) or []
            except Exception as e:
                log("guide file %d unreadable: %s" % (n, e))
                got = []
            if got:
                rows, src = got, "guide file: %s" % m["id"]
                break
    rows = sorted((r for r in rows if r["end"] > lo and r["start"] < hi and r["end"] > r["start"]),
                  key=lambda r: r["start"])
    try:
        with open(INDEX) as f:
            idx = json.load(f)
    except Exception:
        idx = {"keys": [], "providers": {}}
    keys = set(idx.get("keys") or [])
    if rows:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"source": src, "manual": True, "programmes": rows}, f)
        os.replace(tmp, path)
        keys.add(name)
        log("%s: now using %s (%d programmes)" % (m.get("name", name), src, len(rows)))
    else:
        try:
            os.remove(path)
        except OSError:
            pass
        keys.discard(name)
        if m and m.get("type") in ("other", "xml"):
            log("%s: nothing found in %s yet" % (m.get("name", name), m.get("label", "?")))
        else:
            log("%s: stand-in guide removed (%s)" % (
                (m or {}).get("name", name), "switched off" if m else "back to automatic"))
    idx["keys"] = sorted(keys)
    tmp = INDEX + ".tmp"
    with open(tmp, "w") as f:
        json.dump(idx, f)
    os.replace(tmp, INDEX)
    return bool(rows)


def _clear_all(log):
    n = 0
    for path in glob.glob(os.path.join(FILL_DIR, "*.json")):
        try:
            os.remove(path)
            n += 1
        except OSError:
            pass
    if n:
        log("guide gap filling is off - %d stand-in guides removed" % n)


def _file_log(msg):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        try:
            if os.path.getsize(LOG_PATH) > 512 * 1024:
                os.replace(LOG_PATH, LOG_PATH + ".old")
        except OSError:
            pass
        with open(LOG_PATH, "a") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


if __name__ == "__main__":
    def both(msg):
        print(msg)
        _file_log(msg)
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "--one":
            run_one(sys.argv[2], sys.argv[3], both)
        else:
            run(both)
    except Exception as e:
        both("FAILED: %r" % (e,))
        sys.exit(1)
