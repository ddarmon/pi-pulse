#!/usr/bin/env python3
"""Collect recent sesh sessions for the distill stage.

Uses `sesh sessions` JSON to find sessions within the last --since N
days, groups them by project path, and emits the markdown export of
each (truncated head + tail). Provides a view of recent coding work
to feed the distill prompt. Requires `sesh` on $PATH; see
https://github.com/ddarmon/sesh.

By default this reads the sessions on THIS machine, so a brief built on
an always-on host reflects only that host's work. Two options widen it:

--aggregation-root is the general one and is handed straight to sesh,
which reads a directory of mirrored home directories -- one immediate
subdirectory per host, each holding .claude/, .codex/, .pi/agent/.
Anything producing that shape works: rsync, Syncthing, a mounted
backup, an NFS export.

--archive-root is a convenience adapter for one layout that does not,
an agent archive storing machines/<host>/<tool>/data/. build_shim()
symlinks that into the shape above and is rebuilt every run, so hosts
joining or leaving the archive need no config change.

Either way sesh reports a host per session, exposed on each ### line
and selectable with --host.

One shared-state hazard applies to both: aggregation mode and local
mode use the same index under ~/.cache/sesh, and a refresh in either
overwrites the other's. Since this runs unattended on a machine whose
owner also uses sesh interactively, aggregation calls are given their
own XDG_CACHE_HOME rather than clobbering the interactive index.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n\n[... truncated {len(text) - max_chars} chars ...]\n\n{text[-half:]}"


# Archive layout -> the mirrored-$HOME layout sesh's aggregation mode wants.
SHIM_LAYOUT = {
    "claude/data": ".claude",
    "codex/data": ".codex",
    "pi/data": ".pi/agent",
}


def build_shim(archive_root: Path, shim_dir: Path) -> tuple[Path, list[str]]:
    """Adapt the agent-archive layout to the one sesh aggregates over.

    Returns the aggregation root to hand to sesh and the hosts found in
    it. Rebuilt from scratch each run so a host that stops archiving
    stops appearing, and one that starts appears on its own. Callers
    whose sessions are already mirrored per host do not need this --
    they can pass --aggregation-root and skip the adapter entirely.
    """
    machines = archive_root / "machines"
    if not machines.is_dir():
        print(
            f"ERROR: no machines/ directory under archive root {archive_root}. "
            "Expected an agent-archive checkout.",
            file=sys.stderr,
        )
        sys.exit(1)

    if shim_dir.is_symlink() or shim_dir.is_file():
        shim_dir.unlink()
    elif shim_dir.is_dir():
        shutil.rmtree(shim_dir)

    hosts: list[str] = []
    for machine in sorted(machines.iterdir()):
        if not machine.is_dir():
            continue
        linked = False
        for source_rel, home_rel in SHIM_LAYOUT.items():
            source = machine / source_rel
            if not source.is_dir():
                continue
            link = shim_dir / machine.name / home_rel
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(source)
            linked = True
        if linked:
            hosts.append(machine.name)

    if not hosts:
        print(f"ERROR: archive root {archive_root} holds no usable machines.", file=sys.stderr)
        sys.exit(1)
    return shim_dir, hosts


def host_summary(root: Path, found: list[str], wanted: set[str]) -> str:
    scope = ", ".join(sorted(wanted)) if wanted else "all hosts"
    return f"{root} -> {scope} (available: {', '.join(found)})"


def find_sesh(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = shutil.which("sesh")
    if found:
        return found
    print(
        "ERROR: `sesh` not found on PATH. Install from "
        "https://github.com/ddarmon/sesh and ensure it is on $PATH, or "
        "pass --sesh /path/to/sesh.",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", type=int, default=7, help="Days of history (default: 7)")
    ap.add_argument(
        "--max-chars",
        type=int,
        default=4000,
        help="Per-session truncation budget (default: 4000)",
    )
    ap.add_argument(
        "--deny-projects",
        default="",
        help="Comma-separated substring deny list applied to project_path",
    )
    ap.add_argument(
        "--providers",
        default="claude,codex,cursor,copilot,pi",
        help="Comma-separated provider allow list",
    )
    ap.add_argument("--sesh", default=None, help="Path to sesh binary")
    ap.add_argument(
        "--aggregation-root",
        type=Path,
        default=None,
        help=(
            "Read sessions from a directory of mirrored home directories, one "
            "immediate subdirectory per host (each holding .claude/, .codex/, "
            ".pi/agent/). Passed through to sesh. Default: this machine."
        ),
    )
    ap.add_argument(
        "--archive-root",
        type=Path,
        default=None,
        help=(
            "Like --aggregation-root, but for an agent archive storing "
            "machines/<host>/<tool>/data; a symlink shim adapts it. Mutually "
            "exclusive with --aggregation-root."
        ),
    )
    ap.add_argument(
        "--shim-dir",
        type=Path,
        default=Path(".tmp/sesh-agg"),
        help=(
            "Where --archive-root builds its symlink shim "
            "(default: .tmp/sesh-agg). Unused with --aggregation-root."
        ),
    )
    ap.add_argument(
        "--sesh-cache-dir",
        type=Path,
        default=Path(".tmp/sesh-cache"),
        help=(
            "XDG_CACHE_HOME for multi-host sesh calls, so refreshing that "
            "index does not overwrite the interactive one "
            "(default: .tmp/sesh-cache)"
        ),
    )
    ap.add_argument(
        "--host",
        action="append",
        default=[],
        help=(
            "Only include sessions from this host. Repeatable; default is "
            "every host available. Ignored when reading this machine."
        ),
    )
    ap.add_argument(
        "--exclude-cwd",
        action="append",
        default=[],
        help="Drop sessions whose project_path is this directory or a descendant. Repeatable.",
    )
    args = ap.parse_args()

    sesh = find_sesh(args.sesh)
    deny = [s.strip() for s in args.deny_projects.split(",") if s.strip()]
    allow = {s.strip() for s in args.providers.split(",") if s.strip()}

    root_flags: list[str] = []
    env = os.environ.copy()
    hosts_wanted = {h.strip() for h in args.host if h.strip()}

    if args.aggregation_root is not None and args.archive_root is not None:
        print(
            "ERROR: --aggregation-root and --archive-root are mutually "
            "exclusive; --archive-root only adapts one layout into the other.",
            file=sys.stderr,
        )
        return 2

    # Both options end at the same place: an aggregation root for sesh.
    agg_root: Path | None = None
    hosts_found: list[str] = []
    if args.aggregation_root is not None:
        agg_root = args.aggregation_root.expanduser().resolve()
        if not agg_root.is_dir():
            print(f"ERROR: aggregation root not found: {agg_root}", file=sys.stderr)
            return 1
        hosts_found = sorted(d.name for d in agg_root.iterdir() if d.is_dir())
        if not hosts_found:
            print(f"ERROR: aggregation root {agg_root} holds no hosts.", file=sys.stderr)
            return 1
    elif args.archive_root is not None:
        agg_root, hosts_found = build_shim(
            args.archive_root.expanduser().resolve(),
            args.shim_dir.expanduser().resolve(),
        )

    if agg_root is not None:
        root_flags = ["--aggregation-root", str(agg_root)]
        # Must be absolute: the XDG base-directory spec requires a relative
        # XDG_CACHE_HOME to be IGNORED, so a relative value here silently
        # falls back to ~/.cache and this refresh overwrites the interactive
        # index -- the exact clobber this is here to prevent.
        cache = args.sesh_cache_dir.expanduser().resolve()
        cache.mkdir(parents=True, exist_ok=True)
        env["XDG_CACHE_HOME"] = str(cache)
        unknown = hosts_wanted - set(hosts_found)
        if unknown:
            print(
                f"WARN: --host names no available host: {', '.join(sorted(unknown))}; "
                f"available: {', '.join(hosts_found)}",
                file=sys.stderr,
            )
        print(
            f"# multi-host: {host_summary(agg_root, hosts_found, hosts_wanted)}",
            file=sys.stderr,
        )
    elif hosts_wanted:
        print(
            "WARN: --host has no effect without --aggregation-root or --archive-root",
            file=sys.stderr,
        )

    def sesh_cmd(*rest: str) -> list[str]:
        return [sesh, *root_flags, *rest]

    excludes: list[Path] = []
    for raw in args.exclude_cwd:
        try:
            excludes.append(Path(raw).expanduser().resolve(strict=False))
        except (OSError, RuntimeError) as exc:
            print(f"WARN: could not resolve --exclude-cwd {raw!r}: {exc}", file=sys.stderr)

    # Build/refresh the index. sesh CLI subcommands like `sessions` and
    # `export` require an index; if it is missing or stale, every call
    # fails with exit 1 and pulse.sh dies under `set -e`. Refresh is
    # idempotent and takes a few seconds.
    try:
        subprocess.check_call(
            sesh_cmd("refresh"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        print(f"WARN: `sesh refresh` failed: {exc}", file=sys.stderr)

    try:
        raw = subprocess.check_output(sesh_cmd("sessions"), text=True, env=env)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: `sesh sessions` failed: {exc}", file=sys.stderr)
        return 1

    sessions = json.loads(raw)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.since)

    recent: list[dict] = []
    for s in sessions:
        ts = s.get("timestamp")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if t < cutoff:
            continue
        if s.get("provider") not in allow:
            continue
        if hosts_wanted and s.get("host") not in hosts_wanted:
            continue
        path = s.get("project_path", "") or ""
        if any(d in path for d in deny):
            continue
        if excludes and path:
            try:
                rp = Path(path).expanduser().resolve(strict=False)
                if any(rp == e or rp.is_relative_to(e) for e in excludes):
                    continue
            except (OSError, RuntimeError):
                if any(path == str(e) or path.startswith(str(e) + os.sep) for e in excludes):
                    continue
        recent.append(s)

    scope = ""
    if agg_root is not None:
        which = ", ".join(sorted(hosts_wanted)) if hosts_wanted else "all available hosts"
        scope = f", from {which}"
    print(f"# sesh sessions (last {args.since} days, {len(recent)} sessions{scope})")
    print()

    if not recent:
        print("(no sessions in window)")
        return 0

    by_project: dict[str, list[dict]] = defaultdict(list)
    for s in recent:
        by_project[s.get("project_path", "(unknown)")].append(s)

    total_in = total_out = 0
    for project in sorted(by_project):
        print(f"\n## {project}\n")
        for s in sorted(by_project[project], key=lambda x: x.get("timestamp", ""), reverse=True):
            sid = s["id"]
            ts = s.get("timestamp", "?")
            summary = (s.get("summary") or "").strip().replace("\n", " ")[:160]
            provider = s.get("provider", "?")
            model = s.get("model", "?")
            origin = f"{s['host']} | " if s.get("host") else ""
            print(f"### {origin}{provider} | {model} | {ts} | {sid}")
            print(f"_Summary:_ {summary}")
            print()
            try:
                md = subprocess.check_output(
                    sesh_cmd("export", sid, "--provider", provider, "--format", "md"),
                    text=True,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
            except subprocess.CalledProcessError as exc:
                print(f"_(export failed: {exc})_\n")
                continue
            total_in += len(md)
            truncated = truncate(md, args.max_chars)
            total_out += len(truncated)
            print(truncated)
            print()

    print(
        f"\n# Bundle stats: {total_in} chars in, {total_out} chars out",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
