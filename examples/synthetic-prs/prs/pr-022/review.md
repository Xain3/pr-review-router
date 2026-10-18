- cli/help.js: the help string now says --output, matching the option the parser accepts. Only displayed text changes and parsing is not touched in this patch, so a user copying the example no longer gets an unknown-option error.

Open concern: Please confirm the parser registers the option as --output; this patch does not show it.
This needs an answer before merging.
