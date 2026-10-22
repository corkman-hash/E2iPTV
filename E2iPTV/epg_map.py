# =============================================================================
# E2iPTV — "Guide from..." (a channel's OK menu in the EPG grid / channel
# guide): choose where this channel's guide comes from, yourself.
#
#   Automatic              - the usual: own guide, gaps filled (epg_fill.py)
#   From another provider  - pick the provider, then search its channels
#   From the guide file    - search the guide file's channels (Settings ->
#                            EPG -> Fill Gaps From)
#   No guide               - no stand-in for this channel (a wrong match)
#
# Saved for good in E2iPTV_Data/cache/epg_mappings.json (see epg_fill.py)
# and applied straight away in the background (epg_fill.py --one).
# =============================================================================

import glob
import json
import os
import subprocess
import threading

from twisted.internet import reactor

from .paths import DATA
from .state import state
from .side_menu import SideMenu

FILL_SCRIPT = "/usr/lib/enigma2/python/Plugins/Extensions/E2iPTV/epg_fill.py"
MAX_RESULTS = 60


def _fill():
    from . import epg_fill
    return epg_fill


def _key_sid(provider, channel):
    key = str(getattr(channel, "provider_key", "") or getattr(provider, "provider_key", "") or "")
    return key, str(getattr(channel, "stream_id", ""))


def describe(mapping):
    if not mapping:
        return "Automatic"
    t = mapping.get("type")
    if t == "none":
        return "No guide (switched off)"
    return mapping.get("label") or "?"


