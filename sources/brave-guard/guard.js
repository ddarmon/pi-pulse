import dns from "node:dns/promises";
import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { extractPdfText, looksLikePdf } from "./pdf.js";

export const MAX_QUERY_CHARS = 256;
export const MAX_URL_QUERY_CHARS = 512;
export const MAX_URL_CHARS = 2048;
export const MAX_SEARCH_BYTES = 512 * 1024;
export const MAX_PAGE_BYTES = 2 * 1024 * 1024;
export const MAX_PAGE_CHARS = 100_000;
export const MAX_REDIRECTS = 5;
export const REQUEST_TIMEOUT_MS = 15_000;

const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_REPO_ROOT = path.resolve(MODULE_DIR, "../..");
let attemptCounter = 0;

export class GuardError extends Error {
  constructor(message, code = "guard-error") {
    super(message);
    this.name = "GuardError";
    this.code = code;
  }
}

export function repoRoot() {
  return path.resolve(process.env.REPO_ROOT || DEFAULT_REPO_ROOT);
}

function unquoteEnvValue(raw) {
  const value = raw.trim();
  if (value.length >= 2 && value[0] === "'" && value.at(-1) === "'") {
    return value.slice(1, -1);
  }
  if (value.length >= 2 && value[0] === '"' && value.at(-1) === '"') {
    return value
      .slice(1, -1)
      .replace(/\\n/g, "\n")
      .replace(/\\r/g, "\r")
      .replace(/\\t/g, "\t")
      .replace(/\\"/g, '"')
      .replace(/\\\\/g, "\\");
  }
  return value.replace(/\s+#.*$/, "").trim();
}

/** Read one key from .env without evaluating the file as shell code. */
export function readEnvValue(name, envPath = null) {
  const filename = envPath || process.env.PI_PULSE_ENV_FILE || path.join(repoRoot(), ".env");
  let text;
  try {
    text = fs.readFileSync(filename, "utf8");
  } catch (error) {
    if (error && error.code === "ENOENT") return null;
    throw error;
  }
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (match && match[1] === name) return unquoteEnvValue(match[2]);
  }
  return null;
}

function egressLogPath() {
  if (process.env.PI_PULSE_EGRESS_LOG) return path.resolve(process.env.PI_PULSE_EGRESS_LOG);
  const runId = process.env.RUN_ID;
  return runId ? path.join(repoRoot(), "logs", runId, "egress.log") : null;
}

export function logEgress(entry) {
  const filename = egressLogPath();
  if (!filename) return;
  fs.mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 });
  const record = {
    timestamp: new Date().toISOString(),
    stage: process.env.PI_PULSE_EGRESS_STAGE || "scout",
    slot: process.env.PI_PULSE_EGRESS_SLOT || null,
    ...entry,
  };
  fs.appendFileSync(filename, `${JSON.stringify(record)}\n`, { encoding: "utf8", mode: 0o600 });
}

function ipv4Number(address) {
  const parts = address.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return null;
  }
  return (((parts[0] << 24) >>> 0) + (parts[1] << 16) + (parts[2] << 8) + parts[3]) >>> 0;
}

function inV4Range(value, base, prefix) {
  const baseValue = ipv4Number(base);
  if (value === null || baseValue === null) return false;
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  return (value & mask) === (baseValue & mask);
}

/** Conservative non-public-address policy used before every redirect hop. */
export function isForbiddenAddress(address) {
  const zoneFree = String(address).split("%")[0].toLowerCase();
  const family = net.isIP(zoneFree);
  if (family === 4) {
    const value = ipv4Number(zoneFree);
    return [
      ["0.0.0.0", 8],
      ["10.0.0.0", 8],
      ["100.64.0.0", 10],
      ["127.0.0.0", 8],
      ["169.254.0.0", 16],
      ["172.16.0.0", 12],
      ["192.0.0.0", 24],
      ["192.0.2.0", 24],
      ["192.168.0.0", 16],
      ["198.18.0.0", 15],
      ["198.51.100.0", 24],
      ["203.0.113.0", 24],
      ["224.0.0.0", 4],
      ["240.0.0.0", 4],
    ].some(([base, prefix]) => inV4Range(value, base, prefix));
  }
  if (family === 6) {
    if (zoneFree === "::" || zoneFree === "::1") return true;
    // Treat every IPv4-mapped IPv6 answer conservatively. Dotted forms can
    // be delegated to the IPv4 range checks, while hexadecimal forms such as
    // ::ffff:7f00:1 must not bypass them through an alternate spelling.
    const mapped = zoneFree.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
    if (mapped) return isForbiddenAddress(mapped[1]);
    if (zoneFree.startsWith("::ffff:")) return true;
    const first = Number.parseInt(zoneFree.split(":", 1)[0] || "0", 16);
    if ((first & 0xfe00) === 0xfc00) return true; // fc00::/7
    if ((first & 0xffc0) === 0xfe80) return true; // fe80::/10
    if ((first & 0xff00) === 0xff00) return true; // multicast
    return zoneFree.startsWith("2001:db8:"); // documentation range
  }
  return true;
}

