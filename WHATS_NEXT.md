# What's next

Handoff doc for the next agent picking up pi-pulse work. The first batch
of iterations is in (observability + sesh isolation, mini-essay prose,
plan/expand split with N/M/O quotas, sesh auto-refresh, on-demand
profile interview at `scripts/interview.sh`). The pipeline runs
end-to-end. Below is the remaining backlog in recommended order, plus
context the next agent will need.

Read `CLAUDE.md` first for architecture, conventions, and cost
awareness. This file picks up where that ends.

## Remaining backlog

### 1. Auto-suggest profile updates after each run

After `expand`, run a small additional pi call (no tools) that reads
`.tmp/interests_today.md` and `memory/interests.md` and proposes 0--3
small additions or edits to the profile. Write proposals to
`.tmp/profile_updates.md` plus a one-line note in
`logs/YYYY-MM-DD/summary.md` ("3 suggested profile updates pending;
review with `scripts/apply-updates.sh`").

**Do NOT auto-apply.** Build a separate `scripts/apply-updates.sh` that
lets the user accept or reject each suggestion individually, reusing the
snapshot/diff pattern from `scripts/interview.sh` (snapshot to
`memory/interests-history/YYYY-MM-DD-HHMM.md` before any write).

Cost: adds a fourth pi call per run. Gate behind an env var
(`PI_PULSE_SUGGEST_PROFILE=1`) so the user opts in.

### 2. Render brief as standalone HTML

Add `sources/render_html.py`. Prefer `pandoc -s --mathjax` if available;
fall back to the Python `markdown` package with a small embedded CSS
block. Output goes to `out/YYYY-MM-DD.html` plus a copy into
`$PI_PULSE_DELIVERY` alongside the `.md`.

Self-contained: single file, embedded CSS, MathJax loaded from CDN only
if math is detected. Should render acceptably on iPhone and desktop
without further styling.

### 3. Strict citation verify-then-include (defer)

After `expand`, fetch every URL in the brief. If 404, or if the page
content doesn't mention the card's key claim (use a small
model-as-judge), drop the card and note the drop at the bottom of the
brief. Currently low priority: the user has audited the compose-stage
URLs and they grounded honestly. Revisit if drift appears in future
runs.

## Known weaknesses to watch

1.  **Topic-selection drift.** Before the plan/expand split, compose
    skewed hard toward frontier AI regardless of memo content. The N/M/O
    quotas should resolve this, but we have only one run of evidence
    (the 12:58 expand failed on context overflow before producing a
    brief). Compare future expand outputs against the plan to confirm
    the plan's distribution actually ships.

2.  **Structural drift under prose pressure.** Original compose reliably
    dropped the labeled `**Follow-up:**` field; reshape to prose
    addressed that. The plan/expand split makes the leech-bridge a
    dedicated slot rather than a buried "must-include" rule (the kind
    that quietly disappears). Stay vigilant about required elements
    being lost when a stage gets reshaped.

3.  **Context overflow risk.** `kimi-k2.6:cloud` is 262k tokens.
    `web_search` returns are not size-bounded; one call was observed at
    1.1M chars. The expand prompt enforces 1 search + 1 fetch per card,
    which is prompt-level not tool-level. If overflow recurs, replace
    `@ollama/pi-web-search` with the `brave-search` skill (returns
    size-bounded markdown). The user has the brave-search skill
    installed but no Brave API key.

4.  **Wall time growth.** Original two-pi-call compose was \~30s.
    Plan/expand split with per-card search budget puts a full run at
    5--7 min. Fine for a 5:30am cron, less fine for on-demand. Adding
    tasks #2 or #4 above will push this further.

5.  **Meta-recursion.** This conversation now lives in the user's sesh
    index. Tomorrow's distill will include pi-pulse iteration work as an
    "active thread," producing self-referential cards. Not a bug; just
    be aware when reading the next brief.

## Gotchas learned in the first session

-   **md2md.sh (pandoc) escapes `$` as inline math** and collapses
    double-brace pairs inside backticked code spans. Use double-brace
    placeholders in prompt files (see `prompts/compose_plan.md`) and
    substitute with `sed` in `pulse.sh`. Do not use `envsubst`.
-   **`sesh` requires `sesh refresh` before `sessions` works.**
    `collect_sesh.py` does this; do not remove the call without
    replacing the safeguard.
-   **`pi --session-dir` is load-bearing.** Without it, pulse runs
    pollute `~/.pi/agent/sessions/`, which sesh discovers, which means
    today's run becomes tomorrow's distill input. The whole isolation
    story breaks.
-   **`pulse.sh` exits 1 on empty stage output.** Honor this: do not
    swallow exit codes or add fallbacks that paper over a failed stage.
    The guard exists to surface context-overflow and similar errors
    loudly.
-   **Don't run `pulse.sh` casually.** Three pi calls, 5--7 min, spends
    real Ollama Cloud tokens (currently free but that may change). The
    user explicitly invokes it; don't trigger it as a "test" without
    asking.

## How to validate a change

For any change to the pipeline:

1.  Confirm with the user before running `pulse.sh`.
2.  After the user's run, read `out/YYYY-MM-DD.md` and
    `logs/YYYY-MM-DD/summary.md` (in that order).
3.  The summary shows per-stage wall time, tokens, tool calls, web
    search queries, and returned URLs.
4.  Check that any structural requirements you added (quotas, tags,
    sections) actually appear in the brief.
5.  If something looks off in the brief, look at the corresponding
    stage's `.log.md` for tool errors, then the raw session JSONL in
    `.pulse-sessions/YYYY-MM-DD/`.

## Repo state at handoff

-   Branch: `main`
-   Last commit: `3713531 Auto-refresh sesh index in collect_sesh.py`
-   Remote: `github.com/ddarmon/pi-pulse` (private)
-   The user pushes manually.
