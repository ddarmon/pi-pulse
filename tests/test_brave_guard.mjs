import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import test from "node:test";
import { deflateSync } from "node:zlib";

import {
  braveSearch,
  extractReadable,
  fetchPage,
  guardedGet,
  isForbiddenAddress,
  readEnvValue,
  readStreamBounded,
  resolvePublic,
  validateUrlSyntax,
} from "../sources/brave-guard/guard.js";

test("URL syntax policy accepts public HTTP(S) and rejects dangerous shapes", () => {
  assert.equal(validateUrlSyntax("https://example.com/paper?q=test").hostname, "example.com");
  for (const value of [
    "file:///etc/passwd",
    "https://user:pass@example.com/",
    "http://127.0.0.1:8765/",
    "http://localhost/",
    "https://example.com:8443/",
    `https://example.com/?q=${"x".repeat(513)}`,
    // A doubled scheme parses as the single-label host `https`. The Python
    // policy rejects the same shape; the two gates must not diverge, or a
    // URL that clears one and fails the other aborts the run's audit.
    "https://https://arxiv.org/html/2512.16959v1",
    "https://intranet/paper",
  ]) {
    assert.throws(() => validateUrlSyntax(value), { name: "GuardError" });
  }
});

test("private, loopback, link-local, and CGNAT addresses are forbidden", () => {
  for (const address of [
    "127.0.0.1",
    "10.1.2.3",
    "172.16.9.2",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.1.2",
    "::1",
    "::ffff:127.0.0.1",
    "::ffff:7f00:1",
    "fd7a:115c:a1e0::1",
    "fe80::1",
  ]) {
    assert.equal(isForbiddenAddress(address), true, address);
  }
  assert.equal(isForbiddenAddress("8.8.8.8"), false);
  assert.equal(isForbiddenAddress("2606:4700:4700::1111"), false);
});

test("DNS policy rejects an answer set containing a private address", async () => {
  const url = validateUrlSyntax("https://example.com/");
  await assert.rejects(
    resolvePublic(url, async () => [
      { address: "93.184.216.34", family: 4 },
      { address: "127.0.0.1", family: 4 },
    ]),
    /non-public address/,
  );
});

test("redirect targets are re-resolved and private answers are refused", async () => {
  let opens = 0;
  const lookup = async (hostname) => {
    if (hostname === "example.com") return [{ address: "93.184.216.34", family: 4 }];
    return [{ address: "127.0.0.1", family: 4 }];
  };
  const open = async () => {
    opens += 1;
    const response = Readable.from([]);
    response.statusCode = 302;
    response.headers = { location: "https://private.example/next" };
    return response;
  };
  await assert.rejects(
    guardedGet("https://example.com/start", { lookup, open }),
    /non-public address/,
  );
  assert.equal(opens, 1, "private redirect must be rejected before a second request");
});

test("response stream is capped while reading", async () => {
  const stream = Readable.from([Buffer.alloc(6), Buffer.alloc(6)]);
  await assert.rejects(readStreamBounded(stream, 10), /exceeds 10 bytes/);
});

test("an oversize page is truncated at the cap instead of lost", async () => {
  // Two live runs degraded to search snippets on ordinary articles that
  // merely exceeded the cap; a prefix still carries the prose.
  const stream = Readable.from([Buffer.alloc(6, 0x61), Buffer.alloc(6, 0x62)]);
  let truncated = false;
  const body = await readStreamBounded(stream, 10, undefined, {
    truncate: true,
    onTruncate: () => {
      truncated = true;
    },
  });
  assert.equal(body.length, 10, "the byte ceiling still binds exactly");
  assert.equal(body.toString(), "aaaaaabbbb");
  assert.equal(truncated, true);
});

test("fetchPage keeps the prose of a page that exceeds the byte cap", async () => {
  const prose = "A substantive public article sentence. ".repeat(12);
  const html = `<html><title>Fat</title><body><p>${prose}</p></body></html>`;
  const lookup = async () => [{ address: "93.184.216.34", family: 4 }];
  const open = async () => {
    const response = Readable.from([Buffer.from(html), Buffer.alloc(4 * 1024 * 1024)]);
    response.statusCode = 200;
    // Declared oversize too: a truncating read must not be pre-empted by
    // the content-length check.
    response.headers = { "content-type": "text/html", "content-length": "5000000" };
    return response;
  };
  const output = await fetchPage("https://example.com/fat", { lookup, open });
  assert.match(output, /substantive public article/);
});

