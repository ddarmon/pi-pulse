# Guarded web broker

This in-repo, dependency-free Node 20 broker is the only tool surface exposed
to the Pulse scout model. `scout.ts` registers `search(query)` and `fetch(url)`;
there is no shell or generic filesystem tool.

The guard reads `BRAVE_API_KEY` directly from the repository `.env`, validates
and DNS-pins every outbound hop, refuses local/non-public addresses and
nonstandard ports, accepts only HTTP(S) GET, disables compression, streams
under fixed byte limits, and appends attempts/results to the run's private
egress JSONL. Redirects are resolved and revalidated one at a time.

`pdf.js` extracts text from `application/pdf` responses using Node's builtin
`zlib`, because the prose in an academic PDF lives in compressed content
streams and the raw bytes carry none of it. The content-type allowlist admits
declared PDFs only, and extraction additionally requires the `%PDF-` header,
so a mislabeled binary is refused rather than parsed. Every inflate is capped
(`maxOutputLength` per stream plus a whole-document budget) so a decompression
bomb throws instead of exhausting memory, the scanner is a single linear pass
with no regexes over stream bytes, and streams whose extracted text is not
mostly printable ASCII are discarded so image and font payloads cannot leak
binary noise into model context. Scanned PDFs with no text layer are refused as
empty; recovering them would need OCR. CID/Type0 fonts with custom encodings
and math glyphs come out garbled, as they do in any text extractor.

`package-lock.json` intentionally has no third-party packages. The continuous
audit records a SHA-256 over this directory together with the Git commit and Pi
version, so broker changes are visible in each run's provenance report.
