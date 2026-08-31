// PDF text extraction for the fetch broker.
//
// Academic primary sources are frequently PDFs (lecture notes, preprints,
// working papers). Their text lives in FlateDecode-compressed content
// streams, so the bytes carry no readable prose until they are inflated --
// there is nothing a model could summarize from the raw response. Before
// this module the broker's content-type allowlist rejected application/pdf
// outright, so every PDF source degraded to a Brave snippet or dropped.
//
// Node's zlib is a builtin, so extraction adds no dependency. Scope is
// deliberately narrow: recover reading-order text from the text-showing
// operators of LaTeX-style PDFs, which is what the source population is.
// Non-goals: CID/Type0 fonts with custom encodings (garbled), math glyphs
// (garbled in any text extractor), and scanned image-only PDFs (no text at
// all -- the caller drops the slot, which is correct).
//
// This runs inside the security boundary on bytes fetched from arbitrary
// hosts, so it is written to be hostile-input safe:
//   * no regular expressions over stream bytes (an early regex version
//     catastrophically backtracked and hung on binary input); the scanner
//     is a single linear pass
//   * every inflate is capped with maxOutputLength, and a total budget
//     bounds the whole document, so a decompression bomb throws instead of
//     exhausting memory
//   * every per-stream failure is contained, so a damaged or truncated
//     document still yields the text of the streams that did inflate

import { inflateSync } from "node:zlib";

// A single content stream that inflates past this is not prose we want.
export const MAX_PDF_STREAM_BYTES = 8 * 1024 * 1024;
// Total inflated budget for one document; bounds decompression bombs.
export const MAX_PDF_INFLATED_BYTES = 24 * 1024 * 1024;
// How far back to look for the stream's dictionary when classifying it.
const DICT_LOOKBACK = 512;
// A TJ kern at least this large (1/1000 em) is a word gap, not letter
// kerning. LaTeX encodes inter-word space as positioning, not as U+0020,
// so without this every extracted line comes out as "runtogetherwords".
const WORD_GAP_KERN = 100;

const ESCAPES = { n: "\n", r: "\r", t: "\t", b: "\b", f: "\f" };

// TeX's OT1 text encoding puts the f-ligatures at codes 11-15. Left alone
// they decode to control characters and words arrive as "Classi<FF>cation",
// so a card would quote mangled technical vocabulary. Producers emit them
// either as octal escapes (mapped in decodeLiteral) or as raw bytes (mapped
// in expandRawLigatures).
const OT1_LIGATURES = { 11: "ff", 12: "fi", 13: "fl", 14: "ffi", 15: "ffl" };

// Producers also emit the same ligatures as raw bytes rather than escapes.
// 0x0b/0x0c/0x0e/0x0f are never line breaks, so they map unconditionally;
// 0x0d is ambiguous with a carriage return, so it becomes "fl" only when a
// letter follows ("\rexible" -> "flexible"). Math fonts also place glyphs at
// this code, but those are followed by a break rather than a letter, and are
// unrecoverable without the font encoding -- they drop out in normalization.
function expandRawLigatures(value) {
  return value
    .replace(/[\x0b\x0c\x0e\x0f]/g, (ch) => OT1_LIGATURES[ch.charCodeAt(0)])
    .replace(/\x0d(?=[A-Za-z])/g, "fl");
}

// A stream that inflated but is an image, an embedded font, or any other
// binary payload can still contain byte sequences that look like PDF string
// literals, and mining those yields high-byte noise. Require the text this
// stream produced to read as mostly printable ASCII before keeping it;
// prose and math both pass, binary does not.
function looksLikeText(value) {
  if (value.length === 0) return false;
  let printable = 0;
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if ((code >= 0x20 && code <= 0x7e) || code === 0x09 || code === 0x0a) printable += 1;
  }
  return printable / value.length >= 0.85;
}

export function looksLikePdf(body) {
  if (!body || body.length < 5) return false;
  // Some producers emit leading whitespace or a BOM before the header.
  return body.subarray(0, 1024).includes("%PDF-");
}

// Image, font, and already-lossy-compressed streams hold no text operators.
// Skipping them keeps the inflate budget for prose and avoids spending time
// decompressing megabytes of figures.
function isNonTextStream(dict) {
  return /\/(?:Image|FontFile\d?|DCTDecode|JPXDecode|CCITTFaxDecode|JBIG2Decode|RunLengthDecode)\b/.test(dict);
}

