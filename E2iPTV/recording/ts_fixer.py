# =============================================================================
# E2iPTV — keep a recording's sound working when the provider's stream
# changes its sound format part-way through (e.g. AAC -> AC-3 after the
# provider restarts the stream, or after a recording reconnects).
#
# Such a restart re-announces the track list (PMT) with the SAME version
# number, and the box's player only re-reads the track list when that
# number changes - so it keeps decoding the new sound as the old format:
# silence for the rest of the recording. StreamFixer sits between wget and
# the .ts file and gives each changed track list a new version number
# (with a correct checksum). Only those few bytes are touched; picture and
# sound data pass through unchanged. Confirmed on the box with a real
# recording (Gogglebox, 9 Oct: sound lost at 24:01, kept after repair).
#
# tools/ts_fix_audio.py is the same logic as a stand-alone repair for
# recordings made before this.
# =============================================================================

import os

PKT = 188
CHUNK = 188 * 20000          # ~3.7 MB at a time


def _crc_table():
    table = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else (c << 1)
        table.append(c & 0xFFFFFFFF)
    return table


_CRC = _crc_table()


def crc32_mpeg(data):
    c = 0xFFFFFFFF
    for b in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ _CRC[((c >> 24) ^ b) & 0xFF]
    return c


def _payload_start(pkt):
    """Offset of the PSI section inside a packet that starts one, or -1."""
    afc = (pkt[3] >> 4) & 3
    pos = 4
    if afc in (2, 3):
        pos += 1 + pkt[4]
    if afc == 2 or pos >= PKT:
        return -1
    pos += 1 + pkt[pos]                       # pointer field
    return pos if pos + 3 <= PKT else -1


class Fixer(object):

    def __init__(self):
        self.pmt_pids = set()
        self.last_body = {}        # pmt pid -> body of the last table seen
        self.version = {}          # pmt pid -> version we're writing
        self.changes = 0

    def _tables(self, pkt):
        """(section offset, section length) if the whole table fits."""
        pos = _payload_start(pkt)
        if pos < 0:
            return None
        length = ((pkt[pos + 1] & 0x0F) << 8) | pkt[pos + 2]
        if pos + 3 + length > PKT or length < 9:
            return None
        return pos, length

    def pat(self, pkt):
        t = self._tables(pkt)
        if not t or pkt[t[0]] != 0x00:
            return
        pos, length = t
        i, end = pos + 8, pos + 3 + length - 4
        while i + 4 <= end:
            prog = (pkt[i] << 8) | pkt[i + 1]
            if prog:
                self.pmt_pids.add(((pkt[i + 2] & 0x1F) << 8) | pkt[i + 3])
            i += 4

    def pmt(self, buf, at, pid):
        """Rewrite the PMT in buf[at:at+188] in place if needed."""
        pkt = buf[at:at + PKT]
        t = self._tables(pkt)
        if not t or pkt[t[0]] != 0x02:
            return
        pos, length = t
        sec = at + pos
        end = sec + 3 + length          # end of section incl. CRC
        # body = everything except the version byte and the CRC
        body = bytes(buf[sec:sec + 5]) + bytes(buf[sec + 6:end - 4])
        their = (buf[sec + 5] >> 1) & 0x1F
        if pid not in self.version:
            self.version[pid] = their
        elif body != self.last_body.get(pid):
            self.version[pid] = (self.version[pid] + 1) & 0x1F
            self.changes += 1
        self.last_body[pid] = body
        mine = self.version[pid]
        if mine == their:
            return
        buf[sec + 5] = (buf[sec + 5] & 0xC1) | (mine << 1)
        crc = crc32_mpeg(bytes(buf[sec:end - 4]))
        buf[end - 4:end] = crc.to_bytes(4, "big")



