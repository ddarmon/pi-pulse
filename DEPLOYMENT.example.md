# Deployment state

Copy to `DEPLOYMENT.local.md` (gitignored) and keep it current. This
records *which machine runs what* -- deployment state, not repo
knowledge, and usually not something you want in a public commit.
CLAUDE.md carries the transferable lessons; this file carries the
particulars.

## Hosts

| role | host | notes |
|---|---|---|
| scheduled pulse | `<hostname>` | always-on; runs `com.user.pi-pulse` at `<HH:MM>` |
| feedback server | `<hostname>` | must be the same host as the pulse |
| session sources | `<hostname>`, `<hostname>` | machines whose sessions feed distill |

## Endpoints

- Rating UI: `http://<host>:8377/`
- Ollama: `<PI_PULSE_OLLAMA_BASE_URL>`
- Shared session tree: `<PI_PULSE_SESH_ARCHIVE_ROOT>`

## Retired

Record hosts that used to run a job and no longer do, and how they were
stopped -- `launchctl bootout` alone is undone at next login, so a
retired job needs `bootout` **and** `disable` to stay off. Note whether
the plist was kept (re-enabling is then one installer run).

## Ledger ownership

Only the host that runs the pipeline appends to `memory/seen_urls.jsonl`
and `memory/feedback.jsonl`. After a cutover the old host's copies are
frozen and will drift. Rate on the host that generates the briefs.
