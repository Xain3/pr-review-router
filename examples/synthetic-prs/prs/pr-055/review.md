- src/cache.py: lookup now calls key.lower() before store.get, so mixed-case keys hit a lowercase store. Non-string keys will raise AttributeError, and a store holding distinct uppercase keys can no longer be reached through them.
- tests/test_cache.py: the test checks that 'USER' and 'user' both resolve to 7 in a lowercase-key store; it does not cover a store with uppercase keys.

Open concern: Please confirm writers always store lowercase keys, since only lookup is normalized.
This needs an answer before merging.
