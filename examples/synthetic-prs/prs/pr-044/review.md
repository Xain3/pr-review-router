- src/stats.py: the new guard returns 0 for an empty list before the division, so [] no longer raises ZeroDivisionError. Callers that relied on that exception now receive 0, which is a behavior change worth noting.
- tests/test_stats.py: the new tests cover [] returning 0 and [2, 4] returning 3, pinning both the new edge case and the ordinary path.

Open concern: A returned 0 can be confused with a real average of zero; please confirm downstream reports accept that.
This needs an answer before merging.