function boundedInteger(value, fallback, minimum, maximum) {
  const number = Number(value);
  if (!Number.isSafeInteger(number)) return fallback;
  return Math.max(minimum, Math.min(number, maximum));
}

export function validateUrlSyntax(raw) {
  if (typeof raw !== "string" || !raw.trim()) {
    throw new GuardError("URL is required", "invalid-url");
  }
  const value = raw.trim();
  if (value.length > MAX_URL_CHARS || /[\u0000-\u0020\u007f]/.test(value)) {
    throw new GuardError("URL is too long or contains whitespace/control characters", "invalid-url");
  }
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new GuardError("URL is not parseable", "invalid-url");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new GuardError("only http(s) URLs are allowed", "bad-scheme");
  }
  if (url.username || url.password) {
    throw new GuardError("URLs with embedded credentials are refused", "credentials");
  }
  if (url.search.length > MAX_URL_QUERY_CHARS + 1) {
    throw new GuardError(`URL query exceeds ${MAX_URL_QUERY_CHARS} characters`, "query-too-long");
  }
  const hostname = url.hostname.replace(/^\[|\]$/g, "").replace(/\.$/, "").toLowerCase();
  if (!hostname || /[<>]/.test(hostname) || net.isIP(hostname)) {
    throw new GuardError("IP literals and malformed hostnames are refused", "ip-literal");
  }
  if (hostname === "localhost" || hostname.endsWith(".localhost") || hostname.endsWith(".local") || hostname.endsWith(".internal") || hostname.endsWith(".home.arpa")) {
    throw new GuardError("local hostnames are refused", "local-hostname");
  }
  if (url.port) {
    const expected = url.protocol === "https:" ? "443" : "80";
    if (url.port !== expected) throw new GuardError("non-standard ports are refused", "bad-port");
  }
  return url;
}

export async function resolvePublic(url, lookup = dns.lookup) {
  const hostname = url.hostname.replace(/^\[|\]$/g, "");
  let addresses;
  let timeout;
  try {
    addresses = await Promise.race([
      lookup(hostname, { all: true, verbatim: true }),
      new Promise((_resolve, reject) => {
        timeout = setTimeout(
          () => reject(new GuardError(`DNS lookup timed out for ${hostname}`, "dns-timeout")),
          REQUEST_TIMEOUT_MS,
        );
      }),
    ]);
  } catch (error) {
    if (error instanceof GuardError) throw error;
    throw new GuardError(`DNS lookup failed for ${hostname}: ${error.message}`, "dns-failed");
  } finally {
    if (timeout) clearTimeout(timeout);
  }
  if (!Array.isArray(addresses) || addresses.length === 0) {
    throw new GuardError(`DNS returned no addresses for ${hostname}`, "dns-empty");
  }
  if (addresses.some(({ address }) => isForbiddenAddress(address))) {
    throw new GuardError(`DNS for ${hostname} includes a non-public address`, "private-address");
  }
  return addresses;
}

export async function readStreamBounded(stream, maxBytes, signal) {
  const chunks = [];
  let total = 0;
  for await (const chunk of stream) {
    if (signal?.aborted) {
      stream.destroy();
      throw new GuardError("request aborted", "aborted");
    }
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += buffer.length;
    if (total > maxBytes) {
      stream.destroy();
      throw new GuardError(`response exceeds ${maxBytes} bytes`, "response-too-large");
    }
    chunks.push(buffer);
  }
  return Buffer.concat(chunks, total);
}

