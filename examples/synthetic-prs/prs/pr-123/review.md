- src/render.py: cell now returns html.escape(text), so <, >, & and quotes become entities before output. Rendered output changes for any text containing those characters, and the function becomes a security-relevant boundary against markup injection.
- tests/test_render.py: the test checks that '<b>' becomes '&lt;b&gt;' but does not cover already-escaped text, which would be escaped twice.

Open concern: Double escaping is possible if callers already escape their input.
This needs an answer before merging.
