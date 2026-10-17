- src/names.py: clean now returns name.strip(), so leading and trailing whitespace is removed. Non-string inputs raise AttributeError, and names that differed only by surrounding whitespace now compare equal.

Overall this looks fine.
