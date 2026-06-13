# pi-pulse

Personalized daily Pulse brief built on Pi + Ollama Cloud. Inspired by
OpenAI's Pulse; every piece is local and replaceable. The repo is
private at github.com/ddarmon/pi-pulse.

## Pipeline

Source-first: six stages, `3 + N` pi calls where N is the number of
card slots that survive plan. Plan picks from real evidence (scout's
discovered URLs), not imagined sources, so cards rarely drop in expand.

1.  **collect** -- `sources/collect_{obsidian,sesh,anki}.py` populate
    `.tmp/{chats,sesh,anki_signals}_recent.md`.
    `sources/build_recent_pulses.py` writes `.tmp/recent_pulses.md`
    (scout and plan both read it).
2.  **distill** -- `pi -p prompts/distill_context.md --no-skills` reads
    the three input bundles plus `memory/interests.md` and produces a
    five-section memo at `.tmp/interests_today.md` (Active threads, Open
    questions, Persistent interests, Study reinforcement, Avoid).
3.  **scout** -- `pi -p prompts/scout_signals.md` runs broad
    `brave-search` queries per interest cluster (memo bullets +
    durable-profile candidates from `interests.md`) and writes a
    structured signal sheet at `.tmp/signals_raw.md`: one entry per
    fresh primary source with `url`, `published`, `source_class`,
    `gloss`, `memo_anchor`, and `relation`
    (`memo-anchored` / `profile-adjacent` / `study-bridge`). Bounded
    by `PI_PULSE_SCOUT_MAX_INTERESTS` (default 12) and
    `PI_PULSE_SCOUT_QUERIES_PER_INTEREST` (default 2). Aggregator
    results (HN, Reddit, Twitter, link blogs) are rejected.
    `sources/filter_signals.py` then deterministically drops signals
    whose normalized URL is in `memory/seen_urls.jsonl` (already
    surfaced) or `memory/unfetchable_urls.jsonl` (committed before
    but the expand fetch failed), dedupes within the sheet, and
    writes the surviving pool to `.tmp/signals.md`. The scout prompt
    still receives the seen ledger so the model doesn't waste query
    budget, but ledger enforcement lives in code.
4.  **plan** -- `pi -p prompts/compose_plan.md --no-skills` ranks
    scout signals into card slots. Each slot's `Source URL:` is
    copied verbatim from `.tmp/signals.md` -- plan never invents URLs
    or topics. `PI_PULSE_CARDS_{TRACKED,ADJACENT,BRIDGE,FOLLOWUP}` are
    CAPS, not targets: on slow signal days the brief shrinks rather
    than padding. Default caps 5/2/1/1.
5.  **expand** -- `sources/split_plan.py` writes one
    `.tmp/expand/NN/slot.md` per planned card, verifying each slot's
    `Source URL:` against `.tmp/signals.md` (normalized comparison);
    a slot whose URL is missing or not in the sheet never reaches the
    manifest and is reported as a split-stage drop in
    `logs/${RUN_ID}/dropped.md`. `sources/expand_slot.sh`
    is invoked once per slot via `xargs -P
    $PI_PULSE_EXPAND_PARALLEL` (default 4). Each per-slot
    `pi -p prompts/compose_expand.md` fetches the committed Source URL
    via `brave-search` `content.js` (one fetch budgeted; one fallback
    `search.js` allowed only if the fetch 404s), then writes 250--400
    words of prose to `.tmp/expand/NN/body.md`. The built-in
    `web_search`/`web_fetch` tools are forbidden -- their results are
    unbounded and have overflowed context before. Drops are reported
    on stderr (`DROPPED slot=NN reason=...`) and aggregated into
    `logs/${RUN_ID}/dropped.md` -- never into the delivered brief.
6.  **deliver** -- stitch `.tmp/expand/theme.md` (lifted from plan)
    with each non-empty per-slot body into `out/${RUN_ID}.md`
    (where `RUN_ID` is `YYYY-MM-DD-HHMM`); append URLs to
    `memory/seen_urls.jsonl`; record fetch-failed slots' Source URLs
    in `memory/unfetchable_urls.jsonl` via
    `sources/append_unfetchable.py` (model-emitted drops only --
    `pi-exit-nonzero` and malformed bodies are skipped, and the step
    is skipped entirely when every slot dropped, since that usually
    means a systemic failure rather than bad URLs); render
    `out/${RUN_ID}.html` from the
    markdown via `sources/render_html.py` (pandoc preferred, Python
    `markdown` fallback, MathJax loaded only when math is detected);
    write an editable feedback companion file at
    `out/${RUN_ID}.feedback.md` via
    `sources/build_feedback_template.py` (one numbered line per
    delivered card; generation is non-fatal); copy `.md`, `.html`, and
    `.feedback.md` to `$PI_PULSE_DELIVERY` if set.

## Cost and runtime awareness