class StableFixer(Fixer):
    """For streams that are fetched again part-way through (catch-up: each
    skip is a new request from a new position). Every distinct track list
    always gets the same number, and different ones always differ, so the
    player notices the change whichever point playback jumps to. Shared by
    all requests for one catch-up programme."""

    def __init__(self):
        Fixer.__init__(self)
        self.known = {}            # pmt pid -> {body: version}
        import threading
        self.lock = threading.Lock()

    def pmt(self, buf, at, pid):
        with self.lock:
            pkt = buf[at:at + PKT]
            t = self._tables(pkt)
            if not t or pkt[t[0]] != 0x02:
                return
            pos, length = t
            sec = at + pos
            end = sec + 3 + length
            body = bytes(buf[sec:sec + 5]) + bytes(buf[sec + 6:end - 4])
            their = (buf[sec + 5] >> 1) & 0x1F
            seen = self.known.setdefault(pid, {})
            if body not in seen:
                if seen:
                    seen[body] = (max(seen.values()) + 1) & 0x1F
                    self.changes += 1
                else:
                    seen[body] = their
            mine = seen[body]
            if mine == their:
                return
            buf[sec + 5] = (buf[sec + 5] & 0xC1) | (mine << 1)
            crc = crc32_mpeg(bytes(buf[sec:end - 4]))
            buf[end - 4:end] = crc.to_bytes(4, "big")


# -----------------------------------------------------------------------------
# Continuous clock across joins
#
# Every stream restart (the provider's own, or our reconnect after a drop)
# starts the stream's clock (PCR/PTS/DTS) from a new, unrelated value. The
# box's player works out a recording's length and every skip from that
# clock, so a file with jumps in it shows a nonsense length and skips land
# in random places. Timeline shifts each new stretch so it carries straight
# on from where the last one stopped: one offset for the whole programme,
# changed only at a jump, so picture and sound stay in step.
# -----------------------------------------------------------------------------

_WRAP = 1 << 33
_HALF = 1 << 32
_JUMP = 3 * 90000            # a clock step bigger than 3 s is a restart
_BACK = 45000                # PCR may not run backwards more than 0.5 s


def _sdiff(a, b):
    d = (a - b) % _WRAP
    return d - _WRAP if d >= _HALF else d


def _get_ts(b, i):
    return (((b[i] >> 1) & 7) << 30) | (b[i + 1] << 22) | ((b[i + 2] >> 1) << 15) \
        | (b[i + 3] << 7) | (b[i + 4] >> 1)


def _put_ts(b, i, ts):
    b[i] = (b[i] & 0xF0) | (((ts >> 30) & 7) << 1) | 1
    b[i + 1] = (ts >> 22) & 0xFF
    b[i + 2] = (((ts >> 15) & 0x7F) << 1) | 1
    b[i + 3] = (ts >> 7) & 0xFF
    b[i + 4] = ((ts & 0x7F) << 1) | 1


_NO_PES_HDR = (0xBC, 0xBE, 0xBF, 0xF0, 0xF1, 0xF2, 0xF8, 0xFF)


# Only picture (0xE0-0xEF) and sound (0xC0-0xDF) PES streams are used to
# spot a restart - they carry a time-stamp many times a second. Subtitles,
# teletext and AC-3-in-private-stream (0xBD) are only timed when there's
# something to show, often 5-20 s apart, and were wrongly taken for
# restarts: each added a pause, so recordings with subtitles stuttered
# every few seconds. Those streams are still shifted, never used to detect.
def _detects(stream_id):
    return 0xC0 <= stream_id <= 0xEF


