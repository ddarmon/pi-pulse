"""Security-regression tests for the lethal-trifecta hardening."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "sources"))

import audit_egress  # noqa: E402
import log_capability  # noqa: E402
import privacy  # noqa: E402
import prune_history  # noqa: E402
import url_policy  # noqa: E402


class PrivacyScrubTests(unittest.TestCase):
    def test_redacts_email_home_path_and_key_shapes(self) -> None:
        source = (
            "Contact david@example.com about /Users/david/private/project.\n"
            "BRAVE_API_KEY=brv-abcdefghijklmnopqrstuvwxyz123456\n"
        )
        scrubbed, counts = privacy.redact_text(source)
        self.assertNotIn("david@example.com", scrubbed)
        self.assertNotIn("/Users/david", scrubbed)
        self.assertNotIn("brv-abcdefghijklmnopqrstuvwxyz", scrubbed)
        self.assertIn("[redacted-email]", scrubbed)
        self.assertIn("[redacted-home-path]", scrubbed)
        self.assertGreaterEqual(sum(counts.values()), 3)

    def test_plain_public_topics_are_unchanged(self) -> None:
        source = "- Reading numerical linear algebra and Rust release notes.\n"
        scrubbed, counts = privacy.redact_text(source)
        self.assertEqual(scrubbed, source)
        self.assertEqual(counts, {})

    def test_author_handle_urls_are_not_mangled_as_emails(self) -> None:
        source = (
            "https://medium.com/@michael.hannecke/on-device-llm-runtimes\n"
            "https://www.youtube.com/@3blue1brown/videos\n"
        )
        scrubbed, counts = privacy.redact_text(source)
        self.assertEqual(scrubbed, source)
        self.assertEqual(counts, {})

    def test_redacts_bsa_key_and_prefixed_assignment(self) -> None:
        source = (
            "BRAVE_API_KEY=BSAabcdefghijklmnopqrstuvwx\n"
            "MY_ACCESS_TOKEN: hunter2secret\n"
        )
        scrubbed, counts = privacy.redact_text(source)
        self.assertNotIn("BSAabcdefghijklmnopqrstuvwx", scrubbed)
        self.assertNotIn("hunter2secret", scrubbed)
        self.assertIn("MY_ACCESS_TOKEN", scrubbed)
        self.assertGreaterEqual(sum(counts.values()), 2)


class UrlPolicyTests(unittest.TestCase):
    def test_accepts_normal_public_links(self) -> None:
        self.assertEqual(
            url_policy.validate_public_url("https://example.com/paper?q=linear+algebra"),
            "https://example.com/paper?q=linear+algebra",
        )

    def test_rejects_dangerous_url_shapes(self) -> None:
        bad = (
            "javascript:alert(1)",
            "https://user:pass@example.com/",
            "http://127.0.0.1:8765/",
            "http://localhost/",
            "https://example.com:8443/",
            "https://<domain",
            "https://example.com/?q=" + "x" * 513,
        )
        for value in bad:
            with self.subTest(value=value), self.assertRaises(url_policy.UrlPolicyError):
                url_policy.validate_public_url(value)


class SplitPlanManifestTests(unittest.TestCase):
    def run_split(self, plan: str, signals: str) -> subprocess.CompletedProcess[str]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        plan_path = root / "plan.md"
        signals_path = root / "signals.md"
        out_dir = root / "expand"
        plan_path.write_text(plan)
        signals_path.write_text(signals)
        env = os.environ.copy()
        env["RUN_ID"] = "2026-08-08-0500"
        self.last_out_dir = out_dir
        return subprocess.run(
            [
                sys.executable,
                str(REPO / "sources" / "split_plan.py"),
                str(plan_path),
                str(out_dir),
                "--signals",
                str(signals_path),
            ],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_manifest_has_slot_tag_and_verified_url(self) -> None:
        plan = """# Plan 2026-08-08

**Today's theme:** Safe handoffs

## Card 1 (tracked)
- **Title:** Example
- **Source URL:** https://example.com/paper
"""
        signals = """# Signals 2026-08-08

## Signal S1
- url: https://example.com/paper
"""
        result = self.run_split(plan, signals)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "01\ttracked\thttps://example.com/paper\n")

    def test_invalid_committed_url_is_dropped(self) -> None:
        plan = """# Plan 2026-08-08

