- src/batch.py: MAX_BATCH_SIZE rises from 50 to 100 and the <= comparison is unchanged, so lengths 51 to 100 that were rejected are now accepted. Peak memory per batch can grow accordingly.

Overall this looks fine.
