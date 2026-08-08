import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import test from "node:test";

import {
  braveSearch,
  extractReadable,
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

test("HTML extraction drops scripts and retains substantive prose", () => {
  const prose = "A substantive public article sentence. ".repeat(12);
  const html = Buffer.from(`<html><title>Example</title><script>steal()</script><body><p>${prose}</p></body></html>`);
  const output = extractReadable(html, "text/html");
  assert.match(output, /^# Example/);
  assert.match(output, /substantive public article/);
  assert.doesNotMatch(output, /steal/);
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
