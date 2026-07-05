#!/usr/bin/env python3
"""Tests for sources/build_feedback_digest.py (stdlib unittest).

Run from the repo root:
    python3 -m unittest discover tests -v

Covers the `## Tendencies` per-tag summary (counts, signed one-decimal
means, tags without rows omitted), the empty-window stub (byte-compared
so the pulse.sh guard's printf stays in sync), and the grouped section
structure that pulse.sh's census log line greps/awks over -- the exact
shell snippet is replicated in a subprocess test.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sources"))

import build_feedback_digest  # noqa: E402

# The stub pulse.sh writes when the digest file is missing. Must stay
# byte-identical to render([], 14).
EMPTY_STUB = "# Recent feedback (last 14 days)\n\n(no feedback in window)\n"

# Mirrors the census snippet in pulse.sh (step 4). If the shell changes,
# update this copy -- the test exists to catch drift in the digest's
# section structure that would silently zero the census.
CENSUS_SH = r"""
if grep -q '^(no feedback in window)$' "$1"; then
  printf 'empty'
else
  awk '
    /^## Valued /           {sec="v"; next}
    /^## Neutral /          {sec="n"; next}
    /^## Not valued /       {sec="x"; next}
    /^## Avoid candidates / {sec="a"; next}
    /^## /                  {sec="";  next}
    /^- / { if (sec=="v") v++; else if (sec=="n") n++;
            else if (sec=="x") x++; else if (sec=="a") a++ }
    END { printf "%d valued / %d neutral / %d not-valued / %d avoid", v, n, x, a }
  ' "$1"
fi
"""


def row(rating: int, tag: str = "tracked", title: str = "A card", note: str = "") -> dict:
    return {
        "date": "2026-07-01",
        "rating": rating,
        "tag": tag,
        "title": title,
        "note": note,
    }


def census(digest_text: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(digest_text)
        path = f.name
    try:
        out = subprocess.run(
            ["bash", "-c", CENSUS_SH, "census", path],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout
    finally:
        Path(path).unlink()


class TendenciesTests(unittest.TestCase):
    def test_counts_and_signed_mean(self) -> None:
        rows = [row(2), row(1), row(1), row(-1, tag="bridge")]
        lines = build_feedback_digest.tendencies(rows)
        self.assertEqual(
            lines,
            [
                "- tracked: 3 rated, mean +1.3",
                "- bridge: 1 rated, mean -1.0",
            ],
        )

    def test_zero_mean_carries_plus_sign(self) -> None:
        rows = [row(1), row(-1)]
        self.assertEqual(
            build_feedback_digest.tendencies(rows),
            ["- tracked: 2 rated, mean +0.0"],
        )

    def test_tags_without_rows_are_omitted(self) -> None:
        rows = [row(2, tag="adjacent"), row(0, tag="follow-up")]
        lines = build_feedback_digest.tendencies(rows)
        joined = "\n".join(lines)
        self.assertNotIn("tracked", joined)
        self.assertNotIn("bridge", joined)
        self.assertEqual(
            lines,
            [
                "- adjacent: 1 rated, mean +2.0",
                "- follow-up: 1 rated, mean +0.0",
            ],
        )

    def test_canonical_tag_order_then_unknown(self) -> None:
        rows = [row(1, tag="zzz-custom"), row(1, tag="bridge"), row(1, tag="tracked")]
        tags = [ln.split(":")[0] for ln in build_feedback_digest.tendencies(rows)]
        self.assertEqual(tags, ["- tracked", "- bridge", "- zzz-custom"])

    def test_untagged_rows_are_skipped(self) -> None:
        rows = [row(1, tag=""), row(1)]
        self.assertEqual(
            build_feedback_digest.tendencies(rows),
            ["- tracked: 1 rated, mean +1.0"],
        )

    def test_rendered_digest_places_tendencies_before_groups(self) -> None:
        text = build_feedback_digest.render([row(1)], 14)
        self.assertLess(
            text.index("## Tendencies"),
            text.index("## Valued (more like this)"),
        )


class EmptyWindowTests(unittest.TestCase):
    def test_stub_is_byte_identical(self) -> None:
        self.assertEqual(build_feedback_digest.render([], 14), EMPTY_STUB)

    def test_stub_has_no_tendencies_section(self) -> None:
        self.assertNotIn("Tendencies", build_feedback_digest.render([], 14))

    def test_census_reports_empty_on_stub(self) -> None:
        self.assertEqual(census(EMPTY_STUB), "empty")


class SectionStructureTests(unittest.TestCase):
    """pulse.sh's census greps these headings; keep them verbatim."""

    ROWS = [
        row(2, title="Great one", note="more of this"),
        row(1, tag="adjacent", title="Nice"),
        row(1, tag="bridge", title="Solid"),
        row(0, title="Meh"),
        row(-1, title="Off-target"),
        row(-2, title="Never again"),
    ]

    def test_headings_present_verbatim(self) -> None:
        text = build_feedback_digest.render(self.ROWS, 14)
        for heading in (
            "## Valued (more like this)",
            "## Neutral (reviewed, no strong opinion)",
            "## Not valued (less like this)",
            "## Avoid candidates (rated [--])",
        ):
            self.assertIn(f"\n{heading}\n", f"\n{text}")

    def test_census_counts_grouped_entries(self) -> None:
        # rating -2 rows appear in both Not valued and Avoid candidates,
        # so not-valued=2 (the -1 and the -2) and avoid=1.
        text = build_feedback_digest.render(self.ROWS, 14)
        self.assertEqual(census(text), "3 valued / 1 neutral / 2 not-valued / 1 avoid")

    def test_census_ignores_tendencies_lines(self) -> None:
        # Tendencies entries also start with "- " but sit under their own
        # heading; the awk's catch-all `/^## /` must reset the section.
        text = build_feedback_digest.render([row(1), row(1, tag="bridge")], 14)
        self.assertIn("## Tendencies", text)
        self.assertEqual(census(text), "2 valued / 0 neutral / 0 not-valued / 0 avoid")


if __name__ == "__main__":
    unittest.main()
