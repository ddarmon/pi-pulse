# What's next

Handoff doc for the next agent picking up pi-pulse work. The implemented
iterations are: observability + sesh isolation; mini-essay prose;
plan/expand split with N/M/O quotas; sesh auto-refresh; on-demand
profile interview at `scripts/interview.sh`; brave-search as the sole
search/fetch path (no built-in `web_search`/`web_fetch`);
standalone HTML render via `sources/render_html.py` alongside the
markdown brief; a hard prompt rule against `Write`/`Edit` on the output
file (the model must emit the brief as final stdout text, since
`pulse.sh` captures stdout); pi-pulse repo sessions excluded from sesh
collection via `collect_sesh.py --exclude-cwd` so the meta-recursion
loop is broken; recent-pulses topic dedup; follow-up cards as a
narrow escape hatch when a deduped thread has fresh signal; and
**source-first pipeline** with a new `scout` stage that probes
`brave-search` per interest cluster before plan picks, plus per-card
parallel expand bounded by `PI_PULSE_EXPAND_PARALLEL`. Card quotas are
now caps, not targets. The pipeline runs end-to-end. Below is the
remaining backlog in recommended order, plus context the next agent
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

Cost: adds one more pi call per run. The pipeline already runs `3 + N`
pi calls (distill, scout, plan, one per parallel expand slot) under the
unlimited-Ollama operating assumption, so an extra serial call is
cheap. Still gate behind an env var (`PI_PULSE_SUGGEST_PROFILE=1`) so
the user opts in; auto-applying profile edits would be a trust step
the user hasn't taken yet.

**Revised cadence (2026-06-13).** The decision is to run this *weekly*,
not per-run, and to feed it three inputs: the week's archived distill
memos, the current profile, and the new feedback digest
(`.tmp/feedback_recent.md`, see "Recently shipped: feedback loop").
Per-run suggestion was judged too noisy. The memo is not yet archived
per-run -- the weekly stage needs `pulse.sh` to `cp
.tmp/interests_today.md "$LOG_DIR/memo.md"` after distill (falls back to
`out/*.md` until ~7 memos accumulate). Still proposals-only;
`scripts/apply-updates.sh` stays the human gate.

### 2. Strict citation verify-then-include (defer)

