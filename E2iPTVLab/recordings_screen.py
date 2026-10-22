# =============================================================================
# E2iPTV Lab - Recordings screen (two panels, TiviAtv movie-list style)
#
#   top line : folder being shown (left), clock (right)
#   left     : places - Scheduled recordings, Deleted recordings, the
#              recording bookmarks, "+ Add Recording Bookmark" - and,
#              underneath, the info for whatever is highlighted
#   right    : subfolders, then recordings with their date on the right
#
# Keys are as before: Up/Down move, OK opens or gives the same menus
# (Play/Delete, Open/Remove bookmark, Restore/Delete permanently,
# Cancel/Stop), Left/Right swap panels, Left on the left panel opens the
# rail, Exit goes back to Live TV. CH+/CH- move a page.
#
# All the recording logic (reading folders, bookmarks, delete to the bin,
# restore, scheduled list, play) is the very same code Screen2 uses - it
# is borrowed from Screen2, not copied - so both behave the same.
# =============================================================================

from . import theme as _T
import os
import time

from Screens.Screen import Screen
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.MultiContent import MultiContentEntryText
from enigma import eTimer, gFont, RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER

from .paths import HDD
from .state import state
from .screen2 import Screen2, IE2iList

# Colours (option-3 palette)
C_BG      = "E2iBg"
C_PANEL   = "E2iPanel"
C_TEXT    = "#ffffff"
C_DIM     = "E2iDim"
C_ACCENT  = "E2iHi"
P_PANEL   = _T.PANEL
P_SEL     = _T.HI
P_TEXT    = 0xffffff
P_DIM     = _T.DIM
P_ACCENT  = _T.HI
P_REC     = 0xff6666

# Layout (1920x1080)
L_X, L_W = 40, 600            # left panel
R_X, R_W = 670, 1210          # right panel
P_Y, P_H = 100, 940           # both panels
PL_ROW_H = 62
PL_ROWS = 7                   # places shown at once
PL_Y = P_Y + 16
FILE_ROW_H = 66
FILE_ROWS = 13
F_Y = P_Y + 16
INFO_Y = PL_Y + PL_ROWS * PL_ROW_H + 30


