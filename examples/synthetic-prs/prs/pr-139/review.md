The context manager in `packet/header.py` covers the exception path that used to skip close. Returning from inside the context also closes the stream, and short reads are still returned directly.
