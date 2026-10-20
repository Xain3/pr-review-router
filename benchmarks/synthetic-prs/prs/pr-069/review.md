- src/users.py: user_response returns full_name instead of name, so clients reading name lose the field. No alias or migration period is provided, which makes this a breaking API change.

Overall this looks fine.
