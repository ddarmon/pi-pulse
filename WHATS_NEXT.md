# What's next

Handoff doc for the next agent picking up pi-pulse work. The implemented
iterations are: observability + sesh isolation; mini-essay prose;
plan/expand split with N/M/O quotas; sesh auto-refresh; on-demand
profile interview at `scripts/interview.sh`; brave-search as the sole
search/fetch path in expand (no built-in `web_search`/`web_fetch`);
standalone HTML render via `sources/render_html.py` alongside the
markdown brief; a hard prompt rule against `Write`/`Edit` on the output
file (the model must emit the brief as final stdout text, since
`pulse.sh` captures stdout); and pi-pulse repo sessions excluded from
sesh collection via `collect_sesh.py --exclude-cwd` so the
meta-recursion loop is broken. The pipeline runs end-to-end. Below is
the remaining backlog in recommended order, plus context the next agent
will need.

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

### 2. Cross-Pulse topic awareness over a sliding window

The existing dedup mechanism (`memory/seen_urls.jsonl`) operates at URL
granularity: if today's expand surfaces a URL already in the ledger, the
card finds another source or drops. Topic-level dedup is missing, so
today's plan can re-pick a topic yesterday's brief already covered, just
via a different primary source. The user wants two behaviors:

1.  **Skip topics recently covered**, unless there is a substantive
    update worth surfacing.
2.  **When a topic does warrant expansion**, mark the card as a
    follow-up to the previous Pulse rather than a fresh discovery.

The first half is straightforward; the second half is the hard UX
problem.

Implementation sketch (the next session should refine this before
coding):

-   Before the plan stage, build a `.tmp/recent_pulses.md` bundle by
    walking the last N days of `out/YYYY-MM-DD.md` (default N=7,
    configurable via `PI_PULSE_HISTORY_DAYS`). Keep just the H2 card
    title lines plus the opening sentence of each card, so the bundle
    stays small.
-   Pass `.tmp/recent_pulses.md` as a third input to the plan-stage `pi`
    invocation alongside `interests_today.md` and `seen_urls.jsonl`.
-   Extend `prompts/compose_plan.md` with a rule: if a candidate topic
    matches a card title in the recent-pulses bundle, drop it by
    default. The plan MAY mark the topic as a follow-up to a specific
    prior date only when the memo (or active-threads section) shows new
    substantive ground worth covering; cap follow-ups at 1 per run so
    they do not crowd out genuinely new topics.
-   At expand time, follow-up cards should open with a one-clause
    callback ("Last week's Pulse covered X; the new ground is Y") rather
    than re-explaining the original topic from scratch.

UX failure modes to design against:

-   **Over-suppression.** A tracked thread (e.g. "Gemma 4 development")
    that is genuinely evolving daily must not be permanently blocked by
    a single past mention. The user's "Active threads" memo section is
    the live signal; respect it over the recent-pulses bundle when they
    conflict.
-   **Callback fatigue.** If every brief opens with "last week we
    covered...", novelty collapses. The 1-per-run cap and the
    "substantive new ground" gate are both load-bearing.
-   **Soft recency, not hard window.** A topic covered yesterday is much
    more painful to repeat than one covered 30 days ago. Whatever
    matching the plan does should prefer recent matches and gradually
    tolerate older overlaps.

Consider shipping the dedup half first (drop overlaps, no follow-up
mechanism) and the expansion half second once the dedup is observably
calibrated.

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
    quotas appear to be working: the 2026-05-17 and 2026-05-18 runs both
    shipped tracked/adjacent/bridge distributions that matched the plan,
    modulo cards dropped at expand for lack of a fresh primary source
    (4/8 dropped on the corrupted 05-17 run, 2/8 on 05-18). Keep
    comparing future expand outputs against the plan; if drops exceed
    \~25% consistently, consider whether plan-stage source plausibility
    checks need to be tighter.

2.  **Structural drift under prose pressure.** Original compose reliably
    dropped the labeled `**Follow-up:**` field; reshape to prose
    addressed that. The plan/expand split makes the leech-bridge a
    dedicated slot rather than a buried "must-include" rule (the kind
    that quietly disappears). Stay vigilant about required elements
    being lost when a stage gets reshaped.

3.  **Context overflow risk (mitigated).** `kimi-k2.6:cloud` is 262k
    tokens. An early 2026-05-17 expand reproduced overflow when 8
    `web_search` calls totaled 276,288 input tokens; pi compacted
    mid-expand and the model produced an empty brief. The fix lives in
    `prompts/compose_expand.md`: brave-search
    (https://github.com/badlogic/pi-skills/tree/main/brave-search,
    installed at `~/.claude/skills/brave-search`) is now the only
    permitted search/fetch path, since its markdown output is
    size-bounded. The per-card "1 search + 1 fetch" budget is still
    prompt-level, not tool-level -- if a future run overflows again
    despite brave-search, look at the expand session JSONL for the
    largest tool result and cap further upstream (e.g. drop to `-n 2`).
    The 2026-05-18 run's expand stage used 132K input tokens,
    comfortably under the limit.

