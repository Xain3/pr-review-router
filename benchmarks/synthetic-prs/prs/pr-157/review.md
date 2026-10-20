- src/client.py: fetch now requires a timeout argument with no default, so any existing fetch(url) call raises TypeError until it is updated. External callers will break.
- src/jobs.py: the one in-repo call is updated to pass 30, which preserves its previous timeout but does not help callers outside this patch.

Open concern: Please confirm whether a default or deprecation period is wanted before callers migrate.
This needs an answer before merging.
