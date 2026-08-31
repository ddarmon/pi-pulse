#!/usr/bin/env python3
"""Audit one completed pipeline run's egress and tool-capability invariants."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlsplit

from privacy import find_private_markers
from url_policy import MAX_URL_QUERY_CHARS, UrlPolicyError, validate_public_url

MAX_SEARCH_QUERY_CHARS = 256
ENCODED_BLOB_RE = re.compile(r"(?:[A-Fa-f0-9]{80,}|[A-Za-z0-9+/]{80,}={0,2})")
EXPECTED_TOOLS = {
    "distill": set(),
    "scout": {"search", "fetch"},
    "plan": set(),
    "expand": set(),
}
EXPECTED_EGRESS = {
    "scout": {"search", "fetch"},
    "expand": {"search", "fetch"},
}
PROMPTS = {
    "distill": Path("prompts/distill_context.md"),
    "scout": Path("prompts/scout_signals.md"),
    "plan": Path("prompts/compose_plan.md"),
    "expand": Path("prompts/compose_expand.md"),
}


@dataclass
class SessionEvidence:
    stage: str
    path: Path
    session_id: str = "?"
    provider: str = "?"
    model: str = "?"
    tools: list[tuple[str, dict]] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command_version(command: list[str], fallback: str = "unknown") -> str:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return fallback
    lines = [line.strip() for line in (proc.stdout + "\n" + proc.stderr).splitlines() if line.strip()]
    return next((line for line in lines if "warning" not in line.lower()), fallback)[:200]


def parse_session(path: Path, stage: str) -> SessionEvidence:
    evidence = SessionEvidence(stage=stage, path=path)
    for raw in path.read_text(errors="replace").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if entry.get("type") == "session":
            evidence.session_id = str(entry.get("id") or "?")
        elif entry.get("type") == "model_change":
            evidence.provider = str(entry.get("provider") or "?")
            evidence.model = str(entry.get("modelId") or "?")
        elif entry.get("type") == "message":
            message = entry.get("message") or {}
            content = message.get("content")
            if not isinstance(content, list) or message.get("role") == "toolResult":
                continue
            for item in content:
                if isinstance(item, dict) and item.get("type") == "toolCall":
                    args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
                    evidence.tools.append((str(item.get("name") or "?"), args))
    return evidence


def load_sessions(session_root: Path) -> list[SessionEvidence]:
    sessions: list[SessionEvidence] = []
    for stage in EXPECTED_TOOLS:
        stage_dir = session_root / stage
        if not stage_dir.is_dir():
            continue
        sessions.extend(parse_session(path, stage) for path in sorted(stage_dir.rglob("*.jsonl")))
    return sessions


def load_manifest(path: Path) -> tuple[dict[str, str], list[str]]:
    slots: dict[str, str] = {}
    violations: list[str] = []
    if not path.is_file():
        return slots, [f"manifest missing: {path}"]
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        fields = line.split("\t")
        if len(fields) != 3:
            violations.append(f"manifest line {number} has {len(fields)} fields (expected 3)")
            continue
        slot, _tag, url = fields
        if not slot or slot in slots:
            violations.append(f"manifest line {number} has a missing/duplicate slot")
            continue
        try:
            validate_public_url(url)
        except UrlPolicyError as exc:
            violations.append(f"manifest slot {slot} has invalid URL ({exc})")
            continue
        slots[slot] = url
    return slots, violations


def load_egress(path: Path) -> tuple[list[dict], list[str]]:
    entries: list[dict] = []
    violations: list[str] = []
    if not path.is_file():
        return entries, [f"egress log missing: {path}"]
    for number, raw in enumerate(path.read_text(errors="replace").splitlines(), 1):
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            violations.append(f"egress line {number} is invalid JSON")
            continue
        if not isinstance(item, dict):
            violations.append(f"egress line {number} is not an object")
            continue
        item["_line"] = number
        entries.append(item)
    return entries, violations


def load_capabilities(path: Path) -> tuple[list[dict], list[str]]:
    records: list[dict] = []
    violations: list[str] = []
    if not path.is_file():
        return records, [f"capability log missing: {path}"]
    for number, raw in enumerate(path.read_text(errors="replace").splitlines(), 1):
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            violations.append(f"capability line {number} is invalid JSON")
            continue
        if not isinstance(record, dict):
            violations.append(f"capability line {number} is not an object")
            continue
        record["_line"] = number
        records.append(record)
    return records, violations


def decoded_forms(value: str) -> list[str]:
    forms = [value]
    for _ in range(3):
        decoded = unquote_plus(forms[-1])
        if decoded == forms[-1]:
            break
        forms.append(decoded)
    return forms


def audit_private_text(
    label: str,
    value: str,
    violations: list[str],
    *,
    check_encoded_blob: bool = True,
) -> None:
    markers: set[str] = set()
    encoded_blob = False
    for candidate in decoded_forms(value):
        markers.update(find_private_markers(candidate))
        if check_encoded_blob:
            encoded_blob = encoded_blob or bool(ENCODED_BLOB_RE.search(candidate))
    if markers:
        violations.append(f"{label} contains private marker type(s): {', '.join(sorted(markers))}")
    if encoded_blob:
        violations.append(f"{label} contains a suspicious long encoded token")


def audit_entries(entries: list[dict], manifest: dict[str, str]) -> list[str]:
    violations: list[str] = []
    expand_initial: dict[str, list[dict]] = {}
    for item in entries:
        line = item.get("_line", "?")
        stage = item.get("stage")
        kind = item.get("kind")
        if stage not in EXPECTED_EGRESS:
            violations.append(f"egress line {line} has forbidden/unknown stage {stage!r}")
        elif kind not in EXPECTED_EGRESS[stage]:
            violations.append(f"egress line {line} {stage} has unknown kind {kind!r}")
        if item.get("event") not in {"attempt", "result", "rejected"}:
            violations.append(f"egress line {line} has unknown event {item.get('event')!r}")
        query = item.get("query")
        if isinstance(query, str):
            query_length = item.get("query_length")
            if len(query) > MAX_SEARCH_QUERY_CHARS or (
                isinstance(query_length, int) and query_length > MAX_SEARCH_QUERY_CHARS
            ):
                violations.append(f"egress line {line} query exceeds {MAX_SEARCH_QUERY_CHARS} chars")
            audit_private_text(f"egress line {line} query", query, violations)
        for key in ("requested_url", "url"):
            raw_url = item.get(key)
            if not isinstance(raw_url, str) or not raw_url:
                continue
            # Private markers are forbidden anywhere in a URL. Long encoded
            # blobs are suspicious specifically as query payloads; applying
            # that heuristic to a path would reject legitimate content hashes.
            audit_private_text(
                f"egress line {line} {key}",
                raw_url,
                violations,
                check_encoded_blob=False,
            )
            try:
                validate_public_url(raw_url)
            except UrlPolicyError as exc:
                # Rejected attempts are evidence that the guard worked, not a
                # policy violation; successful/attempted outbound URLs must pass.
                if item.get("event") != "rejected":
                    violations.append(f"egress line {line} has invalid {key} ({exc})")
            parsed = urlsplit(raw_url)
            if len(parsed.query) > MAX_URL_QUERY_CHARS:
                violations.append(f"egress line {line} URL query exceeds {MAX_URL_QUERY_CHARS} chars")
            for values in parse_qs(parsed.query, keep_blank_values=True).values():
                for value in values:
                    audit_private_text(f"egress line {line} decoded URL parameter", value, violations)
        if (
            item.get("event") == "attempt"
            and item.get("stage") == "expand"
            and item.get("kind") == "fetch"
            and item.get("redirect_hop") == 0
        ):
            expand_initial.setdefault(str(item.get("slot") or ""), []).append(item)

    for slot, committed in manifest.items():
        attempts = expand_initial.get(slot, [])
        if not attempts:
            violations.append(f"expand slot {slot} has no logged committed-URL fetch")
            continue
        committed_host = (urlsplit(committed).hostname or "").rstrip(".").lower()
        for attempt in attempts:
            attempted_host = str(attempt.get("host") or "").rstrip(".").lower()
            if attempted_host != committed_host:
                violations.append(
                    f"expand slot {slot} fetched host {attempted_host!r}, expected {committed_host!r}"
                )
    unknown_slots = sorted(slot for slot in expand_initial if slot and slot not in manifest)
    for slot in unknown_slots:
        violations.append(f"expand egress references slot {slot} absent from manifest")
    return violations


def audit_sessions(sessions: list[SessionEvidence]) -> list[str]:
    violations: list[str] = []
    stages_seen = {session.stage for session in sessions}
    for required in ("distill", "scout", "plan"):
        if required not in stages_seen:
            violations.append(f"no {required} session transcript found")
    for session in sessions:
        allowed = EXPECTED_TOOLS[session.stage]
        for name, args in session.tools:
            if name not in allowed:
                violations.append(
                    f"{session.stage} session {session.path.name} called forbidden tool {name!r}"
                )
            if name == "search" and isinstance(args.get("query"), str):
                audit_private_text(
                    f"{session.stage} session {session.path.name} search query",
                    args["query"],
                    violations,
                )
            if name == "fetch" and isinstance(args.get("url"), str):
                audit_private_text(
                    f"{session.stage} session {session.path.name} fetch URL",
                    args["url"],
                    violations,
                    check_encoded_blob=False,
                )
    return violations


def audit_capabilities(records: list[dict], manifest: dict[str, str]) -> list[str]:
    """Verify the flags extracted from the exact command arrays that ran."""
    violations: list[str] = []
    stages_seen: set[str] = set()
    common_switches = ("no_context_files", "no_extensions", "no_skills")
    for record in records:
        line = record.get("_line", "?")
        stage = record.get("stage")
        if stage not in EXPECTED_TOOLS:
            violations.append(f"capability line {line} has unknown stage {stage!r}")
            continue
        stages_seen.add(stage)
        for switch in common_switches:
            if record.get(switch) is not True:
                violations.append(f"capability line {line} {stage} lacks --{switch.replace('_', '-')}")
        tools = record.get("tools")
        extensions = record.get("extensions")
        skills = record.get("skills")
        if not isinstance(tools, list) or not all(isinstance(item, str) for item in tools):
            violations.append(f"capability line {line} has malformed tools evidence")
            tools = []
        if not isinstance(extensions, list) or not all(
            isinstance(item, str) for item in extensions
        ):
            violations.append(f"capability line {line} has malformed extension evidence")
            extensions = []
        if not isinstance(skills, list) or not all(isinstance(item, str) for item in skills):
            violations.append(f"capability line {line} has malformed skill evidence")
            skills = []
        if skills:
            violations.append(f"capability line {line} {stage} loads explicit skills {skills!r}")

        if stage == "scout":
            if record.get("no_builtin_tools") is not True:
                violations.append(f"capability line {line} scout lacks --no-builtin-tools")
            if record.get("no_tools") is True:
                violations.append(f"capability line {line} scout unexpectedly disables broker tools")
            if set(tools) != {"search", "fetch"} or len(tools) != 2:
                violations.append(
                    f"capability line {line} scout tool allowlist is {tools!r}, expected search/fetch"
                )
            expected_extension = Path("sources/brave-guard/scout.ts").resolve()
            if len(extensions) != 1 or Path(extensions[0]).resolve() != expected_extension:
                violations.append(
                    f"capability line {line} scout extension evidence is {extensions!r}"
                )
        else:
            if record.get("no_tools") is not True:
                violations.append(f"capability line {line} {stage} lacks --no-tools")
            if tools:
                violations.append(f"capability line {line} {stage} has a tool allowlist {tools!r}")
            if extensions:
                violations.append(
                    f"capability line {line} {stage} loads explicit extensions {extensions!r}"
                )

        slot = record.get("slot")
        if stage == "expand":
            if not isinstance(slot, str) or slot not in manifest:
                violations.append(f"capability line {line} expand has unknown slot {slot!r}")
        elif slot is not None:
            violations.append(f"capability line {line} {stage} unexpectedly names slot {slot!r}")
        if not record.get("provider") or not record.get("model"):
            violations.append(f"capability line {line} lacks provider/model provenance")

    for stage in EXPECTED_TOOLS:
        if stage not in stages_seen:
            violations.append(f"no {stage} capability record found")
    return violations


def render_report(
    run_id: str,
    sessions: list[SessionEvidence],
    entries: list[dict],
    capabilities: list[dict],
    violations: list[str],
) -> str:
    commit = command_version(["git", "rev-parse", "HEAD"])
    pi_version = command_version(["pi", "--version"])
    broker_files = sorted(Path("sources/brave-guard").glob("*.*"))
    broker_hash = hashlib.sha256(
        "".join(f"{path}:{sha256_file(path)}\n" for path in broker_files if path.is_file()).encode()
    ).hexdigest()
    prompt_hashes = {stage: sha256_file(path) for stage, path in PROMPTS.items()}
    attempts = sum(1 for item in entries if item.get("event") == "attempt")
    rejected = sum(1 for item in entries if item.get("event") == "rejected")

    lines = [
        f"# Egress audit {run_id}",
        "",
        f"- status: **{'FAIL' if violations else 'PASS'}**",
        f"- commit: `{commit}`",
        f"- pi: `{pi_version}`",
        f"- guarded broker SHA-256: `{broker_hash}`",
        f"- network attempts: {attempts} (rejected before network: {rejected})",
        f"- recorded Pi invocations: {len(capabilities)}",
        "",
        "## Stage evidence",
        "",
        "| stage | session | provider/model | tools | prompt SHA-256 |",
        "|---|---|---|---:|---|",
    ]
    if sessions:
        for session in sessions:
            lines.append(
                f"| {session.stage} | `{session.session_id}` | "
                f"`{session.provider}/{session.model}` | {len(session.tools)} | "
                f"`{prompt_hashes[session.stage]}` |"
            )
    else:
        lines.append("| (none) | — | — | 0 | — |")
    lines.extend(["", "## Assertions", ""])
    if violations:
        lines.extend(f"- FAIL: {violation}" for violation in violations)
    else:
        lines.extend(
            [
                "- PASS: sealed stages made zero tool calls.",
                "- PASS: every recorded Pi invocation used its expected capability flags.",
                "- PASS: scout used only the guarded `search`/`fetch` tools.",
                "- PASS: every expand fetch began at its manifest-committed host.",
                "- PASS: query/URL caps and private-marker checks passed.",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--manifest", type=Path, default=Path(".tmp/expand/manifest.tsv"))
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--session-dir", type=Path)
    args = parser.parse_args()
    log_dir = args.log_dir or Path("logs") / args.run_id
    session_dir = args.session_dir or Path(".pulse-sessions") / args.run_id

    manifest, violations = load_manifest(args.manifest)
    entries, load_violations = load_egress(log_dir / "egress.log")
    violations.extend(load_violations)
    capabilities, capability_load_violations = load_capabilities(
        log_dir / "capabilities.jsonl"
    )
    violations.extend(capability_load_violations)
    sessions = load_sessions(session_dir)
    violations.extend(audit_sessions(sessions))
    violations.extend(audit_capabilities(capabilities, manifest))
    violations.extend(audit_entries(entries, manifest))
    # Keep ordering stable but collapse repeated marker reports.
    violations = list(dict.fromkeys(violations))
    sys.stdout.write(render_report(args.run_id, sessions, entries, capabilities, violations))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
