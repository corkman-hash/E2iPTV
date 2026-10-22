#!/usr/bin/env python3
# =============================================================================
# E2iPTV — report on the time-stamps at the start of a recording
#
#   python3 ts_gap_report.py "<file>.ts" [MB]
#
# Reads the first 200 MB (or the MB given) and prints:
#   - every stream in it (picture / sound / other) and how often it's timed
#   - every place where the sound's time-stamps step by more than 0.1 s,
#     with what the picture and the stream clock do at the same moment
# Read-only. For diagnosing pauses / sound gaps in recordings.
# =============================================================================

import os
import sys

PKT = 188


def ts(b, i):
    return (((b[i] >> 1) & 7) << 30) | (b[i + 1] << 22) | ((b[i + 2] >> 1) << 15) \
        | (b[i + 3] << 7) | (b[i + 4] >> 1)


def main(path, mb):
    with open(path, "rb") as f:
        data = f.read(mb * 1024 * 1024)
    start = 0
    while start < 400 and not (data[start] == 0x47 and data[start + PKT] == 0x47):
        start += 1
    streams = {}          # pid -> [stream_id, count, first_pts, last_pts]
    last = {}             # pid -> last pts
    last_pcr = None
    pcr0 = None
    events = []
    st_steps = {}         # sound pid -> its usual time-stamp spacing
    clock_steps = []
    i = start
    n = len(data)
    while i + PKT <= n:
        b = data
        if b[i] != 0x47:
            j = data.find(b"\x47", i + 1)
            if j < 0:
                break
            i = j
            continue
        pid = ((b[i + 1] & 0x1F) << 8) | b[i + 2]
        afc = (b[i + 3] >> 4) & 3
        pos = i + 4
        if afc & 2:
            alen = b[i + 4]
            if alen >= 7 and b[i + 5] & 0x10:
                j = i + 6
                pcr = (b[j] << 25) | (b[j + 1] << 17) | (b[j + 2] << 9) | (b[j + 3] << 1) | (b[j + 4] >> 7)
                if pcr0 is None:
                    pcr0 = pcr
                if last_pcr is not None:
                    d = (pcr - last_pcr) / 90000.0
                    if d > 0.3 or d < -0.1:
                        clock_steps.append(((last_pcr - pcr0) / 90000.0, d))
                last_pcr = pcr
            pos += 1 + alen
        if (b[i + 1] & 0x40) and (afc & 1) and pos + 14 <= i + PKT \
                and b[pos] == 0 and b[pos + 1] == 0 and b[pos + 2] == 1:
            sid = b[pos + 3]
            if b[pos + 7] & 0x80 and sid not in (0xBC, 0xBE, 0xBF, 0xF0, 0xF1, 0xF2, 0xF8, 0xFF):
                p = ts(b, pos + 9)
                st = streams.setdefault(pid, [sid, 0, p, p])
                st[1] += 1
                st[3] = p
                if pid in last and (0xC0 <= sid <= 0xDF or sid == 0xBD):
                    d = (p - last[pid]) / 90000.0
                    steps = st_steps.setdefault(pid, [])
                    if len(steps) < 50 and 0 < d < 1:
                        steps.append(d)
                    normal = sorted(steps)[len(steps) // 2] if steps else 0.05
                    if d > max(0.1, normal * 2.5) or d < -0.05:
                        vid = [q for q, s in streams.items() if 0xE0 <= s[0] <= 0xEF]
                        vpts = (last.get(vid[0]) if vid else None)
                        events.append((((last_pcr or 0) - (pcr0 or 0)) / 90000.0, pid, d,
                                       ((p - vpts) / 90000.0) if vpts is not None else None))
                last[pid] = p
        i += PKT

    print("File: %s   (first %d MB, about %.0f s)" % (
        os.path.basename(path), mb, ((last_pcr or 0) - (pcr0 or 0)) / 90000.0))
    print("")
    print("Streams:")
    for pid, (sid, cnt, first, lastp) in sorted(streams.items()):
        kind = ("picture" if 0xE0 <= sid <= 0xEF else "sound" if 0xC0 <= sid <= 0xDF
                else ("sound/data (0xBD)" if sid == 0xBD else "other (0x%02X)" % sid))
        span = (lastp - first) / 90000.0
        print("  pid %5d  %-14s %6d time-stamps over %6.1f s" % (pid, kind, cnt, span))
    print("")
    print("Clock (PCR) steps over 0.3 s or backwards: %d" % len(clock_steps))
    for t, d in clock_steps[:15]:
        print("  at %7.1f s   step %+7.2f s" % (t, d))
    print("")
    print("Sound time-stamp steps bigger than usual, or backwards: %d" % len(events))
    for t, pid, d, av in events[:15]:
        print("  at %7.1f s   pid %d   step %+6.2f s   sound-minus-picture %s" % (
            t, pid, d, ("%+.2f s" % av) if av is not None else "?"))


if __name__ == "__main__":
    if len(sys.argv) < 2 or not os.path.isfile(sys.argv[1]):
        print('Usage: python3 ts_gap_report.py "<file>.ts" [MB]')
        sys.exit(1)
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 200)