Scout now commits every card's `Source URL` upstream, and expand's
first action is a `content.js` fetch on that committed URL -- so URL
fabrication is structurally unlikely (see "Recently shipped: source-
first pipeline" below). The remaining risk is **claim drift**: the
fetched page is real but the card's claim about it isn't supported by
what was fetched. A model-as-judge pass after expand could catch this
(read card body + content.js result, flag mismatches), but no live
case has surfaced yet. Revisit if a future audit finds a card whose
URL resolves but whose claims don't match the page content.

## Recently shipped

-   **Feedback loop (2026-06-13).** Each run now emits
    `out/${RUN_ID}.feedback.md`: one numbered line per delivered card
    (card N = Nth `## ` heading in the brief), each with an editable
    mark. The reader marks `[++]`/`[+]`/`[ ]`/`[-]`/`[--]` (optional
    indented `note:`) and runs `scripts/ingest-feedback.sh [RUN_ID]`.
    Ingest re-joins each rated card against the brief to recover title,
    normalized primary URL (shared `append_seen.normalize`), and tag,
    then writes idempotent rows to `memory/feedback.jsonl` (drops a
    run's existing rows before re-adding, so re-edit/re-ingest is safe)
    and rebuilds `.tmp/feedback_recent.md` (last 14 days, grouped
    valued / not-valued / avoid-candidates) via
    `build_feedback_digest.py`. **Zero model calls** -- pure local
    bookkeeping. New files: `sources/build_feedback_template.py`,
    `sources/ingest_feedback.py`, `sources/build_feedback_digest.py`,
    `scripts/ingest-feedback.sh`. `pulse.sh` generates + delivers the
    companion file (non-fatal). Nothing in the daily pipeline consumes
    the digest yet -- it is the intended input to backlog item 1
    (weekly profile-suggest). `[--]` does not auto-suppress topics; it
    only flags avoid-candidates for the weekly review.

-   **Deterministic hard predicates (2026-06-11).** Three checks that
    were prompt-level promises are now code. (1)
    `sources/filter_signals.py` sits between scout and plan: scout's
    stdout lands in `.tmp/signals_raw.md`, the filter drops signals
    whose normalized URL (shared `append_seen.normalize`) is in
    `memory/seen_urls.jsonl` or `memory/unfetchable_urls.jsonl`,
    dedupes within the sheet, and writes `.tmp/signals.md`; an
    all-filtered sheet is a loud exit-1. (2) `split_plan.py
    --signals` verifies each plan slot's `Source URL:` against the
    signal sheet and excludes failures from the manifest as
    `DROPPED slot=NN tag=... reason=...` on stderr, which pulse.sh
    folds into `dropped.md`. (3) `sources/append_unfetchable.py`
    records model-dropped slots' Source URLs (the
    `DROPPED slot=... reason=...` contract from compose_expand.md)
    into `memory/unfetchable_urls.jsonl` so a 403'd source is not
    re-scouted and re-dropped the next day; `pi-exit-nonzero` and
    malformed-body drops are skipped, and the step only runs on
    runs that delivered (all-dropped runs usually mean a systemic
    failure like a missing `BRAVE_API_KEY`, not bad URLs).
    Motivating evidence: in the week of 2026-06-05--11, both expand
    drops were paywalled-publisher 403s (Wiley, ACM) whose URLs
    never entered the seen ledger and so remained re-discoverable;
    the 2026-06-11 ACM URL was used as the live test fixture.

-   **Source-first pipeline (2026-05-21).** Replaced the
    distill→plan→expand contract with distill→scout→plan→expand, where
    `scout` (new pi call, `prompts/scout_signals.md`) probes
    `brave-search` per interest cluster and writes a structured
    `.tmp/signals.md` with `url`/`published`/`source_class`/`gloss`/
    `memo_anchor`/`relation`. Plan is now a ranker: each card slot
    must cite a signal `url` verbatim, and quotas became *caps* rather
    than targets. Expand was decomposed into per-card parallel pi
    calls (`sources/split_plan.py` writes per-slot files,
    `sources/expand_slot.sh` is invoked by `xargs -P
    $PI_PULSE_EXPAND_PARALLEL`); each slot fetches its committed
    Source URL via `content.js` and falls back to one `search.js`
    only if the fetch 404s. Drops go to stderr as `DROPPED slot=NN
    reason=...` and are aggregated into `logs/YYYY-MM-DD/dropped.md`
    -- never into the delivered brief. First live run measured 0/4
    drops (down from 5/8 = 62.5% on the morning baseline) with every
    card carrying a verifiable scout-discovered URL. Wall time
    ~10 min (distill 49s + scout 331s + plan 73s + parallel expand
    ~120s). Plan's output-saturation issue resolved as a side effect
    (7k tokens vs 16,384 cap) because plan's job shrank to mechanical
    ranking. New env vars: `PI_PULSE_SCOUT_MAX_INTERESTS` (default
    12), `PI_PULSE_SCOUT_QUERIES_PER_INTEREST` (default 2),
    `PI_PULSE_EXPAND_PARALLEL` (default 4).

-   **Recent-pulses topic dedup (2a).** Plan stage receives
    `.tmp/recent_pulses.md` (last 7 days of `out/*.md`, today excluded,
    backup files filtered) and drops candidate topics that semantically
    overlap. Bundle derives from markdown via
    `sources/build_recent_pulses.py` -- no new ledger. Today is
    intentionally excluded so the script is safe to re-run mid-day
    without self-citing; this lives in the script's docstring and
    `--days` help text. Configurable via `PI_PULSE_HISTORY_DAYS`
    (default 7).
-   **Follow-up cards (2b).** Up to one card per run (configurable via
    `PI_PULSE_CARDS_FOLLOWUP`, default 1) may re-cover a
    recently-shipped topic, but only when the memo's "Active threads" OR
    "Open questions" section names a fresh signal -- a release, paper,
    version bump, blog post, or named event dated within the
    recent-pulses window. A follow-up consumes one tracked slot; if
    TRACKED=5 and FOLLOWUP=1, the run emits at most 4 fresh tracked
    cards plus 1 follow-up. The plan tags the card
    `(follow-up of YYYY-MM-DD)` with extra `Prior coverage:` and
    `New ground:` fields; expand renders the title as `(follow-up)` and
    requires the first sentence to cite the prior date and state what's
    new. Each bundle entry carries a `[YYYY-MM-DD]` prefix so the model
    attributes the prior date accurately. First live run (2026-05-18
    evening) correctly emitted zero follow-ups -- yesterday's brief
    covered Gemma 4, Ollama MLX, and BitNet, but today's memo had no
    fresh-signal bullet for any of them. The plan model demonstrably
    consulted the bundle (one tracked card's rationale said "Yesterday
    covered Gemma 4; this is the distinct hybrid-SSM lineage") and
    routed to an adjacent thread rather than re-covering. No callback
    fatigue yet because the gate hasn't fired.

## Known weaknesses to watch

1.  **Drop rate (RESOLVED 2026-05-21 by source-first pipeline).** Was
    chronic 50% (sometimes 62.5%) under the old distill→plan→expand
    contract: plan picked topics speculatively, expand discovered the
    absence of fresh sources too late and dropped cards. Resolved by
    inserting the scout stage so plan picks only from grounded
    candidates. First live run: 0/4 drops. Watch the next several
    runs to confirm the rate stays at or near zero. If drops creep
    back up, look first at scout's source-class judgement (is it
    accepting aggregator URLs?) and at signal freshness criteria in
    `prompts/scout_signals.md`.

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

5.  **Wall time; scout is the new bottleneck.** Source-first pipeline
    runs in ~10 min (first live run: distill 49s + scout 331s + plan
    73s + parallel expand ~120s). Scout is now the longest leg
    because it runs `M*K` brave-search queries (default 12 interests
    \* 2 queries = up to 24 search.js calls) serially within one pi
    session. If scout wall time becomes a problem, the natural fix is
    to parallelize *within* scout (one pi sub-session per interest
    cluster) the same way expand was parallelized -- but only if it
    matters. Per-card expand parallelism keeps total time bounded by
    `max(per-card)` rather than the sum, so adding cards is cheap
    until you hit `PI_PULSE_EXPAND_PARALLEL` saturation.

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

7.  **Follow-up regression risk.** If a follow-up card's `New ground:`
    reads like restatement rather than new information, 2b is leaking
    the duplication 2a was built to prevent. Watch the first several
    runs that emit a follow-up; if more than \~1 in 3 reads as a
    restatement, tighten the "fresh signal" gate in
    `prompts/compose_plan.md` to require an explicit date or version
    number in the memo bullet. Companion risk: **callback fatigue** --
    if every brief opens with "last week's Pulse covered...", novelty
    collapses. The 1-per-run cap and the prompt's "memo bullet must name
    a fresh signal" gate are both load-bearing; loosening either
    re-opens the original over-suppression vs. callback-fatigue
    tradeoff.

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

-   **Don't run `pulse.sh` casually.** `3 + N` pi calls (where N is
    the planned card count, default cap 8), ~10 min, spends real
    Ollama Cloud tokens (currently unlimited per the user's
    subscription but that may change). The user explicitly invokes
    it; don't trigger it as a "test" without asking.

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
