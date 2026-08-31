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
2.  **distill** -- `pi -p prompts/distill_context.md --no-tools
    --no-context-files --no-extensions --no-skills` reads
    the three input bundles plus `memory/interests.md` and produces a
    five-section memo at `.tmp/interests_today.md` (Active threads, Open
    questions, Persistent interests, Study reinforcement, Avoid).
3.  **scout** -- `pi -p prompts/scout_signals.md` runs with no built-in
    tools and only the in-repo `search`/`fetch` broker extension. It probes
    Brave Search per interest cluster (redacted memo bullets +
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
    writes the surviving pool to `.tmp/signals.md`. It also blocks a
    whole **host** once the unfetchable ledger holds
    `--host-block-threshold` (default 2) distinct failed URLs for it,
    keyed on the host the fetch actually died at: a publisher that
    refuses us (MDPI 403'd three times across two papers and an alias
    in one fortnight) is a property of the source, not of the URL, so
    a per-URL ledger never learns it. DOI-style resolvers are never
    host-blocked -- they front every publisher, so banning one would
    ban a whole class of academic signals. The scout prompt
    still receives the seen ledger so the model doesn't waste query
    budget, but ledger enforcement lives in code.
4.  **plan** -- `pi -p prompts/compose_plan.md --no-tools
    --no-context-files --no-extensions --no-skills` ranks
    scout signals into card slots. Each slot's `Source URL:` is
    copied verbatim from `.tmp/signals.md` -- plan never invents URLs
    or topics. `PI_PULSE_CARDS_{TRACKED,ADJACENT,BRIDGE,FOLLOWUP}` are
    CAPS, not targets: on slow signal days the brief shrinks rather
    than padding. Default caps 5/2/1/1. The reader-feedback digest
    (`.tmp/feedback_recent.md`) is attached as a ranking prior:
    valued topics break ties, not-valued topics are down-ranked,
    avoid-candidates need a fresh dated signal -- always within the
    quotas, never to pad. A per-thread diversity cap (at most 2 cards
    per `memo_anchor`/thread) binds before feedback steering so
    "more like this" cannot concentrate the brief onto one thread.
5.  **expand** -- `sources/split_plan.py` writes one
    `.tmp/expand/NN/slot.md` per planned card, verifying each slot's
    `Source URL:` against `.tmp/signals.md` (normalized comparison);
    a slot whose URL is missing or not in the sheet never reaches the
    manifest and is reported as a split-stage drop in
    `logs/${RUN_ID}/dropped.md`. `sources/expand_slot.sh`
    is invoked once per slot via `xargs -P
    $PI_PULSE_EXPAND_PARALLEL` (default 4). Each per-slot
    guard fetches the manifest's committed Source URL before Pi starts
    (one bounded fetch; one bounded search fallback on failure). The
    resulting `page.md` is attached to a sealed no-tools expand call,
    which writes 250--400 words to `.tmp/expand/NN/body.md`. Drops are reported
    on stderr (`DROPPED slot=NN reason=...`) and aggregated into
    `logs/${RUN_ID}/dropped.md` -- never into the delivered brief.
6.  **deliver** -- stitch `.tmp/expand/theme.md` (lifted from plan)
    with each non-empty per-slot body into `out/${RUN_ID}.md`
    (where `RUN_ID` is `YYYY-MM-DD-HHMM`); append URLs to
    `memory/seen_urls.jsonl`; record fetch-failed slots' Source URLs
    in `memory/unfetchable_urls.jsonl` via
    `sources/append_unfetchable.py` (model-emitted drops plus every
    slot that shipped snippet-grounded, since its committed source
    refused us just as surely and that failure produces no drop;
    `pi-exit-nonzero` and malformed bodies are skipped, and the step
    is skipped entirely when every slot dropped, since that usually
    means a systemic failure rather than bad URLs). Each row carries
    the `host` the fetch died at, read from `logs/${RUN_ID}/egress.log`
    rather than parsed from the URL, so a committed doi.org alias
    records the publisher behind it; render
    `out/${RUN_ID}.html` from the
    markdown via `sources/render_html.py` (pandoc preferred, Python
    `markdown` fallback, output-tree allowlist sanitizer, pinned local
    MathJax loaded only when math is detected);
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
-   **Per-stage model selection.** Each stage (distill, scout, plan,
    expand) takes independent `PI_PULSE_<STAGE>_{MODEL,PROVIDER,THINKING}`
    overrides, each falling back to the global `PI_MODEL`/`PI_PROVIDER`.
    As of ~2026-06-18 the local `.env` runs **all four stages on
    `glm-5.2:cloud`** (top open-weight model as of June 2026). (Earlier the
    pipeline split distill+plan onto `minimax-m3:cloud` and scout+expand on
    `kimi-k2.6:cloud` because kimi saturated the harder synthesis prompts;
    both entries remain in `models.json` as alternatives.)
-   **glm-5.2 thinking-runaway and the scout fix.** glm-5.2 is a reasoning
    model and on a heavy synthesis turn it *intermittently* (~20%, measured
    2026-06-27/28) ends inside its `thinking` channel and emits **no answer
    text** -- `stopReason=stop`, `usage.output=0` -- so the stage writes
    0 bytes and the run aborts. It is stochastic: a fresh sample almost
    always succeeds. Two mitigations are in place:
    1.  **Scout runs with thinking OFF** (`PI_PULSE_SCOUT_THINKING=off`),
        which makes it **stall-proof by construction** (no reasoning channel
        to run away in) and, in a live test (2026-06-28), also *faster* with
        an equal-or-fuller signal sheet. This REQUIRES a `thinkingLevelMap`
        on the `glm-5.2:cloud` entry, which now lives in the **repo-owned**
        catalog `pi-agent/models.json.template` (see below), not in
        `~/.pi/agent/models.json`.
        Without it, pi's bare `--thinking off` **omits** the field over the
        OpenAI-compat endpoint and the model still thinks (it does NOT
        disable). With it, pi sends `reasoning_effort=none`. Measured
        2026-08-26 on identical prompts: with the map, 0 chars of thinking;
        without it, 226. `--thinking low` still thinks (231 chars), so the
        map is a real switch, not blanket suppression.
    2.  **distill/plan keep thinking on** (ranking/synthesis benefit from it)
        but are wrapped by `run_pi_retry` in `pulse.sh`, which resamples on
        0-byte output up to `PI_PULSE_SYNTH_RETRIES` (default 3). scout is
        wrapped too as belt-and-suspenders.
-   **The Pi model catalog is repo-owned.** `pulse.sh` and
    `scripts/suggest-profile.sh` render `pi-agent/models.json.template`
    (double-brace `{{OLLAMA_BASE_URL}}`, from `PI_PULSE_OLLAMA_BASE_URL`)
    into the gitignored `.pi-agent/` and export
    `PI_CODING_AGENT_DIR` so pi reads that catalog instead of
    `~/.pi/agent/models.json`. `auth.json`, `trust.json`, and
    `settings.json` are symlinked back to the real agent dir --
    **trust.json is load-bearing**: an untrusted repo makes pi prompt on
    the first tool call, which once stalled scout ~10h.
    Why: the machine-global file is rewritten by `ollama launch pi` and
    `pi update` (twice on 2026-08-26 alone), and a launcher-written entry
    carries **no `thinkingLevelMap` and no `contextWindow`**. From
    2026-08-15 to 2026-08-26 there was no `glm-5.2:cloud` entry at all:
    pi warned `Using custom model id`, passed the id through to Ollama --
    so runs *worked* -- while `PI_PULSE_SCOUT_THINKING=off` silently did
    nothing and pi assumed its 128k default against a 1M-token model.
    `sources/check_models.py` runs before the first pi call (step 0a2)
    and fails the run if any stage's model is absent, lacks a
    `contextWindow`, or asks for a thinking level the catalog would drop;
    stderr lands in `logs/${RUN_ID}/check-models.err`. `contextWindow`
    values come from Ollama itself
    (`curl -s $OLLAMA/api/show -d '{"model":"<id>"}'` ->
    `model_info.*.context_length`); glm-5.2 is 1048576, not the 262144
    that a stale entry may claim. To try a new model, add it to the
    template -- editing `~/.pi/agent/models.json` no longer affects a run.
-   **pi -> Ollama thinking plumbing (gotchas).** pi has no native Ollama
    provider; `ollama` in `models.json` is a user-defined OpenAI-compat
    provider, so pi sends the thinking level as `reasoning_effort` over
    `/v1/chat/completions` (NOT Ollama's native `/api/chat` `think`). Ollama
    maps `reasoning_effort` -> internal `Think`; valid values are
    **`high|medium|low|max|none`** (`none` => thinking off). pi's own labels
    are `off|minimal|low|medium|high|xhigh`; by default `off` is omitted (not
    sent as `none`), `xhigh` clamps to `high` (never reaches `max`), and
    **`minimal` is sent verbatim and 400s the request** -- so a `thinkingLevelMap`
    is the only way to reach `none`/`max`, and **`*_THINKING=minimal` must
    never be set** (the 400 -> 0 bytes -> aborts the run).
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
not-valued / avoid-candidates) via `build_feedback_digest.py`. The
14-day window keys on each card's **delivery date** (the leading
`YYYY-MM-DD` of its `run_id`), not the rating date, so a bulk rating
session that rates months of old cards in one day does not make them
look current; the fallback is the `date` field only when `run_id` is
missing/unparseable. Digest lines with title `Dropped from this run`
(ratings against legacy briefs' dropped sections) are filtered out.
`[=]`
neutral is a distinct *rated* state (rating 0: reviewed, no strong
opinion) and produces a ledger row; `[ ]` unrated means not yet
reviewed and is skipped. The whole path makes
**zero model calls**. Note a run cannot ingest its *own* feedback file
(written at deliver, all-unrated at that point) -- today's edits are
swept up by tomorrow's run.
The digest is consumed daily by the plan stage (attached to the
compose_plan pi call as a ranking prior, with a `## Tendencies`
per-tag summary up top; pulse.sh logs a one-line census of it before
plan and guarantees a stub exists so the attachment never dangles) and
weekly by the profile-suggest stage (below). For the daily plan path
the digest is capped at `PI_PULSE_FEEDBACK_DIGEST_MAX` rows per section
(default 20; `--max-per-section`, 0 = unlimited) -- an uncapped ~184-row
digest tripled the plan call's output and latency; `## Tendencies` is
still computed over every in-window row, not the capped subset. The
weekly profile-suggest path passes no cap (full digest). Unrated
(`[ ]`) cards are skipped; `[--]` does not auto-suppress a topic (the URL
is already in `seen_urls.jsonl`), it only flags an avoid-candidate --
plan may still cover such a topic when today's memo names a fresh,
dated signal, and must say so in the slot's rationale.

A third rating path is the **feedback web server**
(`scripts/feedback-server.sh` -> `sources/feedback_server.py`, stdlib
only, zero dependencies): it serves an index of briefs and each
`out/${RUN_ID}.html` with a rating bar injected under every card
(marks + one-line note), and writes edits into the same
`out/${RUN_ID}.feedback.md` grammar via `POST /api/rate` (locked,
atomic rewrite through `review_feedback.parse_feedback_file`/
`serialize_feedback` -- never a second grammar). Intended deployment is
the user's Tailscale network: set `PI_PULSE_FEEDBACK_HOST=tailscale` in
`.env` (autodetects the tailnet IPv4; default bind is `127.0.0.1`;
`PI_PULSE_FEEDBACK_PORT` default 8377) and load
`launchd/com.user.pi-pulse-feedback.plist.template` (KeepAlive) to run
it persistently. Security model: the tailnet is the auth boundary;
defense-in-depth is a client-IP allowlist (loopback + `100.64.0.0/10`
+ Tailscale IPv6 ULA -- everything else 403s even if misbound),
strict `RUN_ID` regex on every path construction, a 16 KB POST cap,
an exact same-origin check plus JSON content-type gate on rating POSTs,
and a nonce-based CSP on brief pages. Only pinned local MathJax assets
are served from a fixed asset route. The server only ever writes
`out/*.feedback.md`; ingest sweeps them on the next run as usual.

**Deployment state (this machine):** the server is INSTALLED and
running under launchd as `com.user.pi-pulse-feedback` (plist at
`~/Library/LaunchAgents/`, TCC-safe deployment installed 2026-07-20),
bound to the Tailscale IP via `PI_PULSE_FEEDBACK_HOST=tailscale` in
`.env`. `scripts/install-feedback-server.sh` installs a stable native
wrapper at `~/Applications/Pi Pulse Feedback.app`; that app has the
Documents-folder consent needed to reach this checkout after a cold
launch. The LaunchAgent must point at the native wrapper and keep its
logs under `~/Library/Logs/pi-pulse/` -- pointing launchd directly at
the repo's bash/Python files reintroduces the post-reboot TCC failure.
KeepAlive restarts it on crash and at login -- but NOT on code change:
after editing `sources/feedback_server.py` (or `.env`), run
`launchctl kickstart -k gui/$UID/com.user.pi-pulse-feedback` or the old
code keeps serving. Never start a second instance manually while the
launchd job holds the port (EADDRINUSE crash-loop). Logs:
`~/Library/Logs/pi-pulse/feedback-server.{out,err}.log`. User-facing
setup steps live in README.md ("Rate cards from your phone").

**Scheduled pulse deployment (this machine):** the daily 05:00 run is
installed via `scripts/install-pulse-agent.sh` as
`com.user.pi-pulse`, pointing at the native wrapper
`~/Applications/Pi Pulse.app` (same TCC pattern as the feedback
server). The wrapper is required: pointing launchd directly at
bash/pulse.sh leaves node without Documents consent on a cold start, so
every expand guard fetch dies with EPERM and the egress audit aborts
the run. Job stdout/stderr: `~/Library/Logs/pi-pulse/pulse.{out,err}.log`
(per-run logs stay in `logs/<RUN_ID>/`). The LaunchAgent bakes in the
installing shell's PATH -- re-run the installer after node/uv/pi path
changes. `--rebuild-app` recompiles the wrapper, which changes its code
identity and re-prompts for Documents consent.

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
-   `logs/${RUN_ID}/grounding.md` -- how many delivered cards came from the
    committed primary source vs the search-snippet fallback, naming each
    snippet-grounded slot. A fallback card never appears in `dropped.md`,
    so this is the only place that degradation is visible.
-   `logs/${RUN_ID}/egress.log` -- append-only JSONL of every guarded
    outbound attempt; `egress.md` is the post-run invariant/provenance audit.
-   `logs/${RUN_ID}/capabilities.jsonl` -- prompt-free evidence of provider,
    model, thinking level, and security flags extracted from each exact Pi
    invocation.
-   `logs/${RUN_ID}/check-models.err` -- the preflight catalog verdict. A
    failure here aborts the run before the first pi call and names exactly
    which stage/model/thinking level the catalog cannot support.
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
    output *after `PI_PULSE_SYNTH_RETRIES` attempts* (default 3; each
    stage is wrapped by `run_pi_retry`, which resamples on empty output
    -- see the glm-5.2 thinking-runaway note above), if the ledger
    filter drops every scout signal, if the egress audit fails, or if every
    expand slot drops.
    Logs are preserved. A retried stage logs
    `<stage>: EMPTY output on attempt N/M ...` / `recovered on attempt N`.

## Known constraints

-   `kimi-k2.6:cloud` context is 262k tokens. The built-in `web_search`
    tool once returned 1.1M chars, and the former external `content.js`
    downloaded full responses before parsing. Neither is used now: the
    in-repo broker caps queries and streams response bytes under a hard
    limit before content enters model context. A **page** that exceeds
    the 2 MB cap is now truncated at it rather than refused: throwing
    cost two live runs their primary source on ordinary articles that
    were merely fat, and only `MAX_PAGE_CHARS` of extracted text ever
    reaches a model. The socket is still destroyed at exactly the cap,
    and the **search** JSON still fails hard, since a truncated payload
    would not parse.
-   **PDF sources are extracted, not fetched as text.** The broker's
    content-type allowlist admits `application/pdf` and
    `sources/brave-guard/pdf.js` recovers the text with Node's builtin
    `zlib` (no dependency): an academic PDF's prose lives in
    FlateDecode-compressed content streams, so the response bytes carry
    none of it and there is nothing a model could summarize from them.
    Inflates are capped per stream and per document (decompression
    bombs), the scanner is a single linear pass (an early regex version
    hung on binary input), and streams whose text is not mostly printable
    ASCII are dropped so images/fonts cannot leak noise into context.
    Scanned image-only PDFs are refused as having no text layer and the
    slot drops, which is correct. CID/Type0 fonts and math glyphs garble.
    Between 2026-08-08 (`519301b`) and this change the allowlist rejected
    PDFs outright, so every PDF source silently degraded to a Brave
    snippet -- the population most affected is foundational/theory
    material, historically the best-rated cards.
-   **Snippet-grounded cards are reported, not silent.** When a slot's
    primary fetch fails, `expand_slot.sh` still writes a card from the
    search-snippet fallback and records `search-fallback` in
    `.tmp/expand/NN/grounding`. That is a real quality degradation which
    produces no drop, so `pulse.sh` writes a census to
    `logs/${RUN_ID}/grounding.md`, adds a `- grounding:` line to
    `summary.md`, and logs a WARN naming each snippet-grounded slot.
    Such a slot is also written to the unfetchable ledger at deliver,
    so the refusal steers later runs instead of only being reported.
-   **Single-label hostnames are refused** by `sources/url_policy.py`
    and the broker alike. A doubled scheme
    (`https://https://arxiv.org/...`) parses as the legal host `https`;
    one reached a live manifest, DNS-failed at fetch, and silently cost
    that slot its primary source. Keep the two gates in step: a URL
    that clears the Python filter but is refused by the broker logs no
    hop-0 attempt, and the egress audit then fails the whole run.
-   Full run history is preserved by default. `PI_PULSE_RETENTION_DAYS=0`
    disables pruning; setting a positive value opts into deletion of only
    date-shaped children under `logs/` and `.pulse-sessions/`.
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