def _skin():
    return """
<screen name="E2iLabRecordings" position="0,0" size="1920,1080" flags="wfNoBorder" backgroundColor="{bg}">
  <eLabel position="0,0" size="1920,1080" backgroundColor="{bg}" zPosition="0"/>
  <widget name="hdr"   position="60,30" size="1400,50" font="E2iRegular;34" foregroundColor="{text}" backgroundColor="{bg}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="clock" position="1560,30" size="320,50" font="E2iRegular;34" foregroundColor="{dim}" backgroundColor="{bg}" halign="right" valign="center" transparent="1" zPosition="2"/>
  <widget name="l_bg"  position="{lx},{py}" size="{lw},{ph}" backgroundColor="{panel}" cornerRadius="18" zPosition="1"/>
  <widget name="r_bg"  position="{rx},{py}" size="{rw},{ph}" backgroundColor="{panel}" cornerRadius="18" zPosition="1"/>
  <widget name="places" position="{lx2},{ply}" size="{lw2},{plh}" backgroundColor="{panel}" scrollbarMode="showNever" transparent="1" zPosition="2"/>
  <eLabel position="{lx3},{sepy}" size="{lw3},2" backgroundColor="E2iSep" zPosition="2"/>
  <widget name="i_when"  position="{lx3},{iy0}" size="{lw3},36" font="E2iRegular;26" foregroundColor="{accent}" backgroundColor="{panel}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="i_size"  position="{lx3},{iy1}" size="{lw3},36" font="E2iRegular;26" foregroundColor="{dim}" backgroundColor="{panel}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="i_title" position="{lx3},{iy2}" size="{lw3},90" font="E2iRegular;34" foregroundColor="{text}" backgroundColor="{panel}" halign="left" valign="top" transparent="1" zPosition="2"/>
  <widget name="i_chan"  position="{lx3},{iy3}" size="{lw3},36" font="E2iRegular;26" foregroundColor="{dim}" backgroundColor="{panel}" halign="left" valign="center" noWrap="1" transparent="1" zPosition="2"/>
  <widget name="i_desc"  position="{lx3},{iy4}" size="{lw3},{idh}" font="E2iRegular;26" foregroundColor="{text}" backgroundColor="{panel}" halign="left" valign="top" transparent="1" zPosition="2"/>
  <widget name="files" position="{rx2},{fy}" size="{rw2},{fh}" backgroundColor="{panel}" scrollbarMode="showNever" transparent="1" zPosition="2"/>
  <widget name="more"  position="{rx2},{morey}" size="{rw2},30" font="E2iRegular;22" foregroundColor="{dim}" backgroundColor="{panel}" halign="right" valign="center" transparent="1" zPosition="2"/>
</screen>""".format(
        bg=C_BG, text=C_TEXT, dim=C_DIM, panel=C_PANEL, accent=C_ACCENT,
        lx=L_X, lw=L_W, rx=R_X, rw=R_W, py=P_Y, ph=P_H,
        lx2=L_X + 10, lw2=L_W - 20, ply=PL_Y, plh=PL_ROWS * PL_ROW_H,
        lx3=L_X + 24, lw3=L_W - 48, sepy=INFO_Y - 14,
        iy0=INFO_Y, iy1=INFO_Y + 40, iy2=INFO_Y + 88, iy3=INFO_Y + 186,
        iy4=INFO_Y + 230, idh=P_Y + P_H - (INFO_Y + 230) - 20,
        rx2=R_X + 10, rw2=R_W - 20, fy=F_Y, fh=FILE_ROWS * FILE_ROW_H,
        morey=P_Y + P_H - 40)


# The Screen2 code this screen re-uses as is.
_BORROW = (
    "_parse_recording_filename", "_read_recording_meta",
    "_read_native_meta_description", "_file_fallback_date",
    "_recording_bookmarks", "_save_recording_bookmarks",
    "_recordings_load_list", "_tidy_recording_folder",
    "_recordings_menu_choice", "_recordings_play",
    "_recording_running_key", "_recordings_confirm_delete",
    "_recordings_stop_then_delete", "_recordings_delete_confirmed",
    "_trash_folder", "_TRASH_MAP", "_MEDIA_EXTS", "_trash_map",
    "_trash_save_map", "_trash_note", "_trash_items", "_free_base",
    "_trash_files_of", "_trash_ok", "_trash_choice", "_trash_restore",
    "_trash_purge_confirmed", "_sched_ok", "_sched_choice",
    "_recording_bookmark_choice",
)