function pinnedLookup(addresses) {
  return (_hostname, options, callback) => {
    if (options && options.all) {
      callback(null, addresses.map(({ address, family }) => ({ address, family })));
      return;
    }
    callback(null, addresses[0].address, addresses[0].family);
  };
}

function openResponse(url, addresses, headers, signal) {
  return new Promise((resolve, reject) => {
    const transport = url.protocol === "https:" ? https : http;
    const request = transport.request(
      url,
      {
        method: "GET",
        headers: { ...headers, "Accept-Encoding": "identity" },
        lookup: pinnedLookup(addresses),
        signal,
      },
      resolve,
    );
    // `setTimeout` below is an inactivity timeout. Keep a separate absolute
    // deadline so a peer cannot hold a worker forever by dripping one byte
    // just before each inactivity window expires.
    const deadline = setTimeout(() => {
      request.destroy(new GuardError("request deadline exceeded", "timeout"));
    }, REQUEST_TIMEOUT_MS);
    request.once("close", () => clearTimeout(deadline));
    request.setTimeout(REQUEST_TIMEOUT_MS, () => {
      request.destroy(new GuardError("request timed out", "timeout"));
    });
    request.on("error", reject);
    request.end();
  });
}

/** GET a URL with DNS pinning, manual redirects, and a streaming byte cap. */
export async function guardedGet(rawUrl, options = {}) {
  const {
    maxBytes = MAX_PAGE_BYTES,
    headers = {},
    signal,
    kind = "fetch",
    query = null,
    lookup = dns.lookup,
    open = openResponse,
  } = options;
  let requested;
  try {
    requested = validateUrlSyntax(rawUrl);
  } catch (error) {
    logEgress({
      event: "rejected",
      kind,
      method: "GET",
      requested_url: String(rawUrl).slice(0, MAX_URL_CHARS),
      query,
      query_length: typeof query === "string" ? query.length : null,
      error: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }
  let current = requested;

  for (let hop = 0; hop <= MAX_REDIRECTS; hop += 1) {
    try {
      current = validateUrlSyntax(current.href);
    } catch (error) {
      logEgress({
        event: "rejected",
        kind,
        method: "GET",
        requested_url: requested.href,
        url: current.href,
        redirect_hop: hop,
        query,
        query_length: typeof query === "string" ? query.length : null,
        error: error instanceof Error ? error.message : String(error),
      });
      throw error;
    }
    const attemptId = `${process.pid}-${Date.now()}-${attemptCounter++}`;
    const baseLog = {
      attempt_id: attemptId,
      kind,
      method: "GET",
      requested_url: requested.href,
      url: current.href,
      host: current.hostname,
      redirect_hop: hop,
      query,
      query_length: typeof query === "string" ? query.length : null,
    };
    logEgress({ event: "attempt", ...baseLog });

    let response;
    try {
      const addresses = await resolvePublic(current, lookup);
      response = await open(current, addresses, headers, signal);
      const status = response.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status)) {
        const location = response.headers.location;
        response.destroy();
        if (!location) throw new GuardError(`redirect ${status} had no Location`, "bad-redirect");
        logEgress({ event: "result", ...baseLog, status, outcome: "redirect", location });
        if (hop === MAX_REDIRECTS) throw new GuardError("too many redirects", "too-many-redirects");
        current = new URL(location, current);
        continue;
      }
      if (status < 200 || status >= 300) {
        response.destroy();
        throw new GuardError(`HTTP ${status}`, "http-error");
      }
      const encoding = String(response.headers["content-encoding"] || "identity").toLowerCase();
      if (encoding && encoding !== "identity") {
        response.destroy();
        throw new GuardError(`unexpected content encoding: ${encoding}`, "content-encoding");
      }
      const declared = Number(response.headers["content-length"] || 0);
      if (Number.isFinite(declared) && declared > maxBytes) {
        response.destroy();
        throw new GuardError(`declared response exceeds ${maxBytes} bytes`, "response-too-large");
      }
      const body = await readStreamBounded(response, maxBytes, signal);
      logEgress({ event: "result", ...baseLog, status, bytes: body.length, outcome: "ok" });
      return { body, headers: response.headers, status, url: current.href };
    } catch (error) {
      logEgress({
        event: "result",
        ...baseLog,
        status: response?.statusCode || null,
        outcome: "error",
        error: error instanceof Error ? error.message : String(error),
      });
      throw error;
    }
  }
  throw new GuardError("too many redirects", "too-many-redirects");
}

