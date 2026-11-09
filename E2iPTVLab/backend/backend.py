# =============================================================================
# E2iPTV Backend
# Backend Manager
# =============================================================================

from ..paths import LOG  # data folder / debug logs
from .provider_store import ProviderStore
from .category_store import CategoryStore
from .live_channel_store import LiveChannelStore
from .movie_store import MovieStore
from .movie_info_reader import MovieInfoReader
from .series_store import SeriesStore
from .series_info_store import SeriesInfoStore
from .epg_store import EpgStore
import os
import time
import threading

try:
    import queue as _queue
except ImportError:
    import Queue as _queue

def _dbg(msg):

    try:
        with open(LOG("e2iptv_backend.log"), "a") as f:
            f.write(
                "%s  %s\n" %
                (
                    time.strftime("%H:%M:%S"),
                    msg
                )
            )
    except Exception:
        pass

class Backend(object):

    # -----------------------------------------------------------------

    def _ensure_loaded(self, store, category, loader, *args):

        if not store.has(category):
            loader(*args)

    # -----------------------------------------------------------------

    def _fetch_categories_parallel(self, categories, fetch_one, id_of, max_workers=6):
        """Runs fetch_one(category) across several categories at once
        instead of one HTTP round-trip at a time — used by
        get_all_movies()/get_all_series() below, where looping every
        category sequentially (needed for correctness — see those
        methods' comments) was otherwise too slow to be usable for a
        provider with many categories. Bounded concurrency rather than
        firing every category at once: some Xtream panels rate-limit
        or reject too many simultaneous connections from one account,
        so this caps it at a handful of workers rather than removing
        the limit entirely. XtreamClient itself is safe to call this
        way — each call opens its own connection with no shared state
        (see xtream_client.py) — so the only thing that actually needs
        a lock here is appending to the shared results list."""

        work = _queue.Queue()
        for c in categories:
            work.put(c)

        lock = threading.Lock()
        result = []
        seen_ids = set()

        def worker():
            while True:
                try:
                    category = work.get_nowait()
                except _queue.Empty:
                    return
                try:
                    items = fetch_one(category) or []
                except Exception:
                    items = []
                with lock:
                    for item in items:
                        item_id = id_of(item)
                        if item_id in seen_ids:
                            continue
                        seen_ids.add(item_id)
                        result.append(item)
                work.task_done()

        n = min(max_workers, max(1, len(categories)))
        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.daemon = True
            t.start()
        for t in threads:
            t.join()

        return result

    # -----------------------------------------------------------------

    def __init__(self):

        self.provider_store      = ProviderStore()
        self.category_store      = CategoryStore()
        self.live_channel_store  = LiveChannelStore()
        self.movie_store         = MovieStore()
        self.movie_info_reader   = MovieInfoReader()
        self.series_store        = SeriesStore()
        self.series_info_store   = SeriesInfoStore()
        self.epg_store           = EpgStore()

        # category_store is shared across live/movie/series category
        # types (only one type's categories live in it at a time).
        # This tracks which type is currently loaded, so a stale-check
        # based on provider_id alone can't mistake one type's cached
        # categories for another's after switching content types for
        # the same provider.
        self._category_store_content = None

    # -----------------------------------------------------------------

    def load_providers(self, path=None):

        return self.provider_store.load(path)

    # -----------------------------------------------------------------

    def load_live_categories(self, provider):

        _dbg(
            "LOAD LIVE provider=%s id=%s" %
            (
                provider.name,
                provider.provider_id
            )
        )

        count = self.category_store.load_live(provider)

        provider.live_categories_loaded = True
        self._category_store_content = "live"

        _dbg(
            "LOADED %d categories first_source=%s" %
            (
                count,
                self.category_store.all()[0].source_id
                if self.category_store.all()
                else "None"
            )
        )

        return count

    # -----------------------------------------------------------------

    def load_movie_categories(self, provider):

        count = self.category_store.load_vod(provider)

        provider.movie_categories_loaded = True
        self._category_store_content = "movies"

        return count

    # -----------------------------------------------------------------

    def load_series_categories(self, provider):

        count = self.category_store.load_series(provider)

        provider.series_categories_loaded = True
        self._category_store_content = "series"

        return count

    # -----------------------------------------------------------------

    def load_live_channels(self, provider, category):

        count = self.live_channel_store.load(provider, category)

        return count

    # -----------------------------------------------------------------

    def load_movies(self, provider, category):

        count = self.movie_store.load(provider, category)

        return count

    # -----------------------------------------------------------------

    def load_series(self, provider, category):

        count = self.series_store.load(provider, category)

        return count

    # -----------------------------------------------------------------

    def providers(self):

        return self.provider_store.all()

    # -----------------------------------------------------------------

    def check_provider(self, provider):
        if getattr(provider, "is_bouquet", False):
            return True
        """Cheap authenticated provider health check.

        This deliberately performs only one request against the
        provider — same call CategoryReader itself already makes for
        Live TV categories (get_live_categories for Xtream, get_genres
        for Stalker — see StalkerClient's own docstring), not a
        separate, lighter one, so a provider passing this check is a
        genuine guarantee the real category load will also work, not
        just that the server is reachable at all.
        """

        try:
            if provider.source_type == "STALKER":
                from .stalker_client import StalkerClient
                data = StalkerClient(provider).get_genres()
            else:
                from .xtream_client import XtreamClient
                client = XtreamClient(provider)
                data = client.call("get_live_categories")

            if isinstance(data, list):
                provider.online = True
                return True

            if isinstance(data, dict):
                provider.online = True
                return True

            provider.online = False
            return False

        except Exception as e:
            provider.online = False
            _dbg(
                "PROVIDER CHECK FAILED provider=%s error=%s" %
                (
                    getattr(provider, "name", ""),
                    e
                )
            )
            return False

    # -----------------------------------------------------------------

    def get_live_categories(self, provider):
        if getattr(provider, "is_bouquet", False):
            from .bouquets import categories
            return categories()

        _dbg(
            "GET LIVE provider=%s id=%s loaded=%s" % (
                provider.name,
                provider.provider_id,
                provider.live_categories_loaded
            )
        )

        # Fetch server info (timezone, server_timestamp) on first use
        if not provider.server_timestamp:
            try:
                self.provider_store.connect(provider)
            except Exception:
                pass

        if (
            not provider.live_categories_loaded or
            not self.category_store.all() or
            self.category_store.all()[0].source_id != provider.provider_id or
            self._category_store_content != "live"
        ):
            self.load_live_categories(provider)

        cats = self.category_store.all()

        if cats:

            _dbg(
                "RETURN provider=%s first_source=%s count=%d" % (
                    provider.provider_id,
                    cats[0].source_id,
                    len(cats)
                )
            )

        else:

            _dbg(
                "RETURN provider=%s no categories" % (
                    provider.provider_id,
                )
            )

        return cats


    # -----------------------------------------------------------------

    def get_movie_categories(self, provider):

        if (
            not provider.movie_categories_loaded or
            not self.category_store.all() or
            self.category_store.all()[0].source_id != provider.provider_id or
            self._category_store_content != "movies"
        ):
            self.load_movie_categories(provider)

        return self.category_store.all()

    # -----------------------------------------------------------------

    def get_series_categories(self, provider):

        if (
            not provider.series_categories_loaded or
            not self.category_store.all() or
            self.category_store.all()[0].source_id != provider.provider_id or
            self._category_store_content != "series"
        ):
            self.load_series_categories(provider)

        return self.category_store.all()

    # -----------------------------------------------------------------

    def categories(self):

        return self.category_store.all()

    # -----------------------------------------------------------------

    def get_live_channels(self, provider, category):
        if getattr(provider, "is_bouquet", False):
            from .bouquets import channels as _bouquet_channels
            from ..state import state as _state
            return _bouquet_channels(category, _state.providers)

        # Skip disabled providers and categories entirely
        if not getattr(provider, 'enabled', True):
            return []
        disabled = getattr(provider, 'disabled_cats', set())
        if category.category_id in disabled:
            return []
        from .private import hidden as _private_hidden
        if str(category.category_id) in _private_hidden(provider, "live"):
            return []

        key = "%s_%s" % (category.source_id, category.category_id)

        self._ensure_loaded(
            self.live_channel_store,
            key,
            self.load_live_channels,
            provider,
            category
        )

        # Your own channel order for this category (see channel_order.py)
        # — applied on every load, so it survives provider refreshes.
        channels = self.live_channel_store.get(key)
        try:
            from .channel_order import apply as _apply_channel_order
            return _apply_channel_order(provider, category, channels)
        except Exception:
            return channels

    # -----------------------------------------------------------------

    def channels(self):

        return self.live_channel_store.all()

    # -----------------------------------------------------------------

    def get_movies(self, provider, category):

        # Composite key — see get_live_channels just above, which
        # already does this correctly. category_id alone isn't unique
        # across providers (Xtream numbers each provider's categories
        # independently), so without source_id in the key, switching
        # provider while landing on a category_id that happens to
        # match one already cached from a *different* provider would
        # silently skip the fetch and return that other provider's
        # movies instead.
        key = "%s_%s" % (category.source_id, category.category_id)

        self._ensure_loaded(
            self.movie_store,
            key,
            self.load_movies,
            provider,
            category
        )

        return self.movie_store.get(key)

    # -----------------------------------------------------------------

    def movies(self):

        return self.movie_store.all()

    # -----------------------------------------------------------------

    def get_series(self, provider, category):

        # Same composite-key reasoning as get_movies just above.
        key = "%s_%s" % (category.source_id, category.category_id)

        self._ensure_loaded(
            self.series_store,
            key,
            self.load_series,
            provider,
            category
        )

        return self.series_store.get(key)

    # -----------------------------------------------------------------

    def get_all_movies(self, provider):
        """Every movie for this provider, uncategorized — used by
        Search (see search.py). Own cache slot, kept separate from any
        per-category entry get_movies() above populates.

        Always loops every category and aggregates, rather than
        relying on Xtream's "omit category_id = everything" convention
        — confirmed unreliable for at least one real provider, and the
        dangerous failure mode isn't an empty result (which a
        fallback could catch) but a silently *partial* one: some
        panels return a subset without category_id rather than
        nothing at all, which would just quietly search an incomplete
        catalogue with no sign anything was missing. Run in parallel
        (see _fetch_categories_parallel) rather than one category at a
        time — sequential was correct but far too slow to be usable
        for a provider with many categories."""

        key = "%s_ALL" % (provider.provider_id,)

        cached = self.movie_store.get(key)

        if cached:
            return cached

        result = self._fetch_categories_parallel(
            self.get_movie_categories(provider),
            lambda category: self.get_movies(provider, category),
            lambda movie: movie.stream_id
        )

        if result:
            self.movie_store.put(key, result)

        return result

    # -----------------------------------------------------------------

    def get_all_series(self, provider):
        """Every series for this provider, uncategorized — see
        get_all_movies()'s identical comment, including why this
        always loops categories (in parallel) rather than trying the
        no-category shortcut first."""

        key = "%s_ALL" % (provider.provider_id,)

        cached = self.series_store.get(key)

        if cached:
            return cached

        result = self._fetch_categories_parallel(
            self.get_series_categories(provider),
            lambda category: self.get_series(provider, category),
            lambda series: series.series_id
        )

        if result:
            self.series_store.put(key, result)

        return result

    # -----------------------------------------------------------------

    def get_series_info(self, provider, series):

        # Composite key — see SeriesInfoStore.load()'s comment.
        key = "%s_%s" % (series.source_id, series.series_id)

        if not self.series_info_store.has(key):
            self.series_info_store.load(provider, series)

        return self.series_info_store.get(key)[0]

    # -----------------------------------------------------------------

    def get_movie_info(self, provider, movie):
        """Lazily enriches a Movie with the plot/cast/genre detail the
        basic movie list doesn't carry (see MovieInfoReader). Cached
        on the movie object itself via info_loaded, so this only ever
        makes the network call once per movie per process lifetime —
        no separate store needed, unlike series_info_store, since a
        movie has nothing else (episodes/seasons) to cache alongside
        it."""

        if getattr(movie, "info_loaded", False):
            return True

        try:
            fields = self.movie_info_reader.read(provider, movie)
        except Exception:
            return False

        movie.plot         = fields.get("plot") or movie.plot
        movie.cast         = fields.get("cast") or movie.cast
        movie.director     = fields.get("director") or movie.director
        movie.genre        = fields.get("genre") or movie.genre
        movie.rating       = fields.get("rating") or movie.rating
        movie.release_date = fields.get("release_date") or movie.release_date
        movie.year         = fields.get("year") or movie.year
        movie.info_loaded  = True

        return True

    # -----------------------------------------------------------------

    def series(self):

        return self.series_store.all()

    # -----------------------------------------------------------------

    def get_epg(self, provider, channel):
        from .bouquets import real_provider
        provider = real_provider(provider, channel)
        if getattr(provider, "is_bouquet", False):
            return []      # bouquet channel whose provider is gone
        if not provider.server_timestamp:
            try:
                self.provider_store.connect(provider)
            except Exception:
                pass

        if not self.epg_store.has_for(channel):
            self.epg_store.load(provider, channel)

        return self.epg_store.get_for(channel)

    # -----------------------------------------------------------------
    # -----------------------------------------------------------------

    def find_provider(self, provider_id):

        for provider in self.providers():

            if provider.provider_id == provider_id:

                return provider

        return None

    # -----------------------------------------------------------------

    def find_live_channel(self, provider, stream_id):

        # source_id required — stream_id alone isn't unique across
        # providers (same reasoning as every other fix in this file:
        # Xtream numbers each provider's streams independently), and
        # this was matching purely on stream_id across every provider's
        # cached channels, arbitrarily returning whichever provider's
        # channel happened to be stored first. Used by the recording
        # resolver — a collision here doesn't just show the wrong
        # thing on screen, it could actually record the wrong channel.
        ch = self.live_channel_store.find_by_stream_id(
            getattr(provider, "provider_id", None),
            stream_id
        )
        if ch is not None:
            return ch
        # Not loaded since the box started (e.g. a repeat recording due
        # before the plugin has been opened): use the saved lists on disk,
        # then any of My Bouquets holding it.
        try:
            ch = self.live_channel_store.find_on_disk(provider, stream_id)
        except Exception:
            ch = None
        if ch is None:
            try:
                from .bouquets import BOUQUET_DIR, _FIELDS
                from .live_channel import LiveChannel
                import json as _json
                key = str(getattr(provider, "provider_key", ""))
                for n in os.listdir(BOUQUET_DIR):
                    if not n.endswith(".json"):
                        continue
                    with open(os.path.join(BOUQUET_DIR, n)) as f:
                        for e in _json.load(f):
                            if (str(e.get("provider_key")) == key
                                    and str(e.get("stream_id")) == str(stream_id)):
                                ch = LiveChannel()
                                for fld in _FIELDS:
                                    if fld in e:
                                        setattr(ch, fld, e[fld])
                                ch.stream_id = str(stream_id)
                                ch.provider_key = key
                                ch.category_id = e.get("src_category_id", "")
                                break
                    if ch is not None:
                        break
            except Exception:
                ch = None
        if ch is not None:
            ch.source_id = getattr(provider, "provider_id", ch.source_id)
        return ch

    # -----------------------------------------------------------------

    def find_epg_event(self, provider, channel, event_start, event_end):

        events = self.get_epg(
            provider,
            channel
        )

        #
        # Exact match first.
        #

        for event in events:

            if (
                event.start == event_start and
                event.end == event_end
            ):
                return event

        #
        # Fallback: return any event that overlaps the requested time.
        #

        for event in events:

            if (
                event.start < event_end and
                event.end > event_start
            ):
                return event

        return None
