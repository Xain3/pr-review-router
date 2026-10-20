- src/export.py: csv_text writes rows through csv.writer with LF line endings and returns the string, so fields containing commas are quoted. No header is added and formula escaping is not handled.

Overall this looks fine.