A full `./pulse.sh` run takes roughly 8--12 min and makes `3 + N` pi
calls against `kimi-k2.6:cloud`, where N is the planned card count
(default cap 8). Per-card expand calls run in parallel
(`PI_PULSE_EXPAND_PARALLEL`, default 4), so wall time scales with
`max(per-card)`, not the sum. Operating assumption: unlimited Ollama
subscription, so the call count is not budgeted. **Reintroduce a hard
budget if the provider changes.** **Do not run pulse.sh speculatively**
-- only when the user explicitly asks for a test run.

## Conventions

-   **Prompt placeholders use double-brace syntax**, not `$NAME` -- see
    `prompts/compose_plan.md` for the in-file convention. Substitute
    placeholders in pulse.sh with `sed`, not `envsubst`. The launchd
    template uses the same double-brace convention.
-   **No personal data in committed files.** `memory/interests.md`,
    `memory/seen_urls.jsonl`, `memory/unfetchable_urls.jsonl`,
    `memory/feedback.jsonl`, `out/`, `logs/`, `.pulse-sessions/`,
    `.env`, and `memory/interests.md.local` are all gitignored. The
    `.example` and `.template` counterparts are committed.
-   **Pi-call count is variable.** `pulse.sh` invokes `pi` for distill,
    scout, plan, and once per planned card slot in parallel expand
    (capped by `PI_PULSE_EXPAND_PARALLEL`). This is the source-first
    pipeline; it relies on an unlimited Ollama subscription. If the
    provider changes, reintroduce a hard call budget in `pulse.sh`.
-   **Card quotas are caps, not targets** (except the bridge
    minimum). Plan emits fewer cards when scout returns fewer grounded
    signals. A short, fully grounded brief is the goal -- never
    invent a topic to fill a slot. The bridge slot has a minimum of
    1 to protect foundational/theoretical content from being crowded
    out by news-shaped signals on infrastructure-heavy days.
-   **One RUN_ID per invocation.** Every `pulse.sh` run owns a
    `RUN_ID` of the form `YYYY-MM-DD-HHMM` (set from the wall clock,
    or overridden via `PI_PULSE_RUN_ID` for backfill or retry).
    Output, logs, and session archives are all keyed on `RUN_ID`,
    so multiple pulses can land on the same day without clobbering
    each other. `.tmp/` is shared scratch; the lockfile
    `.tmp/.pulse.lock` rejects concurrent runs. Legacy briefs named
    `YYYY-MM-DD.md` (pre-RUN_ID) are still picked up by
    `build_recent_pulses.py`.
-   **Drop info lives in logs only.** `logs/${RUN_ID}/dropped.md`
    captures per-slot drops; the delivered brief in `out/` never
    contains a `## Dropped from this run` section.
-   **Commit messages.** Imperative subject, body explains motivation
    and surfaces what evidence drove the change. No `Co-Authored-By`
    lines (per global CLAUDE.md). No emoji.

## Feedback loop

Each run emits `out/${RUN_ID}.feedback.md`: one numbered line per
delivered card (card N is the Nth `## ` heading in the brief), each
prefixed with an editable mark. The reader edits the marks
(best-to-worst `[++]`/`[+]`/`[=]`/`[-]`/`[--]`, plus `[ ]`; optional
indented `note:` line) by hand
or via the interactive reviewer `scripts/review-feedback.sh` (a
zero-dependency single-keypress TUI, `sources/review_feedback.py`, that
shows each card's prose from the brief and writes marks back to the
`.feedback.md`; default queue is every unrated card across all briefs,
oldest first; pass a `RUN_ID` for one brief or `--include-rated` to
revisit). Either way, **`pulse.sh` auto-ingests on the next run** (step
1c calls
`scripts/ingest-feedback.sh --all`, sweeping every `out/*.feedback.md`),
so manual ingest is normally unnecessary. Run
`scripts/ingest-feedback.sh [RUN_ID]` by hand only to pick edits up
immediately. Ingest re-joins each rated card against the brief to
recover its title, normalized primary URL, and tag, then writes rows to
`memory/feedback.jsonl` (gitignored). Ingest is **idempotent** -- it
drops a run's existing rows before re-adding, so sweeping all files
every run is safe (unedited files contribute zero rows; already-ingested
rows are replaced, not duplicated). It then rebuilds
`.tmp/feedback_recent.md` (last 14 days, grouped valued / neutral /
not-valued / avoid-candidates) via `build_feedback_digest.py`. `[=]`
neutral is a distinct *rated* state (rating 0: reviewed, no strong
opinion) and produces a ledger row; `[ ]` unrated means not yet
reviewed and is skipped. The whole path makes
**zero model calls**. Note a run cannot ingest its *own* feedback file
(written at deliver, all-unrated at that point) -- today's edits are
swept up by tomorrow's run.
The digest is consumed by the profile-suggest stage (below). Unrated
(`[ ]`) cards are skipped; `[--]` does not auto-suppress a topic (the URL
is already in `seen_urls.jsonl`), it only flags an avoid-candidate.

## Profile-suggest (weekly, manual)

A human-gated weekly step that surfaces drift between the durable
profile (`memory/interests.md`) and what the user has actually been
doing. It is decoupled from `pulse.sh` (no daily-run latency); the only
daily-run touch is step 2b archiving each memo to `logs/${RUN_ID}/memo.md`.

