- src/stats.py: the new guard returns 0 for an empty list before the division, so [] no longer raises ZeroDivisionError. Callers that relied on that exception now receive 0, which is a behavior change worth noting.

Overall this looks fine.
