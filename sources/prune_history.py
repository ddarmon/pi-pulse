#!/usr/bin/env python3
"""Prune date-stamped private logs and Pi sessions after a retention window."""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

RUN_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d{2})(\d{2}))?$")
SESSION_CONTAINERS = ("suggest", "interview")


def run_timestamp(name: str) -> datetime | None:
    match = RUN_RE.fullmatch(name)
    if not match:
        return None
    value = match.group(1)
    fmt = "%Y-%m-%d"
    if match.group(2) is not None:
        value += f"-{match.group(2)}{match.group(3)}"
        fmt += "-%H%M"
    try:
        return datetime.strptime(value, fmt)
    except ValueError:
        return None


def candidates(logs_dir: Path, sessions_dir: Path) -> list[tuple[Path, Path]]:
    """Return (retention-root, candidate) pairs; never broad roots."""
    found: list[tuple[Path, Path]] = []
    if logs_dir.is_dir():
        found.extend((logs_dir, child) for child in logs_dir.iterdir() if run_timestamp(child.name))
    if sessions_dir.is_dir():
        found.extend(
            (sessions_dir, child)
            for child in sessions_dir.iterdir()
            if run_timestamp(child.name)
        )
        for container_name in SESSION_CONTAINERS:
            container = sessions_dir / container_name
            if container.is_dir():
                found.extend(
                    (container, child)
                    for child in container.iterdir()
                    if run_timestamp(child.name)
                )
    return found


def safe_delete(root: Path, target: Path) -> None:
    resolved_root = root.resolve()
    resolved_target = target.resolve()
    if resolved_target == resolved_root or resolved_root not in resolved_target.parents:
        raise ValueError(f"refusing unsafe retention target: {target}")
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)


def prune(
    logs_dir: Path,
    sessions_dir: Path,
    days: int,
    now: datetime,
    dry_run: bool,
    exclude: frozenset[str] = frozenset(),
) -> list[Path]:
    if days < 1:
        raise ValueError("retention days must be at least 1")
    cutoff = now - timedelta(days=days)
    removed: list[Path] = []
    for root, target in candidates(logs_dir, sessions_dir):
        if target.name in exclude:
            continue
        timestamp = run_timestamp(target.name)
        if timestamp is None or timestamp >= cutoff:
            continue
        removed.append(target)
        if not dry_run:
            safe_delete(root, target)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        required=True,
        help="Positive retention window; omission never implies deletion.",
    )
    parser.add_argument("--logs-dir", type=Path, default=Path("logs"))
    parser.add_argument("--sessions-dir", type=Path, default=Path(".pulse-sessions"))
    parser.add_argument("--now", help="Test override (ISO local datetime)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Date-shaped run ID to preserve (repeatable).",
    )
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now) if args.now else datetime.now()
    removed = prune(
        args.logs_dir,
        args.sessions_dir,
        args.days,
        now,
        args.dry_run,
        frozenset(args.exclude),
    )
    action = "would prune" if args.dry_run else "pruned"
    print(f"# retention: {action} {len(removed)} entr{'y' if len(removed) == 1 else 'ies'} older than {args.days}d")
    for item in removed:
        print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
