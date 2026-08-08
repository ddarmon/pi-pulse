# Guarded web broker

This in-repo, dependency-free Node 20 broker is the only tool surface exposed
to the Pulse scout model. `scout.ts` registers `search(query)` and `fetch(url)`;
there is no shell or generic filesystem tool.

The guard reads `BRAVE_API_KEY` directly from the repository `.env`, validates
and DNS-pins every outbound hop, refuses local/non-public addresses and
nonstandard ports, accepts only HTTP(S) GET, disables compression, streams
under fixed byte limits, and appends attempts/results to the run's private
egress JSONL. Redirects are resolved and revalidated one at a time.

`package-lock.json` intentionally has no third-party packages. The continuous
audit records a SHA-256 over this directory together with the Git commit and Pi
version, so broker changes are visible in each run's provenance report.
