#!/usr/bin/env python3
"""Tests for the unattended (--auto) mode of sources/apply_updates.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "sources"))

import apply_updates  # noqa: E402

PROFILE = """# Durable interests

## Research interests

### General

-   Information theory and computational
    mechanics.
-   Proper scoring rules.

## Avoid

-   Crypto price news.
-   Celebrity gossip.
"""


def block(**fields: str) -> str:
    order = ("PROPOSAL", "Section", "Target", "Text", "Rationale", "Evidence")
    return "\n".join(f"{k}: {fields[k]}" for k in order if k in fields)


PROPOSALS = "\n---\n".join(
    [
        block(PROPOSAL="ADD", Section="### General", Text="-   Shapley attribution.", Rationale="r", Evidence="e"),
        block(PROPOSAL="ADD", Section="## Avoid", Text="-   Benchmark leaderboards.", Rationale="r", Evidence="e"),
        # Mislabeled: the target actually lives under ## Avoid.
        block(PROPOSAL="DEMOTE", Section="### General", Target="-   Crypto price news.", Text="remove"),
        block(
            PROPOSAL="EDIT",
            Section="### General",
            Target="-   Information theory and computational mechanics.",
            Text="-   Information theory and computational mechanics (epsilon-machines).",
        ),
        block(PROPOSAL="DEMOTE", Section="### General", Target="-   Not in the profile.", Text="remove"),
    ]
)


class TestTouchesAvoid(unittest.TestCase):
    def setUp(self):
        self.lines = PROFILE.splitlines()

    def test_add_into_avoid(self):
        self.assertTrue(apply_updates.touches_avoid(self.lines, {"type": "ADD", "section": "## Avoid"}))

    def test_add_elsewhere(self):
        self.assertFalse(apply_updates.touches_avoid(self.lines, {"type": "ADD", "section": "### General"}))

    def test_target_located_in_avoid_despite_label(self):
        p = {"type": "DEMOTE", "section": "### General", "target": "-   Celebrity gossip."}
        self.assertTrue(apply_updates.touches_avoid(self.lines, p))

    def test_wrapped_target_outside_avoid(self):
        p = {"type": "EDIT", "section": "### General", "target": "-   Information theory and computational mechanics."}
        self.assertFalse(apply_updates.touches_avoid(self.lines, p))

    def test_profile_without_avoid(self):
        lines = PROFILE.split("## Avoid")[0].splitlines()
        self.assertFalse(apply_updates.touches_avoid(lines, {"type": "ADD", "section": "### General"}))


class TestEditKeepsNesting(unittest.TestCase):
    def test_sub_bullet_stays_nested(self):
        lines = [
            "## Active tooling",
            "",
            "-   Decks:",
            "    -   Math deck.",
            "    -   Card generation: ad hoc prompt,",
            "        after retiring the pipeline.",
            "    -   Decks are healthy.",
        ]
        new, ok, _ = apply_updates.apply_edit(
            lines,
            "-   Card generation: ad hoc prompt, after retiring the pipeline.",
            "-   Card generation: a shared skill, after retiring the pipeline.",
        )
        self.assertTrue(ok)
        self.assertEqual(new[4], "    -   Card generation: a shared skill, after retiring the pipeline.")
        self.assertEqual(new[5], "    -   Decks are healthy.")

    def test_top_level_edit_unchanged(self):
        new, ok, _ = apply_updates.apply_edit(["-   Old."], "-   Old.", "-   New.")
        self.assertTrue(ok)
        self.assertEqual(new, ["-   New."])


class TestAutoCli(unittest.TestCase):
    def run_auto(self, proposals: str, *extra: str) -> tuple[Path, dict]:
        tmp = Path(tempfile.mkdtemp(prefix="pulse-apply-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "interests.md").write_text(PROFILE)
        (tmp / "proposals.md").write_text(proposals)
        report = tmp / "out" / "profile" / "2026-01-04-0500.json"
        subprocess.run(
            [
                sys.executable,
                str(REPO / "sources" / "apply_updates.py"),
                "--auto",
                "--proposals", str(tmp / "proposals.md"),
                "--profile", str(tmp / "interests.md"),
                "--history-dir", str(tmp / "history"),
                "--report", str(report),
                "--run-id", "2026-01-04-0500",
                "--model", "test-model",
                *extra,
            ],
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
        )
        return tmp, json.loads(report.read_text())

    def test_applies_all_but_avoid_and_reports(self):
        tmp, report = self.run_auto(PROPOSALS)
        profile = (tmp / "interests.md").read_text()
        self.assertIn("-   Shapley attribution.", profile)
        self.assertIn("(epsilon-machines)", profile)
        self.assertIn("-   Crypto price news.", profile)  # Avoid untouched
        self.assertNotIn("Benchmark leaderboards", profile)
        self.assertEqual([p["type"] for p in report["applied"]], ["ADD", "EDIT"])
        self.assertEqual(len(report["left_for_reader"]), 2)
        self.assertEqual(len(report["unmatched"]), 1)
        self.assertEqual(report["model"], "test-model")
        self.assertTrue(Path(report["snapshot"]).read_text() == PROFILE)
        self.assertIn("+-   Shapley attribution.", report["diff"])

    def test_no_proposals_still_writes_report(self):
        tmp, report = self.run_auto("NO PROPOSALS: profile is current.\n")
        self.assertEqual(report["proposals"], 0)
        self.assertEqual(report["applied"], [])
        self.assertEqual(report["diff"], "")
        self.assertEqual((tmp / "interests.md").read_text(), PROFILE)
        self.assertFalse((tmp / "history").exists())


if __name__ == "__main__":
    unittest.main()
