The new guard in average() returns zero before division for an empty
sequence; non-empty sequences retain the existing arithmetic. The batch limit
changes from 100 to 250, so callers may submit larger batches and use more memory.
Validate both the accepted 250-item boundary and rejection of 251 items. The
callers enforcing the limit are outside this diff, so their correctness remains
unverified. This review addresses both changes without claiming they are safe.