class GuideFrom(object):
    """The whole Guide from... conversation for one channel."""

    def __init__(self, session, provider, channel):
        self.session = session
        self.provider = provider
        self.channel = channel
        self.key, self.sid = _key_sid(provider, channel)
        self.cname = getattr(channel, "name", "") or "?"

    # -- step 1: what kind of source ----------------------------------------

    def start(self):
        if not self.key:
            return
        cur = _fill().load_mappings().get("%s_%s" % (self.key, self.sid))
        opts = [("Automatic", "auto"),
                ("From another provider...", "other"),
                ("From the guide file...", "file"),
                ("No guide for this channel", "none")]
        self.session.openWithCallback(
            self._kind_chosen, SideMenu, "Guide for %s" % self.cname, opts,
            detail="Now: %s" % describe(cur), value_only=True)

    def _kind_chosen(self, kind):
        if kind == "auto":
            self._save(None)
        elif kind == "none":
            self._save({"type": "none", "name": self.cname, "label": "none"})
        elif kind == "other":
            self._pick_provider()
        elif kind == "file":
            self._search(self._file_search, "Search the guide file")

    # -- another provider -------------------------------------------------------

    def _pick_provider(self):
        provs = [(n, p) for n, p in (state.providers or [])
                 if not getattr(p, "is_bouquet", False)
                 and str(getattr(p, "provider_key", "")) != self.key]
        if not provs:
            return
        self._provs = dict((str(p.provider_key), (n, p)) for n, p in provs)
        opts = [(n, str(p.provider_key)) for n, p in provs]
        self.session.openWithCallback(
            self._provider_chosen, SideMenu, "Guide from which provider?", opts,
            detail=self.cname, value_only=True)

    def _provider_chosen(self, pkey):
        if not pkey:
            return
        self._src_key = pkey
        self._search(self._other_search, "Search %s" % self._provs[pkey][0])

    def _other_search(self, text):
        """Background: this provider's saved channels whose name fits."""
        fill = _fill()
        want = fill.norm_name(text)
        out, seen = [], set()
        for path in glob.glob(os.path.join(DATA, "cache", "ch_%s_*.json" % self._src_key)):
            try:
                with open(path) as f:
                    rows = json.load(f)
            except Exception:
                continue
            for r in rows or []:
                nm = str(r.get("name", "")).strip()
                sid = str(r.get("stream_id"))
                if not nm or sid in seen:
                    continue
                if want and want not in fill.norm_name(nm):
                    continue
                seen.add(sid)
                has = os.path.exists(os.path.join(DATA, "epg", "%s_%s.json" % (self._src_key, sid)))
                out.append(("%s%s" % (nm, "" if has else "   (no guide saved)"),
                            {"type": "other", "key": self._src_key, "sid": sid,
                             "name": self.cname,
                             "label": "%s: %s" % (self._provs[self._src_key][0], nm)}))
        out.sort(key=lambda x: (x[0].endswith(")"), x[0].lower()))
        return out[:MAX_RESULTS]

    # -- the guide file --------------------------------------------------------

    def _file_search(self, text):
        fill = _fill()
        try:
            with open(fill.XML_CHANNELS) as f:
                listing = json.load(f)
        except Exception:
            return None                  # no guide file read yet
        want = fill.norm_name(text)
        out = []
        for cid, (names, cnt) in listing.items():
            keys = [fill.norm_id(cid)] + [fill.norm_name(n) for n in names]
            if want and not any(want in k for k in keys if k):
                continue
            label = names[0] if names else cid
            out.append(("%s   [%s]%s" % (label, cid, "" if cnt else "  (no programmes)"),
                        {"type": "xml", "id": cid, "name": self.cname,
                         "label": "guide file: %s" % label}))
        out.sort(key=lambda x: (x[0].endswith(")"), x[0].lower()))
        return out[:MAX_RESULTS]

    # -- searching (typed on the remote) --------------------------------------

    def _search(self, finder, title):
        self._finder = finder
        self._title = title
        start = self.cname
        try:
            start = _fill().norm_name(self.cname) and self._plain(self.cname)
        except Exception:
            pass
        from Screens.VirtualKeyBoard import VirtualKeyBoard
        self.session.openWithCallback(self._typed, VirtualKeyBoard,
                                      title=title, text=start)

    @staticmethod
    def _plain(name):
        """'UK: BBC ONE FHD' -> 'BBC ONE' as a starting search."""
        import re
        s = re.sub(r"^\s*(\[[^\]]*\]|\|[^|]*\||[A-Za-z]{2,3}\s*[:|\-])\s*", "", str(name))
        s = re.sub(r"\b(FHD|UHD|HD|SD|4K|HEVC|VIP|BACKUP)\b", "", s, flags=re.I)
        return " ".join(s.split())

    def _typed(self, text):
        if text is None:
            return
        self._last_text = text

        def work():
            try:
                res = self._finder(text)
            except Exception as e:
                print("[E2iPTV] guide search:", e)
                res = []
            reactor.callFromThread(self._results, res)
        threading.Thread(target=work, name="E2iPTV-GuideSearch", daemon=True).start()

    def _results(self, res):
        from Screens.MessageBox import MessageBox
        if res is None:
            self.session.open(MessageBox,
                              "No guide file has been read yet.\n\nChoose one in Settings -> EPG -> "
                              "Fill Gaps From, then press Fill Gaps Now once.",
                              MessageBox.TYPE_INFO)
            return
        if not res:
            self.session.openWithCallback(
                lambda again: again and self._search(self._finder, self._title),
                MessageBox, "Nothing found for '%s'. Search again?" % self._last_text,
                MessageBox.TYPE_YESNO)
            return
        self._res = res
        opts = [(label, i) for i, (label, _m) in enumerate(res)]
        self.session.openWithCallback(
            self._picked, SideMenu, "%s - %d found" % (self._title, len(res)), opts,
            detail="Guide for %s" % self.cname, value_only=True)

    def _picked(self, i):
        if i is None:
            return
        self._save(self._res[i][1])

    # -- saving ---------------------------------------------------------------

    def _save(self, mapping):
        from Screens.MessageBox import MessageBox
        try:
            _fill().save_mapping(self.key, self.sid, mapping)
        except Exception as e:
            self.session.open(MessageBox, "Couldn't save: %s" % e, MessageBox.TYPE_ERROR)
            return
        try:
            subprocess.Popen(["nice", "-n", "10", "python3", FILL_SCRIPT, "--one", self.key, self.sid],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print("[E2iPTV] guide apply:", e)
        if mapping is None:
            text = ("%s: back to automatic.\n\nIts automatic stand-in guide (if any) "
                    "returns at the next Fill Gaps Now or overnight." % self.cname)
        elif mapping.get("type") == "none":
            text = "%s: no stand-in guide from now on." % self.cname
        else:
            text = ("%s: guide from %s.\n\nShows within a minute "
                    "(guide file: a little longer)." % (self.cname, describe(mapping)))
        self.session.open(MessageBox, text, MessageBox.TYPE_INFO, timeout=6)


def open_guide_from(session, provider, channel):
    GuideFrom(session, provider, channel).start()
