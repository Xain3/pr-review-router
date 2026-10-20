- src/cache.py: lookup now calls key.lower() before store.get, so mixed-case keys hit a lowercase store. Non-string keys will raise AttributeError, and a store holding distinct uppercase keys can no longer be reached through them.

Overall this looks fine.
