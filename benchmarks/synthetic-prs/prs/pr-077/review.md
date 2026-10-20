- src/auth.py: == is replaced by hmac.compare_digest, which avoids an early-exit comparison. It needs matching argument types and, for str, ASCII-only text; otherwise it raises TypeError, so callers passing non-ASCII strings or mixed bytes and str will now fail.
- tests/test_auth.py: matching and mismatching ASCII tokens are covered, but the tests cannot show timing behavior or non-ASCII inputs.

Open concern: Please confirm that no caller passes non-ASCII or bytes tokens.
This needs an answer before merging.
