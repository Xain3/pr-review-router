- src/export.py: csv_text writes rows through csv.writer with LF line endings and returns the string, so fields containing commas are quoted. No header is added and formula escaping is not handled.
- tests/test_export.py: the tests cover a comma-containing field, expected to be quoted, and an empty list, expected to give an empty string.

Open concern: Spreadsheet formula injection (cells starting with =, +, - or @) is not mitigated; please decide whether that is in scope.
This needs an answer before merging.
