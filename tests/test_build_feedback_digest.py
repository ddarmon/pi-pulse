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

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
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


class DeliveryWindowTests(unittest.TestCase):
    """load_rows windows on the delivery date parsed from run_id."""

    def _write(self, rows: list[dict]) -> Path:
        f = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False
        )
        for r in rows:
            f.write(json.dumps(r) + "\n")
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return Path(f.name)

    def test_windows_on_run_id_not_rating_date(self) -> None:
        # A bulk rating session on 2026-07-06 rates a May brief. The
        # rating `date` is inside a 14-day window, but the May delivery
        # date is not -- so the row must be excluded.
        ledger = self._write(
            [
                {"run_id": "2026-05-17", "title": "old", "rating": 1, "tag": "tracked", "date": "2026-07-06"},
            ]
        )
        rows = build_feedback_digest.load_rows(ledger, date(2026, 7, 1))
        self.assertEqual(rows, [])

    def test_hhmm_run_id_form_parses(self) -> None:
        ledger = self._write(
            [
                {"run_id": "2026-07-05-0514", "title": "fresh", "rating": 1, "tag": "tracked", "date": "2026-07-06"},
            ]
        )
        rows = build_feedback_digest.load_rows(ledger, date(2026, 7, 1))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["_delivery"], date(2026, 7, 5))

    def test_falls_back_to_date_when_run_id_missing_or_bad(self) -> None:
        ledger = self._write(
            [
                {"title": "no-runid", "rating": 1, "tag": "tracked", "date": "2026-07-05"},
                {"run_id": "not-a-date", "title": "bad-runid", "rating": 1, "tag": "tracked", "date": "2026-07-04"},
            ]
        )
        rows = build_feedback_digest.load_rows(ledger, date(2026, 7, 1))
        self.assertEqual({r["title"] for r in rows}, {"no-runid", "bad-runid"})
        by_title = {r["title"]: r["_delivery"] for r in rows}
        self.assertEqual(by_title["no-runid"], date(2026, 7, 5))
        self.assertEqual(by_title["bad-runid"], date(2026, 7, 4))

    def test_display_shows_delivery_date_not_rating_date(self) -> None:
        ledger = self._write(
            [
                {"run_id": "2026-07-05-0514", "title": "fresh", "rating": 1, "tag": "tracked", "date": "2026-07-06"},
            ]
        )
        rows = build_feedback_digest.load_rows(ledger, date(2026, 7, 1))
        line = build_feedback_digest.fmt(rows[0])
        self.assertIn("[2026-07-05]", line)
        self.assertNotIn("2026-07-06", line)


class TruncationTests(unittest.TestCase):
    def _rows(self, n: int) -> list[dict]:
        # n valued rows on distinct delivery dates, plus one neutral.
        rows = []
        for i in range(n):
            rows.append(
                {
                    "run_id": f"2026-07-{i + 1:02d}",
                    "title": f"card {i}",
                    "rating": 1,
                    "tag": "tracked",
                    "_delivery": date(2026, 7, i + 1),
                }
            )
        return rows

    def test_section_truncates_and_reports_hidden_count(self) -> None:
        text = build_feedback_digest.render(self._rows(5), 14, max_per_section=2)
        valued = text.split("## Valued")[1].split("##")[0]
        # 2 shown + the overflow line.
        self.assertEqual(valued.count("\n- "), 3)
        self.assertIn("- (... and 3 more not shown)", valued)

    def test_no_overflow_line_when_under_cap(self) -> None:
        text = build_feedback_digest.render(self._rows(2), 14, max_per_section=5)
        self.assertNotIn("more not shown", text)

    def test_truncation_keeps_newest_delivery_first(self) -> None:
        text = build_feedback_digest.render(self._rows(5), 14, max_per_section=2)
        # Newest two delivery dates are 07-05 and 07-04.
        self.assertIn("[2026-07-05]", text)
        self.assertIn("[2026-07-04]", text)
        self.assertNotIn("[2026-07-01]", text)

    def test_tendencies_computed_over_all_rows_not_truncated(self) -> None:
        text = build_feedback_digest.render(self._rows(5), 14, max_per_section=2)
        tend = text.split("## Tendencies")[1].split("##")[0]
        self.assertIn("tracked: 5 rated", tend)

    def test_default_zero_is_unlimited(self) -> None:
        text = build_feedback_digest.render(self._rows(5), 14)
        self.assertNotIn("more not shown", text)
        self.assertEqual(census(text), "5 valued / 0 neutral / 0 not-valued / 0 avoid")


class DroppedRowFilterTests(unittest.TestCase):
    def test_dropped_rows_excluded_from_load(self) -> None:
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for r in (
            {"run_id": "2026-07-05", "title": "Dropped from this run", "rating": 1, "tag": "tracked", "date": "2026-07-05"},
            {"run_id": "2026-07-05", "title": "  Dropped from this run  ", "rating": -1, "tag": "tracked", "date": "2026-07-05"},
            {"run_id": "2026-07-05", "title": "Real card", "rating": 1, "tag": "tracked", "date": "2026-07-05"},
        ):
            f.write(json.dumps(r) + "\n")
        f.close()
        self.addCleanup(Path(f.name).unlink)
        rows = build_feedback_digest.load_rows(Path(f.name), date(2026, 7, 1))
        self.assertEqual([r["title"] for r in rows], ["Real card"])

    def test_dropped_rows_absent_from_render_and_tendencies(self) -> None:
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for r in (
            {"run_id": "2026-07-05", "title": "Dropped from this run", "rating": 1, "tag": "tracked", "date": "2026-07-05"},
            {"run_id": "2026-07-05", "title": "Real card", "rating": 1, "tag": "tracked", "date": "2026-07-05"},
        ):
            f.write(json.dumps(r) + "\n")
        f.close()
        self.addCleanup(Path(f.name).unlink)
        rows = build_feedback_digest.load_rows(Path(f.name), date(2026, 7, 1))
        text = build_feedback_digest.render(rows, 14)
        self.assertNotIn("Dropped from this run", text)
        self.assertIn("tracked: 1 rated", text)


if __name__ == "__main__":
    unittest.main()
