# pi-pulse

Personalized daily Pulse brief built on Pi + Ollama Cloud. Inspired by
OpenAI's Pulse; every piece is local and replaceable. The repo is
private at github.com/ddarmon/pi-pulse.

## Pipeline

Five stages, three pi calls:

1.  **collect** -- `sources/collect_{obsidian,sesh,anki}.py` populate
    `.tmp/{chats,sesh,anki_signals}_recent.md`.
2.  **distill** -- `pi -p prompts/distill_context.md --no-skills` reads
    the three input bundles plus `memory/interests.md` and produces a
    five-section memo at `.tmp/interests_today.md` (Active threads, Open
    questions, Persistent interests, Study reinforcement, Avoid).
3.  **plan** -- `pi -p prompts/compose_plan.md --no-skills` picks
    exactly `PI_PULSE_CARDS_{TRACKED,ADJACENT,BRIDGE}` topics from the
    memo into `.tmp/plan.md` (default 5/2/1 = 8 cards).
4.  **expand** -- `pi -p prompts/compose_expand.md` writes one 250--400
    word prose card per planned topic into `out/YYYY-MM-DD.md`. Search
    is done via the `brave-search` skill (`search.js` for queries,
    `content.js` for fetches); the built-in `web_search` and `web_fetch`
    tools are explicitly forbidden in the prompt because their results
    are unbounded in size and have overflowed context before. Budget: at
    most one `search.js` + one `content.js` per card.
5.  **deliver** -- append URLs to `memory/seen_urls.jsonl`; copy brief
    to `$PI_PULSE_DELIVERY` if set.

## Cost and runtime awareness

A full `./pulse.sh` run takes 5--7 min and makes three pi calls against
`kimi-k2.6:cloud` (currently free on Ollama Cloud; the budget matters if
the provider changes). **Do not run pulse.sh speculatively** -- only
when the user explicitly asks for a test run.

## Conventions

-   **Prompt placeholders use double-brace syntax**, not `$NAME` -- see
    `prompts/compose_plan.md` for the in-file convention. Substitute
    placeholders in pulse.sh with `sed`, not `envsubst`. The launchd
    template uses the same double-brace convention.
-   **No personal data in committed files.** `memory/interests.md`,
    `memory/seen_urls.jsonl`, `out/`, `logs/`, `.pulse-sessions/`,
    `.env`, and `memory/interests.md.local` are all gitignored. The
    `.example` and `.template` counterparts are committed.
-   **Three-call budget.** `pulse.sh` invokes `pi` exactly three times
    (distill, plan, expand). Anything that wants to add a fourth call
    (e.g. profile auto-suggest) should be opt-in via env var.
-   **Commit messages.** Imperative subject, body explains motivation
    and surfaces what evidence drove the change. No `Co-Authored-By`
    lines (per global CLAUDE.md). No emoji.

## Debugging a run

-   `logs/YYYY-MM-DD/summary.md` -- per-stage wall time, tokens, tool
    calls, web-search queries, deduped URL list. Always read this first.
-   `logs/YYYY-MM-DD/{distill,plan,expand}.log.md` -- per-stage detail.
-   `logs/YYYY-MM-DD/*.err` -- raw stderr from each subprocess.
-   `.pulse-sessions/YYYY-MM-DD/{distill,plan,expand}/` -- full pi
    session JSONLs. Parse with `sources/inspect_session.py`.
-   `pulse.sh` exits 1 if any stage produces a 0-byte output, with logs
    preserved.

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

## Working on this repo

For the current state of remaining work, read `WHATS_NEXT.md`. Confirm
the user's intent before running `pulse.sh`. Prefer editing prompts and
pipeline scripts over adding new files. Don't add features the user
hasn't asked for.