class RecordingsScreen(Screen):
    REC_VISIBLE = FILE_ROWS     # used by the borrowed scheduled/bin lists

    def __init__(self, session):
        Screen.__init__(self, session)
        self.skin = _skin()
        self.skinName = "E2iLabRecordings"
        for n in ("hdr", "clock", "l_bg", "r_bg", "i_when", "i_size",
                  "i_title", "i_chan", "i_desc", "more"):
            self[n] = Label("")
        self["places"] = IE2iList(PL_ROW_H)
        self["places"].l.setFont(0, gFont("E2iRegular", 30))
        self["files"] = IE2iList(FILE_ROW_H)
        self["files"].l.setFont(0, gFont("E2iRegular", 30))
        self["files"].l.setFont(1, gFont("E2iRegular", 26))

        self._rec_root = getattr(state, "recording_path", HDD + "/movie/")
        self._rec_path = None
        self._rec_entries = []
        self._rec_sel = 0
        self._rec_scroll = 0
        self._rec_mode = "files"
        self._places = []
        self._pl_sel = 0
        self._pl_scroll = 0
        self._focus = "files"

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ChannelSelectBaseActions"],
            {
                "ok": self._ok,
                "cancel": self._exit,
                "up": lambda: self._move(-1),
                "down": lambda: self._move(1),
                "left": self._left,
                "right": self._right,
                "nextBouquet": lambda: self._move(-self._page()),
                "prevBouquet": lambda: self._move(self._page()),
            }, -1)

        self._clock = eTimer()
        try:
            self._clock_conn = self._clock.timeout.connect(self._tick)
        except AttributeError:
            self._clock.callback.append(self._tick)
        self.onLayoutFinish.append(self._first_show)
        self.onClose.append(self._clock.stop)

    # ------------------------------------------------------------------
    def _first_show(self):
        for n in ("places", "files"):
            try:
                self[n].instance.setSelectionEnable(0)
            except Exception:
                pass
        self._tick()
        self._clock.start(15000, False)
        self._recordings_load(self._rec_root)
        self._recordings_render()

    def _tick(self):
        try:
            self["clock"].setText(time.strftime("%a %d %b  %H:%M"))
        except Exception:
            pass

    def _page(self):
        return PL_ROWS if self._focus == "places" else FILE_ROWS

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _recordings_load(self, path):
        if path not in Screen2._tidied_folders:
            Screen2._tidied_folders.add(path)
            self._tidy_recording_folder(path)
        same = (self._rec_mode == "files"
                and (self._rec_path or "").rstrip("/") == path.rstrip("/"))
        old_sel = self._rec_sel
        self._recordings_load_list(path)
        # Screen2 puts the places at the top of the same list; here they
        # live in the left panel instead.
        self._rec_entries = [
            e for e in self._rec_entries
            if not (e.get("is_location") or e.get("is_add_bookmark")
                    or e.get("is_sched_link") or e.get("is_trash_link"))]
        if not self._rec_entries:
            self._rec_entries = [{"is_dir": False, "path": None,
                                  "text": "(no recordings found)"}]
        # Start on the first recording rather than on "..".
        if same:
            self._rec_sel = min(old_sel, len(self._rec_entries) - 1)
        elif (len(self._rec_entries) > 1
                and self._rec_entries[0].get("text") == ".."):
            self._rec_sel = 1
        self._places_build()

    def _places_build(self):
        try:
            from .recording.schedule_list import scheduled_items
            n_sched = len(scheduled_items(state))
        except Exception:
            n_sched = 0
        try:
            _t, names = self._trash_items()
        except Exception:
            names = []
        places = [
            {"is_sched_link": True, "text": "Scheduled recordings (%d)" % n_sched},
            {"is_trash_link": True, "text": "Deleted recordings (%d)" % len(names)},
            {"is_home": True, "path": self._rec_root,
             "text": "Recordings folder"},
        ]
        for b in self._recording_bookmarks():
            if isinstance(b, dict) and b.get("path"):
                places.append({"is_bookmark": True, "path": b["path"],
                               "text": b.get("name") or b["path"]})
        places.append({"is_add_bookmark": True, "text": "+ Add Recording Bookmark"})
        self._places = places
        self._pl_sel = max(0, min(self._pl_sel, len(places) - 1))

    def _sched_show(self, keep_sel=False):
        Screen2._sched_show(self, keep_sel)
        self._places_build()
        self._recordings_render()

    def _trash_show(self, keep_sel=False):
        Screen2._trash_show(self, keep_sel)
        self._places_build()
        self._recordings_render()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def _place_is_current(self, p):
        if p.get("is_sched_link"):
            return self._rec_mode == "sched"
        if p.get("is_trash_link"):
            return self._rec_mode == "trash"
        if p.get("path") and self._rec_mode == "files":
            return p["path"].rstrip("/") == (self._rec_path or "").rstrip("/")
        return False

    def _render_places(self):
        W, H = L_W - 20, PL_ROW_H
        if self._pl_sel < self._pl_scroll:
            self._pl_scroll = self._pl_sel
        elif self._pl_sel >= self._pl_scroll + PL_ROWS:
            self._pl_scroll = self._pl_sel - PL_ROWS + 1
        rows = []
        for i in range(self._pl_scroll, min(len(self._places), self._pl_scroll + PL_ROWS)):
            p = self._places[i]
            sel = self._focus == "places" and i == self._pl_sel
            bg = P_SEL if sel else P_PANEL
            fg = 0x000000 if sel else (P_ACCENT if self._place_is_current(p) else P_TEXT)
            row = [None,
                   MultiContentEntryText(
                       pos=(0, 0), size=(W, H), font=0, text="",
                       flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                       color=fg, color_sel=fg,
                       backcolor=P_PANEL, backcolor_sel=P_PANEL)]
            if sel:
                row.append(MultiContentEntryText(
                    pos=(4, 3), size=(W - 8, H - 6), font=0, text="",
                    flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    color=fg, color_sel=fg, backcolor=bg, backcolor_sel=bg,
                    cornerRadius=14))
            row.append(MultiContentEntryText(
                pos=(24, 0), size=(W - 48, H), font=0, text=p.get("text", ""),
                flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                color=fg, color_sel=fg, backcolor=bg, backcolor_sel=bg))
            rows.append(row)
        self["places"].setList(rows)

    def _render_files(self):
        W, H = R_W - 20, FILE_ROW_H
        n = len(self._rec_entries)
        self._rec_sel = max(0, min(self._rec_sel, n - 1))
        if self._rec_sel < self._rec_scroll:
            self._rec_scroll = self._rec_sel
        elif self._rec_sel >= self._rec_scroll + FILE_ROWS:
            self._rec_scroll = self._rec_sel - FILE_ROWS + 1
        self._rec_scroll = max(0, min(self._rec_scroll, max(0, n - FILE_ROWS)))
        RIGHT_W = 430
        rows = []
        for i in range(self._rec_scroll, min(n, self._rec_scroll + FILE_ROWS)):
            e = self._rec_entries[i]
            sel = self._focus == "files" and i == self._rec_sel
            bg = P_SEL if sel else P_PANEL
            fg = 0x000000 if sel else P_TEXT
            dim = 0x000000 if sel else P_DIM
            left, right = self._file_texts(e)
            if not sel and e.get("title_colour"):
                fg = e["title_colour"]
            if e.get("is_dir") and not sel:
                fg = P_ACCENT if left.startswith("<") else P_TEXT
            row = [None,
                   MultiContentEntryText(
                       pos=(0, 0), size=(W, H), font=0, text="",
                       flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                       color=fg, color_sel=fg,
                       backcolor=P_PANEL, backcolor_sel=P_PANEL)]
            if sel:
                row.append(MultiContentEntryText(
                    pos=(4, 3), size=(W - 8, H - 6), font=0, text="",
                    flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    color=fg, color_sel=fg, backcolor=bg, backcolor_sel=bg,
                    cornerRadius=14))
            rw = RIGHT_W if right else 0
            row.append(MultiContentEntryText(
                pos=(24, 0), size=(W - 48 - rw - (20 if rw else 0), H), font=0,
                text=left, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                color=fg, color_sel=fg, backcolor=bg, backcolor_sel=bg))
            if right:
                row.append(MultiContentEntryText(
                    pos=(W - 24 - rw, 0), size=(rw, H), font=1, text=right,
                    flags=RT_HALIGN_RIGHT | RT_VALIGN_CENTER,
                    color=dim, color_sel=dim, backcolor=bg, backcolor_sel=bg))
            rows.append(row)
        self["files"].setList(rows)
        if n > FILE_ROWS:
            self["more"].setText("%d of %d" % (self._rec_sel + 1, n))
        else:
            self["more"].setText("")

    @staticmethod
    def _file_texts(e):
        """(left text, right text) for one row of the right panel."""
        if e.get("is_pick_use"):
            return e["text"], ""
        if e.get("is_dir") or e.get("path") is None:
            text = e.get("text", "")
            if "/  (" in text:                       # "name/  (n items)"
                name, count = text.split("/  (", 1)
                return "▸  " + name, count.rstrip(")")
            return text, ""
        if e.get("right_text"):
            return e.get("title") or "", e["right_text"]
        if e.get("start"):
            return (e.get("title") or "",
                    time.strftime("%a %d %b %Y   %H:%M", time.localtime(e["start"])))
        return e.get("title") or "", ""

    def _recordings_render(self):
        if self._rec_mode == "sched":
            self["hdr"].setText("Scheduled recordings")
        elif self._rec_mode == "trash":
            self["hdr"].setText("Deleted recordings")
        elif self._rec_mode == "pick":
            self["hdr"].setText("Choose a folder to bookmark:  " + self._pick_path)
        else:
            self["hdr"].setText(self._rec_path or "")
        self._render_places()
        self._render_files()
        self._update_info()

    def _update_info(self):
        when = size = title = chan = desc = ""
        if self._focus == "places":
            p = self._places[self._pl_sel] if self._places else {}
            title = p.get("text", "")
            if p.get("is_sched_link"):
                desc = "What is set to record. OK to see the list."
            elif p.get("is_trash_link"):
                desc = "Recordings you deleted. OK to see them - they can be restored or deleted for good."
            elif p.get("is_add_bookmark"):
                desc = "Save a folder here so you can jump to it."
            elif p.get("is_bookmark"):
                chan = p.get("path", "")
                desc = "OK to open or remove this bookmark."
            elif p.get("path"):
                chan = p["path"]
                desc = "OK to go back to your recordings folder."
        else:
            e = (self._rec_entries[self._rec_sel]
                 if 0 <= self._rec_sel < len(self._rec_entries) else {})
            if e.get("is_dir") or e.get("path") is None or e.get("is_pick_use"):
                title, count = self._file_texts(e)
                title = title.replace("\u25b8  ", "")
                size = count
            else:
                s, en = e.get("start"), e.get("end")
                if s:
                    when = time.strftime("%a %d %b %Y   %H:%M", time.localtime(s))
                    if en:
                        when += " - " + time.strftime("%H:%M", time.localtime(en))
                bits = []
                if s and en and en > s:
                    bits.append("%d min" % int((en - s) / 60))
                path = e.get("path")
                if path and os.path.isfile(path):
                    try:
                        b = os.path.getsize(path)
                        bits.append("%.1f GB" % (b / 1e9) if b >= 1e9 else "%d MB" % (b // 1000000))
                    except Exception:
                        pass
                size = "   ·   ".join(bits)
                title = (e.get("title") or "").replace("REC  ", "")
                chan = e.get("channel_name") or ""
                desc = e.get("description") or ""
        for n, v in (("i_when", when), ("i_size", size), ("i_title", title),
                     ("i_chan", chan), ("i_desc", desc)):
            try:
                self[n].setText(v)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------
    def _move(self, d):
        if self._focus == "places":
            if not self._places:
                return
            self._pl_sel = max(0, min(len(self._places) - 1, self._pl_sel + d))
            self._render_places()
        else:
            if not self._rec_entries:
                return
            self._rec_sel = max(0, min(len(self._rec_entries) - 1, self._rec_sel + d))
            self._render_files()
        self._update_info()

    def _left(self):
        if self._focus == "files":
            self._focus = "places"
            self._recordings_render()
        else:
            from .lab_rail import open_rail
            open_rail(self, "Recordings")

    def _right(self):
        if self._focus == "places":
            self._focus = "files"
            self._recordings_render()

    def _exit(self):
        if self._rec_mode in ("pick", "sched", "trash"):
            self._rec_mode = "files"
            self._recordings_load(self._rec_path)
            self._recordings_render()
            return
        self.close()

    def _ok(self):
        if self._focus == "places":
            self._place_ok()
        else:
            self._file_ok()

    def _place_ok(self):
        if not self._places:
            return
        p = self._places[self._pl_sel]
        if p.get("is_sched_link"):
            self._focus = "files"
            self._sched_show()
        elif p.get("is_trash_link"):
            self._focus = "files"
            self._trash_show()
        elif p.get("is_add_bookmark"):
            self._focus = "files"
            self._pick_load(self._rec_path or self._rec_root)
        elif p.get("is_bookmark"):
            self._rec_bm_entry = p
            from .side_menu import SideMenu
            self.session.openWithCallback(
                self._bookmark_menu_closed, SideMenu,
                p.get("text") or p["path"],
                [("Open", "open"), ("Remove bookmark", "remove")],
                "Removing the bookmark doesn't touch the folder or its recordings")
        elif p.get("path"):
            self._focus = "files"
            self._rec_sel = 0
            self._recordings_load(p["path"])
            self._recordings_render()

    def _bookmark_menu_closed(self, result):
        opened = result and (result[1] if isinstance(result, (tuple, list)) else result) == "open"
        self._recording_bookmark_choice(result)
        if opened:
            self._focus = "files"
        self._places_build()
        self._recordings_render()

    def _file_ok(self):
        if not self._rec_entries:
            return
        e = self._rec_entries[self._rec_sel]
        if self._rec_mode == "pick":
            self._pick_ok(e)
            return
        if e.get("is_sched_back"):
            self._rec_mode = "files"
            self._rec_sel = 0
            self._recordings_load(self._rec_path)
            self._recordings_render()
            return
        if e.get("sched"):
            self._sched_ok(e)
            return
        if e.get("trash"):
            self._trash_ok(e)
            return
        if e.get("path") is None:
            return
        if e.get("is_dir"):
            self._rec_sel = 0
            self._recordings_load(e["path"])
            self._recordings_render()
            return
        self._rec_menu_entry = e
        from .side_menu import SideMenu
        detail = e.get("channel_name") or ""
        if e.get("start"):
            when = time.strftime("%d %b  %H:%M", time.localtime(e["start"]))
            detail = ("%s  ·  %s" % (detail, when)) if detail else when
        self.session.openWithCallback(
            self._recordings_menu_choice, SideMenu,
            e.get("title") or os.path.basename(e["path"]),
            [("Play", "play"), ("Delete", "delete")], detail)

    # ------------------------------------------------------------------
    # "+ Add Recording Bookmark": pick a folder in the right panel
    # ------------------------------------------------------------------
    def _pick_load(self, path):
        entries = [{"is_pick_use": True, "path": path,
                    "text": "✔  Use this folder"}]
        parent = os.path.dirname(path.rstrip("/"))
        if parent and parent != path.rstrip("/"):
            entries.append({"is_dir": True, "path": parent, "text": ".."})
        try:
            for d in sorted((d for d in os.listdir(path)
                             if not d.startswith(".")
                             and os.path.isdir(os.path.join(path, d))),
                            key=lambda n: n.lower()):
                entries.append({"is_dir": True, "path": os.path.join(path, d),
                                "text": "▸  " + d})
        except Exception:
            pass
        self._pick_path = path
        self._rec_mode = "pick"
        self._rec_entries = entries
        self._rec_sel = 0
        self._rec_scroll = 0
        self._recordings_render()

    def _pick_ok(self, e):
        if not e.get("is_pick_use"):
            self._pick_load(e["path"])
            return
        path = e["path"]
        bookmarks = self._recording_bookmarks()
        if not any(isinstance(b, dict) and str(b.get("path") or "").rstrip("/") == path.rstrip("/")
                   for b in bookmarks):
            bookmarks.append({"name": os.path.basename(path.rstrip("/")) or path,
                              "path": path})
            self._save_recording_bookmarks(bookmarks)
        self._rec_mode = "files"
        self._rec_sel = 0
        self._recordings_load(path)
        self._recordings_render()


for _name in _BORROW:
    setattr(RecordingsScreen, _name, Screen2.__dict__[_name])