test("HTML extraction drops scripts and retains substantive prose", () => {
  const prose = "A substantive public article sentence. ".repeat(12);
  const html = Buffer.from(`<html><title>Example</title><script>steal()</script><body><p>${prose}</p></body></html>`);
  const output = extractReadable(html, "text/html");
  assert.match(output, /^# Example/);
  assert.match(output, /substantive public article/);
  assert.doesNotMatch(output, /steal/);
});

// Builds a minimal one-page PDF whose content stream is deflate-compressed,
// the same shape the real academic sources use.
function buildPdf(operators, { filter = "/FlateDecode", compress = true } = {}) {
  const body = compress ? deflateSync(Buffer.from(operators, "latin1")) : Buffer.from(operators, "latin1");
  return Buffer.concat([
    Buffer.from(`%PDF-1.5\n1 0 obj\n<< /Length ${body.length} ${filter ? `/Filter ${filter}` : ""} >>\nstream\n`, "latin1"),
    body,
    Buffer.from("\nendstream\nendobj\n%%EOF\n", "latin1"),
  ]);
}

const PDF_PROSE = "Distributed rate limiting converges to a fair allocation under skewed flows. ".repeat(6);

test("PDF text is recovered from compressed content streams", () => {
  // TJ kerns, not spaces, separate the words -- as LaTeX emits them.
  const kerned = "BT /F1 11 Tf [(Distributed) -300 (rate) -300 (limiter)] TJ ET";
  const pdf = buildPdf(`BT /F1 11 Tf (${PDF_PROSE}) Tj ET ${kerned}`);
  const output = extractReadable(pdf, "application/pdf");
  assert.match(output, /Distributed rate limiting converges/);
  // The kern-to-space heuristic must not run words together.
  assert.match(output, /Distributed rate limiter/);
});

test("PDF ligatures are expanded from both escapes and raw bytes", () => {
  const filler = "and this sentence exists only to clear the substantive-text floor. ".repeat(4);
  const pdf = buildPdf(`BT /F1 11 Tf (Classi\\014cation of \x0dexible ffi models. ${filler}) Tj ET`);
  const output = extractReadable(pdf, "application/pdf");
  assert.match(output, /Classification/);
  assert.match(output, /flexible/);
  assert.doesNotMatch(output, /[\x0b-\x0f]/);
});

test("declared PDF without the %PDF- header is refused, not parsed", () => {
  const notPdf = Buffer.from("GIF89a" + "\u0000".repeat(400), "latin1");
  assert.throws(() => extractReadable(notPdf, "application/pdf"), /does not begin with %PDF-/);
});

test("image-only PDF is refused as having no text layer", () => {
  // A /Image stream is skipped, so nothing substantive is extracted.
  const pdf = buildPdf("\u0000\u0001\u0002".repeat(400), { filter: "/DCTDecode" });
  assert.throws(() => extractReadable(pdf, "application/pdf"), /no extractable text layer/);
});

test("binary streams that inflate do not leak noise into extracted text", () => {
  // High-byte payload containing PDF-string-literal shapes; the per-stream
  // text gate must discard it rather than mining "(...)" out of binary.
  let noise = "";
  for (let i = 0; i < 3000; i += 1) noise += String.fromCharCode(128 + (i % 127));
  const pdf = buildPdf(`BT /F1 11 Tf (${PDF_PROSE}) Tj ET`) ;
  const withNoise = Buffer.concat([
    pdf,
    Buffer.from("1 0 obj\n<< >>\nstream\n", "latin1"),
    deflateSync(Buffer.from(`((${noise}) Tj`, "latin1")),
    Buffer.from("\nendstream\n", "latin1"),
  ]);
  const output = extractReadable(withNoise, "application/pdf");
  assert.match(output, /Distributed rate limiting converges/);
  // No high-byte garbage survived into model context.
  assert.ok(!/[\u0080-\u00ff]{8}/.test(output), "binary noise leaked into extracted text");
});

test("a PDF decompression bomb is capped instead of exhausting memory", () => {
  const bomb = buildPdf("A".repeat(40 * 1024 * 1024));
  // Must return control quickly and never OOM; no text operators, so refused.
  assert.throws(() => extractReadable(bomb, "application/pdf"), /no extractable text layer/);
});

test("search query length fails before any network request", async () => {
  await assert.rejects(braveSearch("x".repeat(257)), /exceeds 256 characters/);
});

test("broker reads its API key directly from a dotenv file", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "pulse-guard-"));
  try {
    const envPath = path.join(directory, ".env");
    fs.writeFileSync(envPath, 'BRAVE_API_KEY="brv-test-value"\n');
    assert.equal(readEnvValue("BRAVE_API_KEY", envPath), "brv-test-value");
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
