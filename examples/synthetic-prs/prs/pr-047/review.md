- src/auth.py: == is replaced by hmac.compare_digest, which avoids an early-exit comparison. It needs matching argument types and, for str, ASCII-only text; otherwise it raises TypeError, so callers passing non-ASCII strings or mixed bytes and str will now fail.

Overall this looks fine.
