import pytest

from pr_review_router.patches import parse_patch


def test_file_headers_multiple_hunks_and_no_newline_marker():
    patch = (
        "diff --git a/demo.py b/demo.py\n"
        "index 123..456 100644\n"
        "--- a/demo.py\n"
        "+++ b/demo.py\n"
        "@@ -3,2 +3,2 @@\n"
        " context\n"
        "-old\n"
        "+new\n"
        "@@ -10 +10,2 @@\n"
        "-last\n"
        "+first\n"
        "+second\n"
        "\\ No newline at end of file\n"
    )
    hunks = parse_patch(patch)
    assert hunks[0].removed == ["old"]
    assert hunks[0].added == [(4, "new")]
    assert hunks[1].added == [(10, "first"), (11, "second")]


@pytest.mark.parametrize(
    ("patch", "removed", "added"),
    [
        ("@@ -0,0 +1 @@\n+new\n", [], [(1, "new")]),
        ("@@ -1 +0,0 @@\n-old\n", ["old"], []),
        ("@@ -1 +1 @@\n---- text\n++++ text\n", ["--- text"], [(1, "+++ text")]),
    ],
)
def test_new_removed_and_header_like_content(patch, removed, added):
    hunk = parse_patch(patch)[0]
    assert hunk.removed == removed
    assert hunk.added == added


@pytest.mark.parametrize(
    "patch",
    [
        "@@ -0 +1 @@\n-old\n+new\n",
        "@@ -1 +0 @@\n-old\n+new\n",
        "@@ -0 +0 @@\n-old\n+new\n",
    ],
)
def test_nonempty_ranges_cannot_start_at_zero(patch):
    with pytest.raises(ValueError, match="line zero"):
        parse_patch(patch)