`scripts/suggest-profile.sh [DAYS]` (default 7) refreshes the feedback
digest, builds an input bundle via `sources/build_suggest_input.py`
(last N days of archived memos, falling back to recent `out/*.md`
briefs until memos accrue, plus `.tmp/feedback_recent.md`), and runs one
no-tools `pi` call on `prompts/suggest_profile.md` to emit 0--6
machine-parseable proposals (`ADD`/`EDIT`/`DEMOTE`) to
`.tmp/profile_updates.md`. It never edits the profile.

**This stage uses `gemma4:31b-cloud`, not the pipeline's
`kimi-k2.6:cloud`** (override `PI_SUGGEST_MODEL`). This is load-bearing:
kimi emits ~72k chars of inline chain-of-thought on this evaluative task
regardless of `--thinking` (`off` only suppresses reasoning on trivial
prompts), saturating the fixed 16,384-token output cap and emitting no or
truncated proposals across three live runs. gemma4 is a non-reasoning
instruct model that returned clean proposals in ~6s / ~450 output tokens.
pi exposes no max-output-tokens flag, so a non-reasoning model is the
fix. The brief fallback is also condensed to titles + lede
(`build_suggest_input.condense_brief`) to keep the input compact. If you
point `PI_SUGGEST_MODEL` at a reasoning model, set `PI_SUGGEST_THINKING`
accordingly (default: unset, no `--thinking` flag passed).

`scripts/apply-updates.sh` (-> `sources/apply_updates.py`) walks the
proposals interactively (`y`/`n`/`q`), snapshots the profile to
`memory/interests-history/` before any write (same pattern as
`interview.sh`), applies accepted ADDs (append bullet under the named
section) and EDIT/DEMOTE (locate the target bullet by
whitespace-normalized match, so wrapped bullets match; ambiguous or
missing targets are reported for manual handling, never guessed), and
prints a unified diff. `--dry-run` shows changes without writing. Both
scripts make zero model calls except the single suggest `pi` call.
There is no cron yet -- run it weekly when convenient.

## Debugging a run

-   `logs/${RUN_ID}/summary.md` -- per-stage wall time, tokens, tool
    calls, web-search queries, deduped URL list. Always read this first.
-   `logs/${RUN_ID}/{distill,scout,plan,expand}.log.md` -- per-stage
    detail. `expand.log.md` concatenates per-slot session digests.
-   `logs/${RUN_ID}/dropped.md` -- which expand slots dropped and why
    (empty `(none)` on a clean run).
-   `logs/${RUN_ID}/*.err` -- raw stderr from each subprocess.
-   `.pulse-sessions/${RUN_ID}/{distill,scout,plan,expand}/` -- full
    pi session JSONLs. Parse with `sources/inspect_session.py`. Expand
    has one subdirectory per slot (`expand/01/`, `expand/02/`, ...).
-   `.tmp/signals_raw.md` -- scout's structured signal sheet as the
    model emitted it; `.tmp/signals.md` -- the post-ledger-filter pool
    plan picks from. `logs/${RUN_ID}/filter-signals.err` lists each
    `FILTERED signal=... reason=...` line plus kept/total counts.
-   `.tmp/expand/NN/{slot.md,body.md,err.log}` -- per-slot plan
    fragment, card body, and stderr (including any `DROPPED` line).
-   `pulse.sh` exits 1 if distill, scout, or plan produces 0-byte
    output, if the ledger filter drops every scout signal, or if
    every expand slot drops. Logs are preserved.

## Known constraints

-   `kimi-k2.6:cloud` context is 262k tokens. The built-in `web_search`
    tool returns unbounded results (one call was observed at 1.1M
    chars), which is why `compose_expand.md` mandates the `brave-search`
    skill instead: its `search.js` and `content.js` return size-bounded
    markdown. Do not reintroduce `web_search`/`web_fetch` without a
    size-bounding plan.
-   `sesh` requires a built index. `collect_sesh.py` runs `sesh refresh`
    before `sesh sessions` (idempotent, \~5s). Removing that call will
    silently break `pulse.sh` under `set -e`.
-   `pi --session-dir` routes session JSONLs off
    `~/.pi/agent/sessions/`, which is sesh's canonical discovery path.
    Without this, today's pulse run would feed tomorrow's distill via
    `collect_sesh.py`.
-   `pulse.sh` passes `--exclude-cwd "$PWD"` to `collect_sesh.py`, which
    drops sessions whose `project_path` is the repo root or a
    descendant. This stops meta-recursion: claude-code conversations,
    `scripts/interview.sh` runs, and ad-hoc debugging done while `cd`'d
    into the repo no longer feed tomorrow's distill as an "active
    thread." Trade-off: any unrelated session you run while `cd`'d into
    this repo is also excluded.

## Working on this repo

For the current state of remaining work, read `WHATS_NEXT.md`. Confirm
the user's intent before running `pulse.sh`. Prefer editing prompts and
pipeline scripts over adding new files. Don't add features the user
hasn't asked for.
