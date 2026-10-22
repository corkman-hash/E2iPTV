#!/usr/bin/env python3
# =============================================================================
# E2iPTV — check a recording (.ts) for audio dropping out
#
#   python3 ts_audio_check.py "/media/hdd/movie/Some recording.ts"
#
# Reads the file in samples (a slice every megabyte, so even a 2-3 GB
# recording takes well under a minute on the box) and reports, in minutes
# from the start of the recording:
#   - which audio track(s) the stream announces, and when that changes
#     (a new audio track part-way through is the usual reason the sound
#     stops while the picture carries on - the player stays on the old one)
#   - stretches with no audio data at all
#   - points where the stream's clock jumps (provider restarted the
#     stream, or a recording reconnected after a drop)
# Read-only: never changes the file.
# =============================================================================

import os
import sys

PKT = 188
SAMPLE = 128 * 1024           # read this much...
STRIDE = 1024 * 1024          # ...out of every megabyte
GAP_SECS = 3.0                # report audio missing for longer than this

AUDIO_TYPES = {0x03: "MPEG audio", 0x04: "MPEG audio", 0x0F: "AAC",
               0x11: "AAC (LATM)", 0x81: "AC-3", 0x87: "E-AC-3"}
PRIV_DESC = {0x6A: "AC-3", 0x7A: "E-AC-3", 0x7C: "AAC", 0x7F: "audio"}


