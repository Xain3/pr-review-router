- src/totals.py: the local variable t is renamed to running_total at its initialization, the accumulation, and the return. All three uses are updated and the signature of total() is unchanged, so every input gives the same result.

Open concern: The rename is local to total(); please confirm the longer name satisfies the project's line-length lint.
This needs an answer before merging.