4.  **Output-channel race (mitigated 2026-05-17).** A later 2026-05-17
    run produced a brief whose first \~1187 bytes were the model's
    narrative summary ("Done. Wrote 4 cards..."), with Card 1's title
    and opening paragraph missing. Root cause: `pulse.sh` redirects
    `pi`'s stdout to `out/$TODAY.md`, while the model used the `Write`
    tool to author the same path. Bash's fd held offset 0 from the `>`
    redirect; when pi's final assistant text streamed through, it
    clobbered the start of the Write tool's output.
    `prompts/compose_expand.md` now explicitly forbids `Write` and
    `Edit` on `out/YYYY-MM-DD.md` and explains the race inline so the
    rule survives a future prompt refactor. If anyone later wants the
    model to use `Write` (e.g. for post-hoc fixups), the pulse.sh
    redirect must change first; otherwise the race reappears silently.

5.  **Wall time growth.** Original two-pi-call compose was \~30s.
    Plan/expand split with per-card search budget put earlier runs at
    5--7 min, but the 2026-05-18 run took \~25 min (distill 6.9 + plan
    10.1 + expand 8.1). Distill showing 4x variance on similar input
    volume (107K tokens vs 108s the prior day) suggests Ollama Cloud /
    `kimi-k2.6:cloud` provider variance rather than a code regression,
    but worth monitoring across the next several runs. If the slowdown
    persists, profile the distill prompt and consider lowering the
    per-stage budget or running on a faster model. Adding backlog task
    #1 (profile auto-suggest) adds a fourth pi call and will push wall
    time further.

6.  **Meta-recursion (mitigated 2026-05-18).** Before this fix, pi-pulse
    iteration conversations lived in the user's sesh index and fed
    tomorrow's distill as an "active thread," producing self-referential
    cards. `pulse.sh` now passes `--exclude-cwd "$PWD"` to
    `collect_sesh.py`, which drops sessions whose `project_path` is the
    repo or a descendant. Trade-off: any unrelated session the user runs
    while `cd`'d into the repo for other work is also excluded;
    acceptable given the recursion cost. If a future brief still shows
    pi-pulse iteration as a thread, check whether the relevant session's
    `project_path` is actually outside the repo (e.g. started from `~`
    and only later `cd`'d in).

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

-   **`pulse.sh` redirects pi's stdout to `out/$TODAY.md`.** That `>`
    redirect is the channel the model uses to deliver the brief, which
    is why `compose_expand.md` forbids `Write`/`Edit` on the same path
    (they race and corrupt the output; see weakness #4 above). The HTML
    render step (`sources/render_html.py`) reads the markdown after pi
    exits, so it isn't affected by the redirect.

-   **Don't run `pulse.sh` casually.** Three pi calls, 5--25 min, spends
    real Ollama Cloud tokens (currently free but that may change). The
    user explicitly invokes it; don't trigger it as a "test" without
    asking.

-   **`BRAVE_API_KEY` and launchd inheritance.** Expand depends on
    `BRAVE_API_KEY`. launchd does NOT inherit `~/.zprofile` or
    `~/.zshrc`, so a key exported only in the shell profile will work
    interactively but silently break the 5:30am cron -- expand will
    return no search results and exit with the same empty-brief error as
    a context overflow. The key belongs in `.env`, which `pulse.sh`
    sources explicitly. To verify the launchd path will succeed,
    simulate its stripped environment:

    ``` bash
    env -i HOME="$HOME" PATH="/opt/homebrew/bin:/usr/bin:/bin" bash -c '
      cd <repo> &&
      set -a && source .env && set +a &&
      ~/.claude/skills/brave-search/search.js "test" -n 1 >/dev/null &&
      echo OK'
    ```

    If that prints `OK`, the scheduled run will see the key.

-   **HTML render needs pandoc or the `markdown` Python package.**
    `sources/render_html.py` prefers `pandoc` (installed at
    `/opt/homebrew/bin/pandoc`) and falls back to the `markdown` package
    via a PEP 723 header that `uv run` resolves. A render failure is
    non-fatal -- pulse.sh logs a WARN and leaves
    `logs/YYYY-MM-DD/render_html.err` -- so the markdown brief still
    ships even if HTML fails.

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
-   Latest commit: run `git log -1 --oneline` (this line rots fast).
-   Remote: `github.com/ddarmon/pi-pulse` (private)
-   The user pushes manually.
