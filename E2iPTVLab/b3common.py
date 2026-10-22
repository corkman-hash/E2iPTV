# =============================================================================
# E2iPTV — Common B3 Channel List
#
# One shared B3 row-building routine for every screen that renders a
# scrollable window of channels (picon + name + catch-up glyph).
#
# Previously this exact logic was hand-copied into screen1.py, screen2.py
# and screen3.py with only the picon/text x-offsets differing between
# screen1 and the other two. Any change to how a B3 row is drawn — colours,
# spacing, the catch-up glyph, picon handling — had to be made in three
# places and kept in sync by hand. It now lives here once.
#
# The owning screen still supplies:
#   - its own scroll position and focused index (however it tracks them)
#   - its own widget ("b3_list", an IE2iList) to receive the built entries
#   - its own geometry, if it differs from the shared defaults below
# =============================================================================

from . import theme as _T
from Components.MultiContent import (
    MultiContentEntryText,
    MultiContentEntryPixmapAlphaBlend,
)
from enigma import RT_HALIGN_LEFT, RT_VALIGN_CENTER

try:
    from enigma import BT_SCALE, BT_KEEP_ASPECT_RATIO
    B3_PICON_FLAGS = BT_SCALE | BT_KEEP_ASPECT_RATIO
except ImportError:
    B3_PICON_FLAGS = 0

# All B3 row visual properties live here. Screens do not duplicate them.
B3_W            = 620
B3_ITEM_H       = 66
B3_VISIBLE      = 10

B3_FOCUS_COLOUR   = _T.MUTED
B3_BASE_COLOUR    = _T.PANEL
B3_TEXT_COLOUR    = 0xffffff
B3_ARCHIVE_COLOUR = 0xaaddff
B3_ARCHIVE_GLYPH  = u"\u21ba"

# Default row layout (used by screen2.py and screen3.py).
B3_PICON_X   = 10
B3_TEXT_X    = 76
B3_TEXT_W    = 494
B3_ARCHIVE_X = 580
B3_ARCHIVE_W = 30


class B3Common(object):

    # ------------------------------------------------------------------
    # B3 scroll window
    # ------------------------------------------------------------------

    def _b3_window(self, total, scroll, visible=B3_VISIBLE):
        """Clamp (scroll, scroll + visible) to the channel list length."""

        vis_start = max(0, scroll)
        vis_end = min(total, vis_start + visible)
        return vis_start, vis_end

    # ------------------------------------------------------------------
    # B3 row building
    # ------------------------------------------------------------------

    def _b3_build_entries(
            self,
            channels,
            vis_start,
            vis_end,
            focused_idx,
            item_w=B3_W,
            item_h=B3_ITEM_H,
            picon_x=B3_PICON_X,
            text_x=B3_TEXT_X,
            text_w=B3_TEXT_W,
            archive_x=B3_ARCHIVE_X,
            archive_w=B3_ARCHIVE_W,
            focus_colour=B3_FOCUS_COLOUR,
            base_colour=B3_BASE_COLOUR):
        """Build the MultiContent row entries for one visible B3 window."""

        from .backend.picon import picon_pixmap_for, register_refresh

        # Repaint this list when background picon downloads land.
        register_refresh(
            getattr(self, "_b3_refresh", None)
            or getattr(self, "_b3_mirror", None)
        )

        entries = []

        for idx, ch in enumerate(channels[vis_start:vis_end]):
            abs_idx    = vis_start + idx
            is_focused = abs_idx == focused_idx
            row_bg     = focus_colour if is_focused else base_colour
            txt_col    = B3_TEXT_COLOUR

            entry = [None]

            # Plain row background, then (Lab) a rounded highlight tile
            # on the focused row - the text cells sit inside the tile.
            entry.append(MultiContentEntryText(
                pos=(0, 0), size=(item_w, item_h),
                font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                text="", color=txt_col, color_sel=txt_col,
                backcolor=base_colour, backcolor_sel=base_colour))
            if is_focused:
                entry.append(MultiContentEntryText(
                    pos=(4, 3), size=(item_w - 8, item_h - 6),
                    font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text="", color=txt_col, color_sel=txt_col,
                    backcolor=row_bg, backcolor_sel=row_bg,
                    cornerRadius=14))

            pm = picon_pixmap_for(ch)
            if pm:
                entry.append(MultiContentEntryPixmapAlphaBlend(
                    pos=(picon_x, 4), size=(56, 58), png=pm,
                    flags=B3_PICON_FLAGS))

            # Channel being moved (EPG grid "Move Channel"): marked so
            # you can see which row is travelling.
            name_txt = ch.name
            name_col = txt_col
            # My Bouquets: channel whose provider is gone — greyed out.
            if getattr(ch, "_bq_missing", False):
                name_col = 0x808080
            if (getattr(self, "_moving_stream_id", None)
                    and str(ch.stream_id) == self._moving_stream_id):
                name_txt = u"\u00bb " + ch.name + u" \u00ab"
                name_col = 0xffcc00

            entry.append(MultiContentEntryText(
                pos=(text_x, 3), size=(text_w, item_h - 6),
                font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                text=name_txt, color=name_col, color_sel=name_col,
                backcolor=row_bg, backcolor_sel=row_bg))

            if ch.tv_archive:
                entry.append(MultiContentEntryText(
                    pos=(archive_x, 3), size=(min(archive_w, item_w - 14 - archive_x), item_h - 6),
                    font=0, flags=RT_HALIGN_LEFT | RT_VALIGN_CENTER,
                    text=B3_ARCHIVE_GLYPH,
                    color=B3_ARCHIVE_COLOUR, color_sel=B3_ARCHIVE_COLOUR,
                    backcolor=row_bg, backcolor_sel=row_bg))

            entries.append(entry)

        return entries

    # ------------------------------------------------------------------
    # B3 focus positioning
    # ------------------------------------------------------------------

    def _b3_move_to_focused(self, widget_name, focused_idx, vis_start):
        """Move the listbox selection to the focused row, if visible."""

        focused_in_visible = focused_idx - vis_start

        try:
            entries = self[widget_name].l.getList()
        except Exception:
            entries = None

        if entries is None or 0 <= focused_in_visible < len(entries):
            try:
                self[widget_name].moveToIndex(focused_in_visible)
            except Exception:
                pass
