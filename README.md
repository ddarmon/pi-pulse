# pi-pulse

A personalized daily brief, built on [Pi](https://github.com/badlogic/pi-mono)
and Ollama Cloud-hosted models. Inspired by OpenAI's Pulse, but every
piece is local and replaceable.

Each morning the pipeline:

1. **Collects** the last 30 days of your exported LLM chats (any
   `YYYY/MM/DD/*.md` tree -- e.g. an Obsidian vault), the last 7 days
   of [`sesh`](https://github.com/ddarmon/sesh) coding sessions, and
   three Anki signals (recent reviews, recent additions, leeches).
2. **Distills** them into a memo of active threads, open questions,
   and persistent interests using a single Pi call with `--no-skills`.
3. **Composes** 5--8 markdown cards via a second Pi call with web
   search enabled: ~70% updates on tracked threads, ~30% adjacent
   novelty, plus exactly one card that links an Anki leech to a recent
   primary source.
4. **Delivers** the brief to `out/YYYY-MM-DD.md` and, if configured,
   to a separate delivery directory (e.g. your Obsidian vault).

## Layout

```
pulse.sh                    entrypoint (manual or launchd)
prompts/                    distill + compose templates
sources/                    notes / sesh / anki collectors + URL dedup
memory/interests.md         hand-maintained durable profile (gitignored)
memory/interests.md.example template to copy from
memory/seen_urls.jsonl      dedup ledger (auto-appended, gitignored)
out/                        local copy of each day's brief (gitignored)
logs/                       launchd stdout/stderr (gitignored)
launchd/                    com.user.pi-pulse.plist.template
.env.example                config template
```

## Prerequisites

- [`pi`](https://github.com/badlogic/pi-mono) installed on `$PATH`.
- A Pi provider + model. The defaults assume `kimi-k2.6:cloud` is
  configured under an `ollama` provider in `~/.pi/agent/models.json`,
  but anything Pi understands works. Set `PI_PROVIDER` / `PI_MODEL` to
  override.
- A web-search-capable tool available to Pi. The compose stage relies
  on it. The author uses `@ollama/pi-web-search`
  (`pi install npm:@ollama/pi-web-search`).
- [`sesh`](https://github.com/ddarmon/sesh) on `$PATH` for the coding
  session collector.
- [Anki](https://apps.ankiweb.net/) desktop with the
  [AnkiConnect](https://ankiweb.net/shared/info/2055492159) plugin
  running. The pipeline degrades gracefully if Anki is closed.
- The `anki_search.py` helper from the [anki-search skill](https://github.com/badlogic/pi-skills)
  -- or any compatible script that exposes the same `search <query>
  --json` interface.
- [`uv`](https://docs.astral.sh/uv/) for running the Python collectors
  (stdlib only, no extra deps).

## Configure

```bash
cp .env.example .env                                    # then edit paths
cp memory/interests.md.example memory/interests.md      # then edit profile
```

At a minimum, set `PI_PULSE_NOTES_DIR` in `.env` to the root of your
`YYYY/MM/DD/*.md` notes tree.

## Run manually

```bash
./pulse.sh
```

The brief lands at:

- `out/YYYY-MM-DD.md` (always)
- `$PI_PULSE_DELIVERY/YYYY-MM-DD.md` (if `PI_PULSE_DELIVERY` is set)

## Schedule daily at 05:30 (macOS launchd)

```bash
# Substitute the three placeholders ({{REPO}}, {{HOME}}, {{PATH}}) for
# your paths, then write the result to ~/Library/LaunchAgents/.
sed -e "s|{{REPO}}|$PWD|g" \
    -e "s|{{HOME}}|$HOME|g" \
    -e "s|{{PATH}}|$PATH|g" \
    launchd/com.user.pi-pulse.plist.template \
    > ~/Library/LaunchAgents/com.user.pi-pulse.plist

launchctl load ~/Library/LaunchAgents/com.user.pi-pulse.plist

# Trigger an immediate run without waiting:
launchctl kickstart -k gui/$UID/com.user.pi-pulse

# Tail logs:
tail -f logs/stdout.log logs/stderr.log

# Disable:
launchctl unload ~/Library/LaunchAgents/com.user.pi-pulse.plist
```

On Linux, write a systemd timer with `Persistent=true` against
`pulse.sh` instead; the script itself has no macOS-specific
dependencies beyond the launchd template.

## Tuning

- Edit `memory/interests.md` -- it feeds every run.
- `sources/collect_sesh.py --deny-projects "dotfiles,ci"` filters
  noisy projects out of the sesh snapshot.
- Adjust the 70/30 novelty mix and the leech-linkage rule in
  `prompts/compose_brief.md`.
- Change models by setting `PI_PROVIDER` and `PI_MODEL` in `.env`.

## Troubleshooting

- **Empty Anki section.** Anki desktop must be running with
  AnkiConnect. The pipeline writes a warning to `logs/stderr.log` and
  continues.
- **Ollama not reachable.** `pulse.sh` falls back to `ollama serve` in
  the background, but on a cold boot the first `pi` call may time out.
  Re-run.
- **Web search returns nothing.** Confirm the web search package is
  installed (`pi list` for `@ollama/pi-web-search`) and that any API
  key it needs is set.
- **Brief is generic.** The distill memo is probably vague. Inspect
  `.tmp/interests_today.md` before tuning the compose prompt -- if it
  says "model architectures" instead of "Sourdough cold-retard at 70% hydration",
  widen the window or add more detail to `memory/interests.md`.

## License

MIT.