class Timeline(object):

    def __init__(self, squash=False):
        # squash=True (repair tool only): also close up small clock gaps
        # of 0.3-3 s - the pauses the faulty version added - by treating
        # them as joins with no gap.
        self.squash = squash
        self.offset = 0
        self.last_pcr = None
        self.last_pts = {}       # pid -> last output PTS
        self.pid_seen = {}       # pid -> stream clock (PCR) when last seen
        self.pid_norm = {}       # pid -> its usual time-stamp spacing (squash)
        self.pid_last = {}       # pid -> its last time-stamp, any stream (squash)
        self.pid_run = {}        # pid -> short steps in a row just now (squash)
        self.squash_times = []   # stream clock of recent closures (safety brake)
        self.pcr_step = 1800     # usual spacing of the stream clock
        self.high = None         # latest output PTS of any stream
        self.joins = 0

    def _may_squash(self):
        """Safety brake for repairs: more than 30 closures within 10 s of
        programme means something isn't a pause - stop closing for good."""
        if self.squash is None:
            return False
        now = self.last_pcr or 0
        self.squash_times = [t for t in self.squash_times
                             if 0 <= _sdiff(now, t) <= 900000]
        if len(self.squash_times) >= 30:
            self.squash = None           # off for the rest of the file
            return False
        self.squash_times.append(now)
        return True

    def _jump(self, last, adj, step):
        if self.squash:
            # repair: join tightly, no safety gap
            self.offset = (self.offset + _sdiff(last + step, adj)) % _WRAP
            self.joins += 1
            return
        # Start the new stretch half a second after the latest frame of
        # either picture or sound, so nothing overlaps at the join.
        ref = last
        if self.high is not None and _sdiff(self.high, ref) > 0:
            ref = self.high
        self.offset = (self.offset + _sdiff(ref + 45000, adj)) % _WRAP
        self.joins += 1

    def packet(self, b, i):
        """Correct the clock values in the packet at b[i:i+188] in place."""
        flags = b[i + 1]
        afc = (b[i + 3] >> 4) & 3
        pos = i + 4
        if afc & 2:
            alen = b[i + 4]
            if alen >= 7 and b[i + 5] & 0x10:            # PCR present
                j = i + 6
                base = (b[j] << 25) | (b[j + 1] << 17) | (b[j + 2] << 9) \
                    | (b[j + 3] << 1) | (b[j + 4] >> 7)
                adj = (base + self.offset) % _WRAP
                if self.last_pcr is not None:
                    d = _sdiff(adj, self.last_pcr)
                    if d > _JUMP or d < -_BACK:
                        self._jump(self.last_pcr, adj, 1800)
                        adj = (base + self.offset) % _WRAP
                    elif self.squash and d > 27000 and self._may_squash():   # 0.3 s
                        # close the gap to the stream's usual spacing
                        self.offset = (self.offset + _sdiff(self.last_pcr + self.pcr_step, adj)) % _WRAP
                        self.joins += 1
                        adj = (base + self.offset) % _WRAP
                    elif 0 < d <= 9000:                      # normal step
                        self.pcr_step = d
                self.last_pcr = adj
                if self.offset:
                    b[j] = (adj >> 25) & 0xFF
                    b[j + 1] = (adj >> 17) & 0xFF
                    b[j + 2] = (adj >> 9) & 0xFF
                    b[j + 3] = (adj >> 1) & 0xFF
                    b[j + 4] = ((adj & 1) << 7) | (b[j + 4] & 0x7F)
            pos += 1 + alen
        if not (flags & 0x40) or not (afc & 1) or pos + 14 > i + 188:
            return
        if b[pos] != 0 or b[pos + 1] != 0 or b[pos + 2] != 1:
            return
        if b[pos + 3] in _NO_PES_HDR:
            return
        ptsdts = b[pos + 7] >> 6
        if not ptsdts & 2:
            return
        pid = ((flags & 0x1F) << 8) | b[i + 2]
        p = pos + 9
        pts = _get_ts(b, p)
        adj = (pts + self.offset) % _WRAP
        if self.squash is not None and self.squash:
            # Repair: a stream timed many times a second (picture, or sound
            # of any kind - AC-3 sound is carried as 0xBD) closes a short
            # gap at its very first frame after it, not at the next clock
            # time-stamp, which can come a few frames later and leave
            # those frames out of step (heard as a break in the sound).
            lastp = self.pid_last.get(pid)
            norm = self.pid_norm.get(pid)
            if lastp is not None:
                d = _sdiff(adj, lastp)
                if -18000 <= d <= 9000:                     # normal step
                    if d > 0:
                        norm = d if norm is None else (norm * 7 + d) // 8
                        self.pid_norm[pid] = norm
                    self.pid_run[pid] = self.pid_run.get(pid, 0) + 1
                elif (norm is not None and self.pid_run.get(pid, 0) >= 10
                        and 27000 < d <= _JUMP and self._may_squash()):
                    # a steadily frequent stream jumped 0.3-3 s: a pause
                    self.offset = (self.offset - (d - norm)) % _WRAP
                    self.joins += 1
                    adj = (pts + self.offset) % _WRAP
                else:
                    self.pid_run[pid] = 0                   # not steady
            self.pid_last[pid] = adj
        if _detects(b[pos + 3]):
            last = self.last_pts.get(pid)
            # A track that has been missing for a while (e.g. the sound
            # changing format and coming back) isn't compared with its
            # time-stamp from long ago.
            seen = self.pid_seen.get(pid)
            if (last is not None and seen is not None and self.last_pcr is not None
                    and abs(_sdiff(self.last_pcr, seen)) > _JUMP):
                last = None
            if self.last_pcr is not None:
                self.pid_seen[pid] = self.last_pcr
            if last is not None and abs(_sdiff(adj, last)) > _JUMP:
                self._jump(last, adj, 3600)
                adj = (pts + self.offset) % _WRAP
            self.last_pts[pid] = adj
            if self.high is None or _sdiff(adj, self.high) > 0:
                self.high = adj
        if self.offset:
            _put_ts(b, p, adj)
            if ptsdts == 3 and p + 10 <= i + 188:
                _put_ts(b, p + 5, (_get_ts(b, p + 5) + self.offset) % _WRAP)


