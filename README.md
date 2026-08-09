# pi-pulse

A personalized daily brief, built on
[Pi](https://github.com/badlogic/pi-mono) and Ollama Cloud-hosted
models. Inspired by OpenAI's Pulse, but every piece is local and
replaceable.

Each morning the pipeline:

1.  **Collects** the last 30 days of your exported LLM chats (any
    `YYYY/MM/DD/*.md` tree -- e.g. an Obsidian vault), the last 7 days
    of [`sesh`](https://github.com/ddarmon/sesh) coding sessions, and
    three Anki signals (recent reviews, recent additions, leeches).
2.  **Distills** them into an interest memo via a sealed, no-tools Pi call.
3.  **Scouts** primary sources through an in-repo broker exposing only
    bounded, logged `search` and `fetch` tools; private network targets are
    refused.
4.  **Plans** the day's brief in a second sealed Pi call, committing every
    card to a URL from the scout sheet.
5.  **Expands** each planned topic into a 250--400 word mini-essay after the
    committed URL is fetched deterministically; expand itself has no tools.
6.  **Delivers** sanitized HTML and Markdown to `out/YYYY-MM-DD-HHMM.*`
    and, if configured, to
    a separate delivery directory (e.g. your Obsidian vault).

## Layout

```
pulse.sh                    entrypoint (manual or launchd)
scripts/interview.sh        interactive profile interviewer
prompts/                    distill, plan, expand, interview templates
sources/                    notes / sesh / anki collectors, URL dedup,
                            session inspector
memory/interests.md         durable profile (gitignored)
memory/interests.md.example template to copy from
memory/interests-history/   profile snapshots from interview.sh (gitignored)
memory/seen_urls.jsonl      dedup ledger (auto-appended, gitignored)
out/                        local copy of each day's brief (gitignored)
logs/YYYY-MM-DD/            per-run logs (gitignored): distill.log.md,
                            plan.log.md, expand.log.md, summary.md, *.err
.pulse-sessions/YYYY-MM-DD/ pi session archive (gitignored), kept off
                            sesh's discovery path so today's run does
                            not feed tomorrow's distill
launchd/                    com.user.pi-pulse.plist.template
.env.example                config template
```

## Prerequisites

-   [`pi`](https://github.com/badlogic/pi-mono) installed on `$PATH`.
-   A Pi provider + model. The defaults assume `kimi-k2.6:cloud` is
    configured under an `ollama` provider in `~/.pi/agent/models.json`,
    but anything Pi understands works. Set `PI_PROVIDER` / `PI_MODEL` to
    override.
-   Node.js 20+ for the dependency-free guarded Brave Search broker.
-   A Brave Search API key stored as `BRAVE_API_KEY` in `.env`. The broker
    reads it directly; model processes do not inherit it.
-   [`sesh`](https://github.com/ddarmon/sesh) on `$PATH` for the coding
    session collector.
-   [Anki](https://apps.ankiweb.net/) desktop with the
    [AnkiConnect](https://ankiweb.net/shared/info/2055492159) plugin
    running. The pipeline degrades gracefully if Anki is closed.
-   The `anki_search.py` helper from the [anki-search
    skill](https://github.com/badlogic/pi-skills) -- or any compatible
    script that exposes the same `search <query>   --json` interface.
-   [`uv`](https://docs.astral.sh/uv/) for running the Python collectors
    (stdlib only, no extra deps).

## Configure

```bash
cp .env.example .env                                    # then edit paths
cp memory/interests.md.example memory/interests.md      # then edit profile
```

At a minimum, set `PI_PULSE_NOTES_DIR` in `.env` to the root of your
`YYYY/MM/DD/*.md` notes tree.

For the profile, you can either hand-edit `memory/interests.md` or run
the interactive interview, which seeds it on first run and rewrites it
in place on subsequent runs:

```bash
./scripts/interview.sh
```

The previous profile is snapshotted to `memory/interests-history/`
before each interview, and a diff is printed on exit.

## Run manually

```bash
./pulse.sh
```

The brief lands at:

-   `out/YYYY-MM-DD-HHMM.md` (always)
-   `$PI_PULSE_DELIVERY/YYYY-MM-DD-HHMM.md` (if `PI_PULSE_DELIVERY` is set)

Full logs and Pi session history are preserved by default. Retention is
strictly opt-in: set `PI_PULSE_RETENTION_DAYS` to a positive number only if
you want old date-stamped private run artifacts pruned; `0` keeps everything.

## Schedule daily (macOS launchd)

```bash
# Builds ~/Applications/Pi Pulse.app (a tiny native wrapper that owns the
# TCC Documents-folder consent), asks for that consent once via a macOS
# dialog, and installs the LaunchAgent pointing at the app. Default
# schedule is 05:00; override with --hour/--minute.
scripts/install-pulse-agent.sh

# Trigger an immediate run without waiting:
launchctl kickstart gui/$UID/com.user.pi-pulse

# Tail job logs (per-run detail stays in logs/<RUN_ID>/ in the repo):
tail -f ~/Library/Logs/pi-pulse/pulse.{out,err}.log

# Disable:
launchctl bootout gui/$UID/com.user.pi-pulse
```

The app wrapper matters when the checkout lives under `~/Documents` (or
another TCC-protected folder): launchd pointed directly at bash gets no
consent prompt on a cold start, node's guard fetches fail with `EPERM`,
and the run aborts at the egress audit. The wrapper is the stable TCC
identity; every stage child (bash, python, node, pi) runs beneath it.
Re-run the installer after toolchain PATH changes (e.g. a new node
version); add `--rebuild-app` only if the wrapper source changed, since
recompiling changes its code identity and re-prompts for consent.

On Linux, write a systemd timer with `Persistent=true` against
`pulse.sh` instead; the script itself has no macOS-specific dependencies
beyond the launchd template.

## Rate cards from your phone (feedback server)

A small stdlib-only web server serves each brief with a rating bar
under every card and writes marks into the same `out/*.feedback.md`
files the nightly ingest sweeps. Intended deployment is a private
[Tailscale](https://tailscale.com) network: the tailnet provides
transport security and authentication, and the server refuses any
client that is not loopback or a Tailscale address even if misbound.
Rating writes additionally require same-origin JSON requests. Briefs run
under a nonce-based CSP, and math uses the integrity-checked local MathJax
build in `vendor/mathjax/` rather than a CDN.

```bash
# In .env: bind the machine's Tailscale IP (autodetected via the
# tailscale CLI). Default is 127.0.0.1 (loopback only).
#   PI_PULSE_FEEDBACK_HOST=tailscale
#   PI_PULSE_FEEDBACK_PORT=8377   # default

# Run in the foreground to try it:
./scripts/feedback-server.sh

# Keep it running permanently (macOS launchd, KeepAlive). The installer
# creates a native app identity, requests protected-folder access when
# needed, and installs/starts the LaunchAgent. It requires Apple's Command
# Line Tools (`xcode-select --install` if `swiftc` is unavailable).
./scripts/install-feedback-server.sh

# Logs:
tail -f ~/Library/Logs/pi-pulse/feedback-server.{out,err}.log

# Restart (required after editing sources/feedback_server.py or .env):
launchctl kickstart -k gui/$UID/com.user.pi-pulse-feedback

# Stop:
launchctl bootout gui/$(id -u)/com.user.pi-pulse-feedback
```

Then open `http://<machine-tailnet-name>:8377/` from any device on
your tailnet (MagicDNS). Ratings you enter steer the next morning's
plan stage via the feedback digest; no manual ingest needed.

The native wrapper is required because macOS TCC does not reliably
attribute access to `Desktop`, `Documents`, or `Downloads` when a bare
bash/Python LaunchAgent starts after reboot. The wrapper gives the job a
stable app identity to which macOS can attach folder consent; it does
not require Full Disk Access. The installer reuses that identity on
subsequent runs. Use `--rebuild-app` only when changing the native
wrapper itself, because rebuilding may require granting folder access
again.

## Tuning

-   Edit `memory/interests.md` -- it feeds every run. Or run
    `./scripts/interview.sh` for a guided refresh.
-   Card mix: set `PI_PULSE_CARDS_TRACKED`, `PI_PULSE_CARDS_ADJACENT`,
    `PI_PULSE_CARDS_BRIDGE` in `.env`. Defaults: 5 / 2 / 1 = 8 cards.
-   Card shape and structure: `prompts/compose_expand.md` (word budget,
    paragraph structure, citation conventions).
-   Topic selection logic: `prompts/compose_plan.md` (how the planner
    picks topics, what counts as adjacent, drop rules).
-   `sources/collect_sesh.py --deny-projects "dotfiles,ci"` filters
    noisy projects out of the sesh snapshot.
-   Change models by setting `PI_PROVIDER` and `PI_MODEL` in `.env`.

## Troubleshooting

-   **Empty Anki section.** Anki desktop must be running with
    AnkiConnect. The pipeline writes a warning to `logs/stderr.log` and
    continues.
-   **Ollama not reachable.** `pulse.sh` falls back to `ollama serve` in
    the background, but on a cold boot the first `pi` call may time out.
    Re-run.
-   **Web search returns nothing.** Confirm `BRAVE_API_KEY` is present in
    `.env`, then inspect `logs/<RUN_ID>/egress.log` and `scout.err`.
-   **Security audit refuses delivery.** Inspect `logs/<RUN_ID>/egress.md`.
    `capabilities.jsonl` records only Pi provider/model and security flags
    from the exact invocations; it never records prompt text or credentials.
-   **Brief is generic.** The distill memo is probably vague. Inspect
    `.tmp/interests_today.md` before tuning the compose prompt -- if it
    says "model architectures" instead of "Sourdough cold-retard at 70% hydration",
    widen the window or add more detail to `memory/interests.md`.
-   **Feedback server exits with `EX_CONFIG (78)` after reboot.** A bare
    interpreter LaunchAgent is probably being denied access to a checkout
    under a TCC-protected folder. Run `./scripts/install-feedback-server.sh`
    and approve the folder-access prompt; do not grant bash or Python Full
    Disk Access.

## License

MIT.
