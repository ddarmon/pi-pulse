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
    structured signal sheet at `.tmp/signals.md`: one entry per fresh
    primary source with `url`, `published`, `source_class`, `gloss`,
    `memo_anchor`, and `relation`
    (`memo-anchored` / `profile-adjacent` / `study-bridge`). Bounded
    by `PI_PULSE_SCOUT_MAX_INTERESTS` (default 12) and
    `PI_PULSE_SCOUT_QUERIES_PER_INTEREST` (default 2). Aggregator
    results (HN, Reddit, Twitter, link blogs) are rejected. URLs in
    `memory/seen_urls.jsonl` are filtered out here.
4.  **plan** -- `pi -p prompts/compose_plan.md --no-skills` ranks
    scout signals into card slots. Each slot's `Source URL:` is
    copied verbatim from `.tmp/signals.md` -- plan never invents URLs
    or topics. `PI_PULSE_CARDS_{TRACKED,ADJACENT,BRIDGE,FOLLOWUP}` are
    CAPS, not targets: on slow signal days the brief shrinks rather
    than padding. Default caps 5/2/1/1.
5.  **expand** -- `sources/split_plan.py` writes one
    `.tmp/expand/NN/slot.md` per planned card; `sources/expand_slot.sh`
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
    `memory/seen_urls.jsonl`; render `out/${RUN_ID}.html` from the
    markdown via `sources/render_html.py` (pandoc preferred, Python
    `markdown` fallback, MathJax loaded only when math is detected);
    copy both `.md` and `.html` to `$PI_PULSE_DELIVERY` if set.

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
    `memory/seen_urls.jsonl`, `out/`, `logs/`, `.pulse-sessions/`,
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
-   `.tmp/signals.md` -- scout's structured signal sheet (the candidate
    pool plan picks from).
-   `.tmp/expand/NN/{slot.md,body.md,err.log}` -- per-slot plan
    fragment, card body, and stderr (including any `DROPPED` line).
-   `pulse.sh` exits 1 if distill, scout, or plan produces 0-byte
    output, or if every expand slot drops. Logs are preserved.

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