class StreamFixer(object):
    """feed() raw stream bytes, get back corrected bytes (holding back the
    last couple of packets until the next feed, or flush())."""

    def __init__(self, fixer=None, timeline=False, squash=False):
        self.fx = fixer or Fixer()
        self.tl = Timeline(squash=squash) if timeline else None
        self.carry = b""

    @property
    def changes(self):
        return self.fx.changes

    def feed(self, data):
        buf = bytearray(self.carry + data)
        keep = PKT * 2
        work = len(buf) - keep if len(buf) > keep else 0
        fx = self.fx
        i = buf.find(b"\x47\x40\x00", 0, work)
        while i != -1:
            if i + PKT < len(buf) and buf[i + PKT] == 0x47:
                fx.pat(bytes(buf[i:i + PKT]))
            i = buf.find(b"\x47\x40\x00", i + 1, work)
        for pid in list(fx.pmt_pids):
            hdr = bytes([0x47, 0x40 | (pid >> 8), pid & 0xFF])
            i = buf.find(hdr, 0, work)
            while i != -1:
                if i + PKT < len(buf) and buf[i + PKT] == 0x47:
                    fx.pmt(buf, i, pid)
                i = buf.find(hdr, i + 1, work)
        if self.tl is not None:
            self._timeline(buf, work)
        self.carry = bytes(buf[work:])
        return bytes(buf[:work])

    @property
    def joins(self):
        return self.tl.joins if self.tl is not None else 0

    def _timeline(self, buf, work):
        """Walk every packet starting before `work` (resyncing after any
        broken packet at a restart) and correct its clock values."""
        tl = self.tl
        i = getattr(self, "_next", 0)
        n = len(buf)
        while i < work:
            if buf[i] != 0x47 or (i + PKT < n and buf[i + PKT] != 0x47):
                j = buf.find(b"\x47", i + 1)
                while j != -1 and j + PKT < n and buf[j + PKT] != 0x47:
                    j = buf.find(b"\x47", j + 1)
                if j == -1 or j + PKT >= n:
                    i = work
                    break
                i = j
                continue
            if i + PKT > n:
                break
            tl.packet(buf, i)
            i += PKT
        # where the next walk starts, relative to the carried-over bytes
        self._next = max(0, i - work)

    def prime_from_file(self, path, tail=4 * 1024 * 1024):
        """Before appending to an existing recording (picked up again
        after a restart): learn its clock and track list from its last few
        MB, so the new part carries straight on from it."""
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                f.seek(max(0, size - tail))
                data = f.read()
        except Exception:
            return
        self.feed(data)                 # output discarded - state only
        self.carry = b""
        self._next = 0

    def flush(self):
        out, self.carry = self.carry, b""
        return out
