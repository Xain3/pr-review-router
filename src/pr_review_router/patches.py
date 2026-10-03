"""Validate unified diff coverage without applying or executing PR content."""

import re
from dataclasses import dataclass, field

_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*$")


@dataclass
class Hunk:
    removed: list[str] = field(default_factory=list)
    added: list[tuple[int, str]] = field(default_factory=list)
    lines: list[tuple[str, str]] = field(default_factory=list)
    old_start: int = 0


def parse_patch(patch: str) -> list[Hunk]:
    """Require complete hunk counts. File headers are optional in exported patches."""
    hunks: list[Hunk] = []
    old_remaining = new_remaining = 0
    new_line = 0
    for line in patch.removesuffix("\n").split("\n"):
        header = _HEADER.fullmatch(line)
        if header:
            if old_remaining or new_remaining:
                raise ValueError("incomplete diff hunk")
            old_remaining = int(header[2]) if header[2] is not None else 1
            new_remaining = int(header[4]) if header[4] is not None else 1
            old_start = int(header[1])
            new_start = int(header[3])
            if (old_start == 0 and old_remaining != 0) or (new_start == 0 and new_remaining != 0):
                raise ValueError("nonempty diff range starts at line zero")
            new_line = new_start
            hunks.append(Hunk(old_start=old_start))
            continue
        if line == "\\ No newline at end of file":
            continue
        if not hunks:
            if line.startswith(
                (
                    "diff --git ",
                    "index ",
                    "--- ",
                    "+++ ",
                    "new file mode ",
                    "deleted file mode ",
                    "old mode ",
                    "new mode ",
                    "similarity index ",
                    "dissimilarity index ",
                    "rename from ",
                    "rename to ",
                )
            ):
                continue
            raise ValueError("unsupported diff content")
        if not line or line[0] not in " +-":
            raise ValueError("unsupported diff content")
        prefix, content = line[0], line[1:]
        if prefix in " -":
            old_remaining -= 1
        if prefix in " +":
            new_remaining -= 1
        if old_remaining < 0 or new_remaining < 0:
            raise ValueError("diff hunk exceeds declared counts")
        if prefix == "-":
            hunks[-1].removed.append(content)
        elif prefix == "+":
            hunks[-1].added.append((new_line, content))
        hunks[-1].lines.append((prefix, content))
        if prefix in " +":
            new_line += 1
    if not hunks or old_remaining or new_remaining:
        raise ValueError("missing or incomplete diff hunks")
    return hunks
