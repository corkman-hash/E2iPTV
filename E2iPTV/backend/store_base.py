# =============================================================================
# IE2iPTV Backend
# Store Base Class
# =============================================================================


class StoreBase(object):

    def __init__(self):

        # Legacy sequential storage
        self.items = []

        # Keyed cache storage
        self.cache = {}

    # -------------------------------------------------------------
    # Legacy list API
    # -------------------------------------------------------------

    def add(self, item):

        self.items.append(item)

    def extend(self, items):

        self.items.extend(items)

    def all(self):

        return self.items

    def first(self, key=None):

        if key is None:
            return self.items[0] if self.items else None

        items = self.get(key)
        return items[0] if items else None

    def last(self, key=None):

        if key is None:
            return self.items[-1] if self.items else None

        items = self.get(key)
        return items[-1] if items else None

    # -------------------------------------------------------------
    # Cache API
    # -------------------------------------------------------------

    def put(self, key, items):

        self.cache[str(key)] = list(items)

    def get(self, key):

        return self.cache.get(str(key), [])

    def has(self, key):

        return str(key) in self.cache

    def remove(self, key):

        self.cache.pop(str(key), None)

    def keys(self):

        return sorted(self.cache.keys())

    # -------------------------------------------------------------

    def clear(self):

        self.items = []
        self.cache.clear()

    # -------------------------------------------------------------

    def count(self, key=None):

        if key is None:

            if self.cache:
                return sum(len(v) for v in self.cache.values())

            return len(self.items)

        return len(self.get(key))