function decodeEntities(text) {
  const named = {
    amp: "&", apos: "'", gt: ">", lt: "<", nbsp: " ", quot: '"',
    ndash: "–", mdash: "—", hellip: "…", rsquo: "’", lsquo: "‘",
    rdquo: "”", ldquo: "“", copy: "©", reg: "®", trade: "™",
  };
  return text.replace(/&(#(?:x[0-9a-f]+|\d+)|[a-z][a-z0-9]+);/gi, (whole, entity) => {
    if (entity[0] === "#") {
      const hex = entity[1]?.toLowerCase() === "x";
      const value = Number.parseInt(entity.slice(hex ? 2 : 1), hex ? 16 : 10);
      if (Number.isFinite(value) && value > 0 && value <= 0x10ffff) return String.fromCodePoint(value);
      return whole;
    }
    return named[entity.toLowerCase()] ?? whole;
  });
}

export function extractReadable(body, contentType = "text/html") {
  // PDFs must be handled before any text decoding: the prose lives in
  // compressed content streams, so the response bytes carry none of it.
  if (/pdf/i.test(contentType)) {
    // The content-type allowlist admits declared PDFs only; require the
    // magic bytes too, so a mislabeled binary is refused rather than parsed.
    if (!looksLikePdf(body)) {
      throw new GuardError("declared PDF does not begin with %PDF-", "content-type");
    }
    const extracted = extractPdfText(body);
    const substantivePdf = extracted.replace(/[^\p{L}\p{N}]+/gu, "");
    if (substantivePdf.length < 200) {
      // Scanned/image-only PDFs land here: no text layer to summarize.
      throw new GuardError("PDF has no extractable text layer", "empty-content");
    }
    return extracted.slice(0, MAX_PAGE_CHARS);
  }

  const decoded = new TextDecoder("utf-8", { fatal: false }).decode(body);
  if (!/html|xml/i.test(contentType)) {
    const plain = decoded.replace(/\u0000/g, "").trim();
    if (plain.length < 200) throw new GuardError("response has no substantive text", "empty-content");
    return plain.slice(0, MAX_PAGE_CHARS);
  }

  const titleMatch = decoded.match(/<title\b[^>]*>([\s\S]*?)<\/title\s*>/i);
  const title = titleMatch ? decodeEntities(titleMatch[1].replace(/<[^>]*>/g, " ")).replace(/\s+/g, " ").trim() : "";
  let text = decoded
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/<(script|style|noscript|svg|iframe|object|embed|nav|header|footer|aside)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, " ")
    .replace(/<br\b[^>]*\/?\s*>/gi, "\n")
    .replace(/<li\b[^>]*>/gi, "\n- ")
    .replace(/<h([1-6])\b[^>]*>/gi, (_m, level) => `\n${"#".repeat(Number(level))} `)
    .replace(/<\/(?:h[1-6]|p|div|section|article|main|li|ul|ol|tr|table|blockquote|pre)\s*>/gi, "\n")
    .replace(/<[^>]+>/g, " ");
  text = decodeEntities(text)
    .replace(/\r/g, "")
    .replace(/[ \t]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  const substantive = text.replace(/[^\p{L}\p{N}]+/gu, "");
  const stub = /^(?:loading|enable javascript|just a moment|please wait)[.!…\s]*$/i.test(text);
  if (substantive.length < 200 || stub) {
    throw new GuardError("response has no substantive readable text", "empty-content");
  }
  const heading = title ? `# ${title}\n\n` : "";
  return `${heading}${text}`.slice(0, MAX_PAGE_CHARS);
}

