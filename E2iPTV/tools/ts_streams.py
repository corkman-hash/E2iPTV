#!/usr/bin/env python3
# =============================================================================
# E2iPTV — list what's inside a recording (or any .ts file)
#
#   python3 ts_streams.py "<file>.ts" [MB]
#
# Reads the start of the file (50 MB, or the MB given) and lists each
# stream the channel sends, as the channel itself describes it: picture,
# sound (with language), DVB subtitles (language), teletext (which pages
# - subtitles are usually page 888 in the UK), and anything else, with how
# much of each there is. Read-only. For finding out whether a provider's
# stream carries subtitles at all.
# =============================================================================

import os
import sys

PKT = 188

STREAM_TYPES = {
    0x01: "picture (MPEG-1)", 0x02: "picture (MPEG-2)", 0x1B: "picture (H.264)",
    0x24: "picture (HEVC)", 0x03: "sound (MPEG-1)", 0x04: "sound (MPEG-2)",
    0x0F: "sound (AAC)", 0x11: "sound (AAC LATM)", 0x81: "sound (AC-3)",
    0x87: "sound (E-AC-3)", 0x06: "private data", 0x05: "private sections",
    0x0B: "DSM-CC", 0x0C: "DSM-CC", 0x0D: "DSM-CC", 0x15: "metadata",
}
TT_TYPES = {1: "teletext pages", 2: "SUBTITLES (teletext)", 3: "additional info",
            4: "programme schedule", 5: "SUBTITLES for hard of hearing (teletext)"}


def sections(data, start, want_pid):
    """Yield complete PSI sections for a pid (PAT / PMT)."""
    buf = b""
    i = start
    n = len(data)
    while i + PKT <= n:
        if data[i] != 0x47:
            i += 1
            continue
        pid = ((data[i + 1] & 0x1F) << 8) | data[i + 2]
        if pid == want_pid:
            pusi = data[i + 1] & 0x40
            afc = (data[i + 3] >> 4) & 3
            pos = i + 4
            if afc & 2:
                pos += 1 + data[i + 4]
            if afc & 1 and pos < i + PKT:
                payload = data[pos:i + PKT]
                if pusi:
                    ptr = payload[0]
                    buf = payload[1 + ptr:]
                else:
                    buf += payload
                if len(buf) >= 3:
                    slen = ((buf[1] & 0x0F) << 8) | buf[2]
                    if len(buf) >= 3 + slen:
                        yield buf[:3 + slen]
                        buf = b""
        i += PKT


def descriptors(blob):
    out = []
    j = 0
    while j + 2 <= len(blob):
        tag, ln = blob[j], blob[j + 1]
        body = blob[j + 2:j + 2 + ln]
        if tag == 0x0A and ln >= 3:
            out.append("language %s" % body[:3].decode("latin-1"))
        elif tag == 0x59:
            k = 0
            while k + 8 <= len(body):
                lang = body[k:k + 3].decode("latin-1")
                st = body[k + 3]
                kind = "for hard of hearing" if 0x20 <= st <= 0x24 else "normal"
                out.append("DVB SUBTITLES, %s (%s)" % (lang, kind))
                k += 8
        elif tag == 0x56 or tag == 0x46:
            k = 0
            while k + 5 <= len(body):
                lang = body[k:k + 3].decode("latin-1")
                t = body[k + 3] >> 3
                mag = body[k + 3] & 7 or 8
                page = body[k + 4]
                out.append("%s, %s, page %d%02X" % (
                    TT_TYPES.get(t, "teletext type %d" % t), lang, mag, page))
                k += 5
        elif tag == 0x6A:
            out.append("AC-3")
        elif tag == 0x7A:
            out.append("E-AC-3")
        elif tag == 0x7C:
            out.append("AAC")
        elif tag == 0x52 and ln >= 1:
            out.append("component tag %d" % body[0])
        elif tag == 0x05 and ln >= 4:
            out.append("registration %s" % body[:4].decode("latin-1", "replace"))
        j += 2 + ln
    return out


def describe(data):
    """Lines describing the streams in a chunk of TS data."""
    out = []
    start = 0
    while start < 400 and start + PKT < len(data) and not (
            data[start] == 0x47 and data[start + PKT] == 0x47):
        start += 1
    counts = {}
    for i in range(start, len(data) - PKT + 1, PKT):
        if data[i] == 0x47:
            pid = ((data[i + 1] & 0x1F) << 8) | data[i + 2]
            counts[pid] = counts.get(pid, 0) + 1
    pmt_pids = []
    for sec in sections(data, start, 0):
        j = 8
        while j + 4 <= len(sec) - 4:
            prog = (sec[j] << 8) | sec[j + 1]
            ppid = ((sec[j + 2] & 0x1F) << 8) | sec[j + 3]
            if prog:
                pmt_pids.append(ppid)
            j += 4
        break
    if not pmt_pids:
        return ["No programme table found - not a normal TS stream?"]
    found_subs = False
    for ppid in pmt_pids:
        for sec in sections(data, start, ppid):
            pi_len = ((sec[10] & 0x0F) << 8) | sec[11]
            j = 12 + pi_len
            out.append("Streams the channel lists:")
            seen = set()
            while j + 5 <= len(sec) - 4:
                st = sec[j]
                epid = ((sec[j + 1] & 0x1F) << 8) | sec[j + 2]
                es_len = ((sec[j + 3] & 0x0F) << 8) | sec[j + 4]
                desc = descriptors(sec[j + 5:j + 5 + es_len])
                if any("SUBTITLES" in d for d in desc):
                    found_subs = True
                seen.add(epid)
                out.append("  pid %5d  %-18s %8d packets   %s" % (
                    epid, STREAM_TYPES.get(st, "type 0x%02X" % st),
                    counts.get(epid, 0), "; ".join(desc) or "-"))
                j += 5 + es_len
            others = [q for q in counts if q not in seen and q not in (0, ppid, 0x1FFF)
                      and counts[q] > 10]
            if others:
                out.append("Other data, not listed by the channel:")
                for q in sorted(others):
                    out.append("  pid %5d  %8d packets%s" % (
                        q, counts[q], "   (channel name/details)" if q == 17 else ""))
            break
    out.append("SUBTITLES: yes - see above" if found_subs else
               "SUBTITLES: none - this provider isn't sending them on this channel")
    return out


def main(path, mb):
    with open(path, "rb") as f:
        data = f.read(mb * 1024 * 1024)
    print("File: %s   (first %d MB)" % (os.path.basename(path), mb))
    print("")
    for line in describe(data):
        print(line)


if __name__ == "__main__":
    if len(sys.argv) < 2 or not os.path.isfile(sys.argv[1]):
        print('Usage: python3 ts_streams.py "<file>.ts" [MB]')
        sys.exit(1)
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 50)
