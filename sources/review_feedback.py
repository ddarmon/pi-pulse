#!/usr/bin/env python3
"""Interactive single-keypress reviewer for feedback companion files.

Walks card-by-card through `out/*.feedback.md`, showing each card's prose
(read from the brief) and letting you rate it with one keypress. Ratings
are written straight back to the `.feedback.md` file -- the markdown stays
the backend -- so the normal ingest path (pulse.sh, or
scripts/ingest-feedback.sh) picks them up unchanged.

Default queue is every still-unrated card across all briefs, oldest
first, so a backlog clears in one sitting. Pass a RUN_ID to review just
one brief, or --include-rated to revisit cards you already marked.

Keys (best to worst):
    1  rate ++  (excellent)          4  rate -   (not interesting)
    2  rate +   (useful)             5  rate --  (don't want)
    3  rate =   (neutral: reviewed,  u  set unrated (not reviewed)
                 no strong opinion)  n  add/replace a note
    > / space / enter  next          p / <   previous
    q  quit

Unrated ([ ]) means not yet reviewed and is skipped by ingest; neutral
([=]) means reviewed with no strong opinion and is recorded as rating 0.

Usage:
    review_feedback.py [RUN_ID] [--include-rated] [--out-dir DIR]

Makes zero model calls. Edits are saved immediately, so quitting midway
keeps everything rated so far.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import textwrap
from pathlib import Path

from ingest_feedback import CARD_HEADING, NOTE_LINE, RATING_LINE, TAG_SUFFIX

VALID_MARKS = {"++", "+", "=", "-", "--"}
KEY_TO_MARK = {"1": "++", "2": "+", "3": "=", "4": "-", "5": "--"}
MARK_LABEL = {
    "": "unrated (not yet reviewed)",
    "++": "excellent",
    "+": "useful",
    "=": "neutral",
    "-": "not interesting",
    "--": "don't want this topic",
}


# --- parsing / serialization (pure, unit-testable) ---------------------


def parse_feedback_file(text: str) -> tuple[list[str], list[dict]]:
    """Return (header_lines, entries). Entry: {num, mark, title, note}."""
    lines = text.splitlines()
    first = next((i for i, ln in enumerate(lines) if RATING_LINE.match(ln)), len(lines))
    header = lines[:first]
    entries: list[dict] = []
    for ln in lines[first:]:
        rm = RATING_LINE.match(ln)
        if rm:
            mark = rm.group("mark").strip()
            entries.append(
                {
                    "num": int(rm.group("num")),
                    "mark": mark if mark in VALID_MARKS else "",
                    "title": rm.group("title").strip(),
                    "note": "",
                }
            )
            continue
        nm = NOTE_LINE.match(ln)
        if nm and entries:
            entries[-1]["note"] = nm.group("note")
    return header, entries


def serialize_feedback(header: list[str], entries: list[dict]) -> str:
    out = list(header)
    for e in entries:
        out.append(f"[{e['mark'] or ' '}] {e['num']}  {e['title']}")
        if e["note"]:
            out.append(f"    note: {e['note']}")
    return "\n".join(out).rstrip("\n") + "\n"


def parse_brief_cards(text: str) -> list[dict]:
    """Return [{title, tag, body}] in card order from a delivered brief."""
    lines = text.splitlines()
    heads = [(i, m.group(1).strip()) for i, ln in enumerate(lines) if (m := CARD_HEADING.match(ln))]
    cards: list[dict] = []
    for j, (idx, title) in enumerate(heads):
        end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
        body = "\n".join(lines[idx + 1 : end]).strip()
        tag_m = TAG_SUFFIX.search(title)
        cards.append({"title": title, "tag": tag_m.group(1).lower() if tag_m else "tracked", "body": body})
    return cards


def discover_files(out_dir: Path, run_id: str | None) -> list[Path]:
    if run_id:
        f = out_dir / f"{run_id}.feedback.md"
        return [f] if f.exists() else []
    files = sorted(p for p in out_dir.glob("*.feedback.md") if "_backup" not in p.name)
    return files


# --- terminal I/O ------------------------------------------------------


def getch() -> str:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)  # cbreak (not raw) so Ctrl-C still interrupts
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def wrap_body(body: str, width: int) -> list[str]:
    out: list[str] = []
    for para in body.split("\n\n"):
        para = " ".join(para.split())
        if not para:
            continue
        out.extend(textwrap.wrap(para, width=width) or [""])
        out.append("")
    if out and out[-1] == "":
        out.pop()
    return out


def render(run_id: str, file_pos: int, file_total: int, qpos: int, qtotal: int, card: dict, mark: str, note: str) -> None:
    cols = min(shutil.get_terminal_size((80, 24)).columns, 100)
    rule = "-" * cols
    sys.stdout.write("\033[2J\033[H")  # clear + home
    lines = [
        f"pi-pulse feedback - {run_id} - card {file_pos}/{file_total}   (queue {qpos}/{qtotal})",
        rule,
        f"{card['title']}",
        "",
        *wrap_body(card["body"], cols),
        "",
        f"Current rating: [{mark or ' '}] {MARK_LABEL[mark]}" + (f"   note: {note}" if note else ""),
        rule,
        "[1]++  [2]+  [3]=neutral  [4]-  [5]--    u=unrated  n=note  >=next  p=prev  q=quit",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def read_note(current: str) -> str:
    print()
    if current:
        print(f"current note: {current}")
    print("note (empty = keep current):")
    try:
        line = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        return current
    return line if line else current


# --- main loop ---------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", nargs="?")
    ap.add_argument("--include-rated", action="store_true")
    ap.add_argument("--out-dir", type=Path, default=Path("out"))
    args = ap.parse_args()

    files = discover_files(args.out_dir, args.run_id)
    if not files:
        where = f"out/{args.run_id}.feedback.md" if args.run_id else f"{args.out_dir}/*.feedback.md"
        print(f"no feedback files found ({where}).", file=sys.stderr)
        return 1

    # Load every file's state once: header, entries, and brief bodies.
    state: dict[str, dict] = {}
    queue: list[tuple[str, int]] = []
    for fb in files:
        run_id = fb.name[: -len(".feedback.md")]
        brief = args.out_dir / f"{run_id}.md"
        if not brief.exists():
            print(f"skip {run_id}: no brief at {brief}", file=sys.stderr)
            continue
        header, entries = parse_feedback_file(fb.read_text(errors="replace"))
        cards = parse_brief_cards(brief.read_text(errors="replace"))
        state[run_id] = {"path": fb, "header": header, "entries": entries, "cards": cards}
        for ei, e in enumerate(entries):
            if args.include_rated or not e["mark"]:
                queue.append((run_id, ei))

    if not queue:
        print("nothing to review -- every card is already rated.", file=sys.stderr)
        print("(use --include-rated to revisit rated cards.)", file=sys.stderr)
        return 0

    if not sys.stdin.isatty():
        print("review_feedback needs an interactive terminal (a TTY).", file=sys.stderr)
        return 1

    def write(run_id: str) -> None:
        st = state[run_id]
        st["path"].write_text(serialize_feedback(st["header"], st["entries"]))

    i = 0
    rated = 0
    while 0 <= i < len(queue):
        run_id, ei = queue[i]
        st = state[run_id]
        entry = st["entries"][ei]
        cards = st["cards"]
        card = cards[entry["num"] - 1] if entry["num"] - 1 < len(cards) else {"title": entry["title"], "tag": "", "body": "(card body unavailable)"}
        file_total = len(st["entries"])
        render(run_id, entry["num"], file_total, i + 1, len(queue), card, entry["mark"], entry["note"])

        ch = getch()
        if ch in KEY_TO_MARK:
            entry["mark"] = KEY_TO_MARK[ch]
            write(run_id)
            rated += 1
            i += 1
        elif ch == "u":
            entry["mark"] = ""
            write(run_id)
            rated += 1
            i += 1
        elif ch == "n":
            entry["note"] = read_note(entry["note"])
            write(run_id)
        elif ch in (">", " ", "\r", "\n", "s"):
            i += 1
        elif ch in ("<", "p"):
            i = max(0, i - 1)
        elif ch in ("q", "\x03", "\x04"):
            break
        # any other key: redraw same card

    sys.stdout.write("\033[2J\033[H")
    print(f"done. {rated} rating action(s) saved to the .feedback.md files.")
    print("(via scripts/review-feedback.sh, these are ingested automatically next;")
    print(" otherwise the next pulse run sweeps them into memory/feedback.jsonl.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
