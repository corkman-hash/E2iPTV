# =============================================================================
# E2iPTV — Common B2
# Shared provider/category browser logic.
#
# The screen owning B2 supplies the actual B2 widgets and layout.
# This module contains the common B2 behaviour only.
# =============================================================================

from .paths import HDD  # data folder / debug logs
from .paths import DATA  # E2iPTV_Data folder
from .state import state


class B2Common(object):

    def _get_b2_categories(self, provider):
        content = getattr(self, "_b2_content", "live")

        if content == "live":
            return state.backend.get_live_categories(provider)

        if content == "series":
            return state.backend.get_series_categories(provider)

        if content == "movies":
            return state.backend.get_movie_categories(provider)

        return []

    def _cats_to_list(self, cats_raw):
        import json
        import os

        result = []

        for c in cats_raw:
            key = "%s_%s" % (
                getattr(c, "source_id", ""),
                c.category_id
            )

            cache_path = DATA + "/cache/ch_%s.json" % key

            if os.path.exists(cache_path):
                try:
                    data = json.load(open(cache_path))

                    if not data or not any(
                        ch.get("name", "").strip()
                        for ch in data
                    ):
                        continue

                except Exception:
                    pass

            result.append((
                c.category_name,
                {
                    "type": "category",
                    "name": c.category_name,
                    "category": c
                }
            ))

        return result

    def _private_changed(self):
        """Private categories shown/hidden (5 pressed twice): redraw B2."""
        self._b2_rebuild_flat()
        if self._b2_sel >= len(self._b2_flat):
            self._b2_sel = max(0, len(self._b2_flat) - 1)
        self._b2_refresh()

    def _private_key(self):
        from .backend.private import key_pressed
        key_pressed(self.session, self._private_changed)

    def _load_disabled_cats(self, provider):
        from .backend.provider_category_settings import load
        return load(provider)

    def _get_sel_provider_cat(self):
        if self._b2_sel < 0 or self._b2_sel >= len(self._b2_flat):
            return None, None, None, None

        label, data, is_cat = self._b2_flat[self._b2_sel]

        if not is_cat:
            return None, None, None, None

        category = data.get("category")

        prov_idx = None

        for i in range(self._b2_sel - 1, -1, -1):
            lbl, pdata, is_cat = self._b2_flat[i]

            if not is_cat:
                prov_idx = next(
                    (
                        j for j, (n, p)
                        in enumerate(state.providers)
                        if pdata.get("provider") is p
                    ),
                    None
                )
                break

        if prov_idx is None:
            return None, None, None, None

        provider = state.providers[prov_idx][1]
        cats = self._b2_open_cats.get(prov_idx, [])

        cat_idx = next(
            (
                i for i, (n, d)
                in enumerate(cats)
                if d.get("category") is category
            ),
            0
        )

        return prov_idx, provider, cat_idx, category

    def _b2_rebuild_flat(
            self,
            categories=None,
            open_prov_idx=None):

        if open_prov_idx is not None:
            self._b2_open_set.add(open_prov_idx)

            if categories is not None:
                self._b2_open_cats[open_prov_idx] = categories

        self._b2_flat = []

        # My Bouquets is shown at the top of B2. Only the display order
        # changes — its position in state.providers (which B2 indexes
        # by) stays at the end, so nothing else shifts.
        ordered = sorted(
            enumerate(state.providers),
            key=lambda t: 0 if getattr(t[1][1], "is_bouquet", False) else 1)

        for i, (name, provider) in ordered:

            from .backend.provider import shows as _shows
            if not _shows(provider, getattr(self, "_b2_content", "live")):
                continue

            # My Bouquets are Live TV only (not Series / Movies).
            if (getattr(provider, "is_bouquet", False)
                    and getattr(self, "_b2_content", "live") != "live"):
                continue

            provider_name = provider.name

            # An offline provider is displayed in red and must never
            # retain an expanded category list.  The health state is
            # otherwise display-only; this specifically removes stale
            # category expansion while the provider is unavailable.
            if getattr(provider, "online", True) is False:
                provider_name = "\\c00FF0000" + provider_name + "\\c00FFFFFF"

                self._b2_open_set.discard(i)
                self._b2_open_cats.pop(i, None)

            pdata = {
                "type": "provider",
                "name": provider.name,
                "provider": provider,
                "provider_idx": i
            }

            self._b2_flat.append((
                provider_name,
                pdata,
                False
            ))

            if i not in self._b2_open_set:
                continue

            if not hasattr(provider, "disabled_cats"):
                provider.disabled_cats = (
                    self._load_disabled_cats(provider)
                )

            disabled = {
                str(x) for x in provider.disabled_cats
            }
            # Private categories too, unless shown (backend/private.py)
            from .backend.private import hidden as _private_hidden
            disabled |= _private_hidden(
                provider, getattr(self, "_b2_content", "live"))

            for cname, cdata in (
                self._b2_open_cats.get(i) or []
            ):
                try:
                    cat = cdata.get("category")

                    if cat and str(cat.category_id) in disabled:
                        continue

                    cdata["provider"] = provider
                    cdata["provider_idx"] = i

                    self._b2_flat.append((
                        self._b2_cat_indent + cname,
                        cdata,
                        True
                    ))

                except Exception:
                    pass

    def _b2_refresh(self):
        flat = self._b2_flat
        sel = self._b2_sel

        tr = self._b2_tile_row
        total = len(flat)

        self["b2_tile"].setText(
            flat[sel][0] if 0 <= sel < total else ""
        )

        off = self._b2_row_offset

        for i in range(self._b2_total_rows):

            if i - off == tr:
                continue

            idx = sel - tr + (i - off)

            if "b2_row%d" % i in self:
                self["b2_row%d" % i].setText(
                    flat[idx][0]
                    if 0 <= idx < total
                    else ""
                )
