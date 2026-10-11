"""Run one regular-expression check in a separately killable process."""

import json
import re
import sys


def main() -> None:
    """Read a regex request from standard input and emit its match result."""
    request = json.load(sys.stdin)
    matched = re.search(request["pattern"], request["text"]) is not None
    print(json.dumps(matched))


if __name__ == "__main__":
    main()
