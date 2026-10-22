#!/usr/bin/env python3
# =============================================================================
# E2iPTV — repair a recording: sound that stops part-way, and a wrong
# length / skipping to random places
#
#   python3 /usr/lib/enigma2/python/Plugins/Extensions/E2iPTVLab/tools/ts_fix_audio.py "<file>.ts"
#
# Writes a repaired COPY next to it, "<file> (fixed).ts", plus copies of
# its details files, so it shows in Recordings as "<title> (fixed)". The
# original is never touched. Needs free space for the copy.
#
# Fixes the two problems a stream restart (the provider's, or a reconnect
# after a drop) leaves in a recording - the same corrections new
# recordings get as they're written (recording/ts_fixer.py):
#   - the sound switching format without the player noticing (silence)
#   - the stream's clock jumping, which gives a nonsense length and
#     skips that land in random places
# =============================================================================

import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIXER = os.path.join(HERE, "..", "recording", "ts_fixer.py")


def _load_fixer():
    spec = importlib.util.spec_from_file_location("e2iptv_ts_fixer", FIXER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(src, test_mb=None):
    try:
        tsf = _load_fixer()
    except Exception as e:
        print("Can't load the plugin's corrector (%s).\nRun this from the "
              "plugin's tools folder:\n  %s" % (e, os.path.abspath(__file__)))
        return 1
    base, ext = os.path.splitext(src)
    dst = base + (" (test)" if test_mb else " (fixed)") + ext
    size = os.path.getsize(src)
    if test_mb:
        size = min(size, test_mb * 1024 * 1024)
    print("Repairing into: %s" % os.path.basename(dst))
    print("(about a minute per 500 MB on the box - %d MB to do)" % (size // 1048576))
    # squash: also remove the short pauses an earlier version added to
    # recordings of channels with subtitles/teletext (stutter every few s)
    sf = tsf.StreamFixer(timeline=True, squash=True)
    done, last = 0, -1
    started = time.time()
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            data = fi.read(4 * 1024 * 1024)
            if not data:
                break
            fo.write(sf.feed(data))
            done += len(data)
            if test_mb and done >= size:
                break
            # (a recording still being written keeps growing)
            pct = min(100, done * 100 // max(1, size, os.path.getsize(src)))
            if pct // 10 != last // 10:
                last = pct
                print("  %d%%" % pct, flush=True)
        fo.write(sf.flush())

    # details files, so the copy shows properly in Recordings
    for side in (".json", ".meta"):
        try:
            if side == ".json" and os.path.exists(src + side):
                with open(src + side) as f:
                    meta = json.load(f)
                meta["title"] = (meta.get("title") or "") + (" (test)" if test_mb else " (fixed)")
                with open(dst + side, "w") as f:
                    json.dump(meta, f)
            elif side == ".meta" and os.path.exists(src + side):
                with open(src + side) as f:
                    lines = f.read().split("\n")
                if len(lines) > 1:
                    lines[1] = lines[1] + (" (test)" if test_mb else " (fixed)")
                if len(lines) > 6:
                    lines[5] = "0"                      # length: box recalculates
                    lines[6] = str(os.path.getsize(dst))
                with open(dst + side, "w") as f:
                    f.write("\n".join(lines))
        except Exception:
            pass

    print("Done in %d s." % (time.time() - started))
    if test_mb:
        print("TEST copy of the first %d MB only - check it, then run again "
              "without 'test' for the whole recording." % test_mb)
    if getattr(sf, "tl", None) is not None and sf.tl.squash is None:
        print("NOTE: closing pauses was switched off part-way (far too many) -"
              " this recording may need looking at.")
    print("  stream restarts joined smoothly: %d" % sf.joins)
    print("  sound format changes corrected:  %d" % sf.changes)
    if not sf.joins and not sf.changes:
        print("Nothing needed fixing - the copy is the same as the original.")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    test = None
    if args and args[-1] == "test":
        test, args = 300, args[:-1]
    if len(args) != 1 or not os.path.isfile(args[0]):
        print('Usage: python3 %s "/path/to/recording.ts" [test]' % os.path.abspath(__file__))
        sys.exit(1)
    sys.exit(main(args[0], test))