def mmss(t):
    t = int(max(0, t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def find_sync(buf):
    for i in range(min(len(buf) - 3 * PKT, PKT * 2)):
        if buf[i] == 0x47 and buf[i + PKT] == 0x47 and buf[i + 2 * PKT] == 0x47:
            return i
    return -1


def section(pkt):
    """Payload of a packet that starts a PSI section, or None."""
    if not pkt[1] & 0x40:
        return None
    afc = (pkt[3] >> 4) & 3
    pos = 4
    if afc in (2, 3):
        pos += 1 + pkt[4]
    if afc == 2 or pos >= PKT:
        return None
    pos += 1 + pkt[pos]          # pointer field
    return pkt[pos:] if pos < PKT else None


def parse_pat(sec):
    if not sec or sec[0] != 0x00:
        return []
    length = ((sec[1] & 0x0F) << 8) | sec[2]
    end = min(3 + length - 4, len(sec))
    out, i = [], 8
    while i + 4 <= end:
        prog = (sec[i] << 8) | sec[i + 1]
        pid = ((sec[i + 2] & 0x1F) << 8) | sec[i + 3]
        if prog != 0:
            out.append(pid)
        i += 4
    return out


def parse_pmt(sec):
    """(version, pcr_pid, {audio pid: kind}, video pids) or None."""
    if not sec or sec[0] != 0x02 or len(sec) < 12:
        return None
    length = ((sec[1] & 0x0F) << 8) | sec[2]
    end = min(3 + length - 4, len(sec))
    version = (sec[5] >> 1) & 0x1F
    pcr_pid = ((sec[8] & 0x1F) << 8) | sec[9]
    info_len = ((sec[10] & 0x0F) << 8) | sec[11]
    i = 12 + info_len
    audio, video = {}, set()
    while i + 5 <= end:
        st = sec[i]
        pid = ((sec[i + 1] & 0x1F) << 8) | sec[i + 2]
        es_len = ((sec[i + 3] & 0x0F) << 8) | sec[i + 4]
        desc = sec[i + 5:i + 5 + es_len]
        if st in AUDIO_TYPES:
            audio[pid] = AUDIO_TYPES[st]
        elif st == 0x06:
            j = 0
            while j + 2 <= len(desc):
                if desc[j] in PRIV_DESC:
                    audio[pid] = PRIV_DESC[desc[j]]
                    break
                j += 2 + desc[j + 1]
        elif st in (0x01, 0x02, 0x1B, 0x24, 0x10):
            video.add(pid)
        i += 5 + es_len
    return version, pcr_pid, audio, video


def pcr_of(pkt):
    afc = (pkt[3] >> 4) & 3
    if afc in (2, 3) and pkt[4] >= 7 and pkt[5] & 0x10:
        b = pkt[6:12]
        base = (b[0] << 25) | (b[1] << 17) | (b[2] << 9) | (b[3] << 1) | (b[4] >> 7)
        return base / 90000.0
    return None


def main(path):
    size = os.path.getsize(path)
    print("File:  %s  (%.0f MB)" % (os.path.basename(path), size / 1048576.0))

    pmt_pids = set()
    cur_pmt = None              # (version, audio dict)
    clock = 0.0                 # seconds from the start
    last_pcr = None
    pcr_pid = None
    events = []                 # (time, text)
    audio_seen_at = None        # time audio packets were last seen
    gap_start = None
    gaps = []
    jumps = 0

    with open(path, "rb") as f:
        off = 0
        while off < size:
            f.seek(off)
            buf = f.read(SAMPLE)
            s = find_sync(buf)
            if s < 0:
                off += STRIDE
                continue
            audio_here = False
            sample_pcr_moved = False
            for p in range(s, len(buf) - PKT + 1, PKT):
                pkt = buf[p:p + PKT]
                if pkt[0] != 0x47:
                    break
                pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
                if pid == 0:
                    pmt_pids.update(parse_pat(section(pkt)))
                elif pid in pmt_pids:
                    pmt = parse_pmt(section(pkt))
                    if pmt:
                        version, pcr_pid, audio, video = pmt
                        if cur_pmt is None:
                            events.append((clock, "Audio at the start: %s" % (
                                ", ".join("track %d (%s)" % (a, k) for a, k in sorted(audio.items()))
                                or "NONE announced")))
                        elif (version, audio) != cur_pmt:
                            old = cur_pmt[1]
                            if audio != old:
                                events.append((clock, "AUDIO TRACK CHANGED: %s  ->  %s" % (
                                    ", ".join("%d (%s)" % (a, k) for a, k in sorted(old.items())) or "none",
                                    ", ".join("%d (%s)" % (a, k) for a, k in sorted(audio.items())) or "none")))
                        cur_pmt = (version, audio)
                if pcr_pid is not None and pid == pcr_pid:
                    pcr = pcr_of(pkt)
                    if pcr is not None:
                        if last_pcr is not None:
                            d = pcr - last_pcr
                            if d < -1 and last_pcr > 95000 and pcr < 1000:
                                d += 2 ** 33 / 90000.0          # clock wrap
                            if 0 <= d <= 10:
                                clock += d
                                sample_pcr_moved = True
                            else:
                                jumps += 1
                                events.append((clock, "Stream clock jumped (stream restarted / reconnected)"))
                        last_pcr = pcr
                if cur_pmt and pid in cur_pmt[1]:
                    audio_here = True
            # audio presence bookkeeping, per sample
            if cur_pmt and sample_pcr_moved is not None:
                if audio_here:
                    if gap_start is not None and clock - gap_start > GAP_SECS:
                        gaps.append((gap_start, clock))
                    gap_start = None
                    audio_seen_at = clock
                elif gap_start is None:
                    gap_start = audio_seen_at if audio_seen_at is not None else clock
            off += STRIDE
    if gap_start is not None and clock - gap_start > GAP_SECS:
        gaps.append((gap_start, clock))

    print("Length (by the stream's own clock):  about %s" % mmss(clock))
    print("")
    for t, text in events:
        print("  %8s   %s" % (mmss(t), text))
    if not events:
        print("  (no programme information found - is this a .ts recording?)")
    print("")
    if gaps:
        print("No audio data at all:")
        for a, b in gaps:
            print("  %8s  to  %8s   (%s)" % (mmss(a), mmss(b), mmss(b - a)))
    else:
        print("No gaps in the audio data found.")
    changed = any("CHANGED" in e[1] for e in events)
    print("")
    if changed:
        print("Result: the audio track changes part-way through - the likely reason the")
        print("sound stops while the picture carries on.")
    elif gaps:
        print("Result: the stream itself is missing audio for the time(s) above.")
    else:
        print("Result: the audio looks continuous in the file; if the sound still stops")
        print("on playback, the cause is in the player rather than the recording.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not os.path.isfile(sys.argv[1]):
        print('Usage: python3 ts_audio_check.py "/path/to/recording.ts"')
        sys.exit(1)
    main(sys.argv[1])