**Today's theme:** Safe handoffs

## Card 1 (tracked)
- **Title:** Local target
- **Source URL:** http://127.0.0.1:8765/
"""
        signals = """# Signals 2026-08-08

## Signal S1
- url: http://127.0.0.1:8765/
"""
        result = self.run_split(plan, signals)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("invalid Source URL", result.stderr)

    def test_missing_url_is_dropped_even_without_relying_on_signal_check(self) -> None:
        plan = """# Plan 2026-08-08

**Today's theme:** Safe handoffs

## Card 1 (tracked)
- **Title:** Missing target
"""
        result = self.run_split(plan, "# Signals 2026-08-08\n")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("no Source URL", result.stderr)

    def test_slot_is_scrubbed_before_expand_receives_it(self) -> None:
        plan = """# Plan 2026-08-08

**Today's theme:** Safe handoffs

## Card 1 (tracked)
- **Title:** Example
- **Source URL:** https://example.com/paper
- **Rationale:** Contact david@example.com from /Users/david/private.
"""
        signals = """# Signals 2026-08-08

## Signal S1
- url: https://example.com/paper
"""
        result = self.run_split(plan, signals)
        self.assertEqual(result.returncode, 0, result.stderr)
        slot = (self.last_out_dir / "01" / "slot.md").read_text()
        self.assertNotIn("david@example.com", slot)
        self.assertNotIn("/Users/david", slot)
        self.assertIn("[redacted-email]", slot)


class SignalFilterTests(unittest.TestCase):
    def test_private_url_is_filtered_without_echoing_it_to_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            signals = root / "signals.md"
            signals.write_text(
                "# Signals 2026-08-08\n\n"
                "## Signal S1\n"
                "- url: https://example.com/david@example.com\n"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO / "sources" / "filter_signals.py"),
                    str(signals),
                    "--seen",
                    str(root / "seen.jsonl"),
                    "--unfetchable",
                    str(root / "unfetchable.jsonl"),
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("private-url:email", result.stderr)
            self.assertNotIn("david@example.com", result.stderr)


class PipelineFlagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pulse = (REPO / "pulse.sh").read_text()
        cls.expand = (REPO / "sources" / "expand_slot.sh").read_text()
        cls.suggest = (REPO / "scripts" / "suggest-profile.sh").read_text()

    def test_pure_synthesis_stages_are_sealed(self) -> None:
        sealed = "--no-tools --no-context-files --no-extensions --no-skills"
        self.assertGreaterEqual(self.pulse.count(sealed), 2)  # distill + plan
        self.assertIn(sealed, self.expand)
        self.assertIn(sealed, self.suggest)

    def test_scout_has_only_broker_tools(self) -> None:
        self.assertIn("--no-builtin-tools --tools search,fetch", self.pulse)
        self.assertIn('--extension "$SCOUT_EXTENSION"', self.pulse)
        self.assertNotIn("PI_PULSE_BRAVE_DIR", self.pulse)

    def test_brave_key_is_removed_from_model_environments(self) -> None:
        self.assertGreaterEqual(self.pulse.count("env -u BRAVE_API_KEY"), 3)
        self.assertIn("env -u BRAVE_API_KEY pi", self.expand)
        self.assertNotIn("set -a", self.pulse)
        self.assertNotIn("set -a", self.suggest)

    def test_all_manifest_readers_take_three_fields(self) -> None:
        readers = [line for line in self.pulse.splitlines() if "read -r slot_id" in line]
        self.assertEqual(len(readers), 3)
        self.assertTrue(all("_slot_url" in line for line in readers))

    def test_retention_is_opt_in(self) -> None:
        self.assertIn('RETENTION_DAYS="${PI_PULSE_RETENTION_DAYS:-0}"', self.pulse)

    def test_sensitive_files_are_restricted(self) -> None:
        self.assertIn("umask 077", self.pulse)
        self.assertIn(".env memory/interests.md memory/feedback.jsonl", self.pulse)
        self.assertIn('chmod 600 "$sensitive_file"', self.pulse)

    def test_exact_invocations_are_logged_for_continuous_audit(self) -> None:
        self.assertIn("PI_PULSE_CAPABILITY_LOG", self.pulse)
        self.assertIn('log_capability.py "$label"', self.pulse)
        self.assertIn("log_capability.py expand", self.expand)

    def test_brief_is_published_only_after_audit_passes(self) -> None:
        audit_gate = self.pulse.index('if [[ "$audit_status" != "pass" ]]')
        publish = self.pulse.index('mv "$PENDING_OUT" "$OUT"')
        self.assertLess(audit_gate, publish)
        self.assertNotIn('} > "$OUT"', self.pulse)


class RetentionTests(unittest.TestCase):
    def test_cli_requires_explicit_days(self) -> None:
        result = subprocess.run(
            [sys.executable, str(REPO / "sources" / "prune_history.py"), "--dry-run"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--days", result.stderr)

    def test_only_old_date_shaped_history_is_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            logs = root / "logs"
            sessions = root / "sessions"
            for path in (
                logs / "2026-01-01-0500",
                logs / "2026-08-07-0500",
                logs / "keep-me",
                sessions / "2026-01-02-0500",
                sessions / "suggest" / "2026-01-03-0500",
                sessions / "interview" / "2026-08-07-0500",
            ):
                path.mkdir(parents=True)
                (path / "evidence.txt").write_text("keep or prune")
            removed = prune_history.prune(
                logs,
                sessions,
                days=30,
                now=datetime(2026, 8, 8, 12, 0),
                dry_run=False,
            )
            self.assertEqual(len(removed), 3)
            self.assertFalse((logs / "2026-01-01-0500").exists())
            self.assertFalse((sessions / "2026-01-02-0500").exists())
            self.assertFalse((sessions / "suggest" / "2026-01-03-0500").exists())
            self.assertTrue((logs / "2026-08-07-0500").is_dir())
            self.assertTrue((logs / "keep-me").is_dir())
            self.assertTrue((sessions / "interview" / "2026-08-07-0500").is_dir())

    def test_explicit_exclusion_preserves_an_old_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            logs = Path(temp) / "logs"
            sessions = Path(temp) / "sessions"
            current = logs / "2020-01-01-0500"
            current.mkdir(parents=True)
            removed = prune_history.prune(
                logs,
                sessions,
                days=30,
                now=datetime(2026, 8, 8, 12, 0),
                dry_run=False,
                exclude=frozenset({current.name}),
            )
            self.assertEqual(removed, [])
            self.assertTrue(current.is_dir())


class CapabilityEvidenceTests(unittest.TestCase):
    def test_cli_records_expand_slot_through_real_argv(self) -> None:
        # Regression: argparse.REMAINDER matches positionals greedily, so
        # `expand --slot NN -- pi ...` would swallow `--slot NN` into the
        # command and record slot=None, which the egress audit rejects.
        # Exercise the actual CLI, not just the parser helpers.
        with tempfile.TemporaryDirectory() as d:
            log_path = Path(d) / "capabilities.jsonl"
            env = dict(os.environ, PI_PULSE_CAPABILITY_LOG=str(log_path))
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO / "sources" / "log_capability.py"),
                    "expand",
                    "--slot",
                    "03",
                    "--",
                    "env",
                    "-u",
                    "BRAVE_API_KEY",
                    "pi",
                    "-p",
                    "prompt",
                    "--no-tools",
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            record = json.loads(log_path.read_text())
            self.assertEqual(record["stage"], "expand")
            self.assertEqual(record["slot"], "03")
            self.assertTrue(record["no_tools"])

    def test_command_parser_records_flags_but_not_prompt_text(self) -> None:
        evidence = log_capability.parse_pi_command(
            [
                "env",
                "-u",
                "BRAVE_API_KEY",
                "pi",
                "-p",
                "private prompt text",
                "--provider",
                "test-provider",
                "--model",
                "test-model",
                "--no-tools",
                "--no-context-files",
                "--no-extensions",
                "--no-skills",
            ]
        )
        self.assertTrue(evidence["no_tools"])
        self.assertEqual(evidence["provider"], "test-provider")
        self.assertNotIn("private prompt text", json.dumps(evidence))

    def test_audit_requires_exact_stage_capabilities(self) -> None:
        common = {
            "no_context_files": True,
            "no_extensions": True,
            "no_skills": True,
            "provider": "test",
            "model": "model",
        }
        records = [
            {
                **common,
                "_line": 1,
                "stage": "distill",
                "slot": None,
                "no_tools": True,
                "no_builtin_tools": False,
                "tools": [],
                "extensions": [],
                "skills": [],
            },
            {
                **common,
                "_line": 2,
                "stage": "scout",
                "slot": None,
                "no_tools": False,
                "no_builtin_tools": True,
                "tools": ["search", "fetch"],
                "extensions": [str(REPO / "sources" / "brave-guard" / "scout.ts")],
                "skills": [],
            },
            {
                **common,
                "_line": 3,
                "stage": "plan",
                "slot": None,
                "no_tools": True,
                "no_builtin_tools": False,
                "tools": [],
                "extensions": [],
                "skills": [],
            },
            {
                **common,
                "_line": 4,
                "stage": "expand",
                "slot": "01",
                "no_tools": True,
                "no_builtin_tools": False,
                "tools": [],
                "extensions": [],
                "skills": [],
            },
        ]
        self.assertEqual(
            audit_egress.audit_capabilities(records, {"01": "https://example.com"}),
            [],
        )
        records[2]["no_tools"] = False
        violations = audit_egress.audit_capabilities(
            records, {"01": "https://example.com"}
        )
        self.assertTrue(any("plan lacks --no-tools" in item for item in violations))


class AuditTests(unittest.TestCase):
    def make_session(self, root: Path, stage: str, tools: list[tuple[str, dict]]) -> None:
        stage_dir = root / stage
        stage_dir.mkdir(parents=True, exist_ok=True)
        entries = [
            {"type": "session", "id": f"{stage}-session"},
            {"type": "model_change", "provider": "test", "modelId": "model"},
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "toolCall", "name": name, "arguments": args}
                        for name, args in tools
                    ],
                },
            },
        ]
        (stage_dir / "session.jsonl").write_text(
            "".join(json.dumps(entry) + "\n" for entry in entries)
        )

    def test_clean_synthetic_run_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sessions_root = root / "sessions"
            self.make_session(sessions_root, "distill", [])
            self.make_session(sessions_root, "scout", [("search", {"query": "public topic"})])
            self.make_session(sessions_root, "plan", [])
            self.make_session(sessions_root, "expand", [])
            sessions = audit_egress.load_sessions(sessions_root)
            long_public_path = "a" * 90
            committed = f"https://example.com/{long_public_path}"
            entries = [
                {
                    "event": "attempt",
                    "stage": "scout",
                    "kind": "search",
                    "query": "public topic",
                    "query_length": 12,
                    "requested_url": "https://api.search.brave.com/res/v1/web/search?q=public+topic",
                    "url": "https://api.search.brave.com/res/v1/web/search?q=public+topic",
                    "host": "api.search.brave.com",
                    "redirect_hop": 0,
                    "_line": 1,
                },
                {
                    "event": "attempt",
                    "stage": "expand",
                    "slot": "01",
                    "kind": "fetch",
                    "query": None,
                    "requested_url": committed,
                    "url": committed,
                    "host": "example.com",
                    "redirect_hop": 0,
                    "_line": 2,
                },
            ]
            violations = audit_egress.audit_sessions(sessions)
            violations.extend(
                audit_egress.audit_entries(entries, {"01": committed})
            )
            self.assertEqual(violations, [])

    def test_forbidden_tool_and_private_query_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_session(root, "distill", [("bash", {"command": "env"})])
            self.make_session(root, "scout", [("search", {"query": "david@example.com"})])
            self.make_session(root, "plan", [])
            violations = audit_egress.audit_sessions(audit_egress.load_sessions(root))
            self.assertTrue(any("forbidden tool" in item for item in violations))
            self.assertTrue(any("private marker" in item for item in violations))

    def test_network_entry_from_sealed_stage_is_reported(self) -> None:
        entries = [
            {
                "event": "attempt",
                "stage": "plan",
                "kind": "fetch",
                "requested_url": "https://example.com/",
                "url": "https://example.com/",
                "host": "example.com",
                "redirect_hop": 0,
                "_line": 1,
            }
        ]
        violations = audit_egress.audit_entries(entries, {})
        self.assertTrue(any("forbidden/unknown stage" in item for item in violations))