export async function fetchPage(rawUrl, options = {}) {
  const result = await guardedGet(rawUrl, {
    ...options,
    maxBytes: options.maxBytes || MAX_PAGE_BYTES,
    kind: options.kind || "fetch",
    headers: {
      "User-Agent": "pi-pulse-fetch/1.0",
      Accept: "text/html,application/xhtml+xml,text/plain,text/markdown,application/pdf;q=0.9,application/xml;q=0.8,*/*;q=0.1",
      ...(options.headers || {}),
    },
  });
  const contentType = String(result.headers["content-type"] || "").split(";", 1)[0].trim().toLowerCase();
  // `pdf` admits application/pdf and application/x-pdf; extractReadable
  // additionally requires the %PDF- header before parsing anything.
  // Undeclared binaries (application/octet-stream) stay refused.
  if (contentType && !/(?:html|xml|json|text|markdown|pdf)/.test(contentType)) {
    throw new GuardError(`unsupported content type: ${contentType}`, "content-type");
  }
  const readable = extractReadable(result.body, contentType || "text/html");
  return `Fetched URL: ${result.url}\n\n${readable}\n`;
}

function validateQuery(query) {
  if (typeof query !== "string" || !query.trim()) {
    const printable = typeof query === "string" ? query.slice(0, MAX_QUERY_CHARS + 1) : String(query);
    logEgress({
      event: "rejected",
      kind: "search",
      query: printable,
      query_length: typeof query === "string" ? query.length : null,
      error: "invalid-query",
    });
    throw new GuardError("search query is required", "invalid-query");
  }
  const clean = query.trim().replace(/\s+/g, " ");
  if (clean.length > MAX_QUERY_CHARS) {
    logEgress({
      event: "rejected",
      kind: "search",
      query: clean.slice(0, MAX_QUERY_CHARS + 1),
      query_length: clean.length,
      error: "query-too-long",
    });
    throw new GuardError(`query exceeds ${MAX_QUERY_CHARS} characters`, "query-too-long");
  }
  if (/[\u0000-\u001f\u007f]/.test(clean)) {
    logEgress({
      event: "rejected",
      kind: "search",
      query: clean,
      query_length: clean.length,
      error: "invalid-query",
    });
    throw new GuardError("query contains control characters", "invalid-query");
  }
  return clean;
}

export async function braveSearch(rawQuery, options = {}) {
  const query = validateQuery(rawQuery);
  const count = boundedInteger(options.count ?? 5, 5, 1, 10);
  const country = /^[A-Za-z]{2}$/.test(options.country || "US") ? String(options.country || "US").toUpperCase() : "US";
  const freshness = options.freshness && /^(?:pd|pw|pm|py|\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2})$/.test(options.freshness)
    ? options.freshness
    : null;
  // The key is deliberately read by the broker, not inherited by the Pi
  // model process. pulse.sh invokes Pi and these CLIs with BRAVE_API_KEY unset.
  const apiKey = readEnvValue("BRAVE_API_KEY") || (options.allowProcessEnv ? process.env.BRAVE_API_KEY : null);
  if (!apiKey) {
    logEgress({ event: "rejected", kind: "search", query, query_length: query.length, error: "missing-api-key" });
    throw new GuardError("BRAVE_API_KEY is missing from .env", "missing-api-key");
  }

  const params = new URLSearchParams({ q: query, count: String(count), country });
  if (freshness) params.set("freshness", freshness);
  const endpoint = `https://api.search.brave.com/res/v1/web/search?${params}`;
  const result = await guardedGet(endpoint, {
    maxBytes: MAX_SEARCH_BYTES,
    headers: { Accept: "application/json", "X-Subscription-Token": apiKey, "User-Agent": "pi-pulse-search/1.0" },
    signal: options.signal,
    kind: "search",
    query,
    lookup: options.lookup || dns.lookup,
  });
  let payload;
  try {
    payload = JSON.parse(result.body.toString("utf8"));
  } catch {
    throw new GuardError("Brave returned invalid JSON", "invalid-search-response");
  }
  const results = Array.isArray(payload?.web?.results) ? payload.web.results.slice(0, count) : [];
  return results.map((item, index) => {
    const title = String(item?.title || "").replace(/\s+/g, " ").trim().slice(0, 500);
    const link = String(item?.url || "").trim().slice(0, MAX_URL_CHARS);
    const age = String(item?.age || item?.page_age || "").replace(/\s+/g, " ").trim().slice(0, 100);
    const snippet = String(item?.description || "").replace(/\s+/g, " ").trim().slice(0, 1500);
    const lines = [`--- Result ${index + 1} ---`, `Title: ${title}`, `Link: ${link}`];
    if (age) lines.push(`Age: ${age}`);
    lines.push(`Snippet: ${snippet}`);
    return lines.join("\n");
  }).join("\n\n") || "No results found.";
}