// Raw (unfiltered) content streams are legal; accept them when the bytes
// already look like a text-showing operator stream.
function looksLikeOperators(slice) {
  const head = slice.subarray(0, 2048).toString("latin1");
  return /\b(?:BT|Tj|TJ|Td|Tm|Tf)\b/.test(head);
}

function contentStreams(body) {
  const streams = [];
  let inflated = 0;
  let index = 0;
  while ((index = body.indexOf("stream", index)) !== -1) {
    const dictStart = Math.max(0, index - DICT_LOOKBACK);
    const dict = body.subarray(dictStart, index).toString("latin1");
    let start = index + "stream".length;
    if (body[start] === 0x0d) start += 1;
    if (body[start] === 0x0a) start += 1;
    const end = body.indexOf("endstream", start);
    if (end === -1) break;
    index = end + "endstream".length;
    if (isNonTextStream(dict)) continue;

    const slice = body.subarray(start, end);
    let text = null;
    try {
      text = inflateSync(slice, { maxOutputLength: MAX_PDF_STREAM_BYTES });
    } catch {
      // Not deflate, damaged, truncated by the byte cap, or over the
      // per-stream ceiling. Fall back to raw operators when plausible.
      if (looksLikeOperators(slice)) text = slice;
    }
    if (!text) continue;
    inflated += text.length;
    streams.push(text);
    if (inflated > MAX_PDF_INFLATED_BYTES) break;
  }
  return streams;
}

function decodeLiteral(source, from) {
  let out = "";
  let depth = 1;
  let i = from;
  for (; i < source.length && depth > 0; i += 1) {
    const ch = source[i];
    if (ch === "\\") {
      const next = source[i + 1];
      if (next === undefined) break;
      i += 1;
      if (next >= "0" && next <= "7") {
        let octal = next;
        while (octal.length < 3 && source[i + 1] >= "0" && source[i + 1] <= "7") {
          i += 1;
          octal += source[i];
        }
        const code = parseInt(octal, 8);
        out += OT1_LIGATURES[code] ?? String.fromCharCode(code);
      } else if (next !== "\n") {
        out += ESCAPES[next] ?? next;
      }
    } else if (ch === "(") {
      depth += 1;
      out += ch;
    } else if (ch === ")") {
      depth -= 1;
      if (depth > 0) out += ch;
    } else {
      out += ch;
    }
  }
  return { text: out, next: i - 1 };
}

// One linear pass over the operator stream. Literal strings accumulate in
// `pending`; the operator that terminates them decides what to emit.
function textFromStream(stream) {
  const source = stream.toString("latin1");
  let out = "";
  let pending = [];
  let operator = "";
  let number = "";

  const flushNumber = () => {
    // A numeric token only matters between the strings of a TJ array.
    if (number && pending.length > 0) {
      const value = Number.parseFloat(number);
      if (Number.isFinite(value) && Math.abs(value) >= WORD_GAP_KERN) pending.push(" ");
    }
    number = "";
  };
  const flushOperator = () => {
    if (operator === "Tj" || operator === "'" || operator === '"') {
      out += pending.join("");
    } else if (operator === "TJ") {
      out += `${pending.join("")} `;
    } else if (operator === "T*" || operator === "Td" || operator === "TD" || operator === "ET") {
      out += "\n";
    }
    if (operator) pending = [];
    operator = "";
  };

  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === "(") {
      flushNumber();
      const literal = decodeLiteral(source, i + 1);
      pending.push(literal.text);
      i = literal.next;
      operator = "";
    } else if ((ch >= "0" && ch <= "9") || ch === "." || ch === "-") {
      number += ch;
    } else if ((ch >= "A" && ch <= "Z") || (ch >= "a" && ch <= "z") || ch === "*" || ch === "'" || ch === '"') {
      flushNumber();
      operator += ch;
    } else {
      flushNumber();
      flushOperator();
    }
  }
  flushOperator();
  return out;
}

// Returns extracted text, or "" when the document carries no text layer
// (a scanned PDF). The caller decides whether that is a drop.
export function extractPdfText(body) {
  const text = contentStreams(body)
    .map(textFromStream)
    .filter(looksLikeText)
    .map(expandRawLigatures)
    .join("\n")
    .replace(/[ \r]/g, "")
    .replace(/[ \t]+/g, " ")
    .replace(/[ \t]*\n[ \t]*/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return text;
}
