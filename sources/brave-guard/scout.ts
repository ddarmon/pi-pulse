import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { braveSearch, fetchPage } from "./guard.js";

function boundedInteger(value: string | undefined, fallback: number, maximum: number): number {
  const number = Number(value);
  if (!Number.isSafeInteger(number)) return fallback;
  return Math.max(1, Math.min(number, maximum));
}

export default function (pi: ExtensionAPI) {
  // Invalid environment values must fall back to finite budgets rather than
  // becoming NaN (which would make every `calls > budget` check false).
  const maxInterests = boundedInteger(process.env.SCOUT_MAX_INTERESTS, 12, 50);
  const perInterest = boundedInteger(process.env.SCOUT_QUERIES_PER_INTEREST, 2, 5);
  const maxSearchCalls = maxInterests * perInterest;
  let searchCalls = 0;
  let fetchCalls = 0;

  pi.registerTool({
    name: "search",
    label: "Guarded search",
    description: "Search the public web through a bounded, logged Brave Search broker.",
    promptSnippet: "Search the public web with a focused query",
    promptGuidelines: ["Use search only for focused public-topic queries; query text is length-capped and audited."],
    parameters: Type.Object({
      query: Type.String({ description: "Focused public web-search query (maximum 256 characters)" }),
      count: Type.Optional(Type.Integer({ minimum: 1, maximum: 10, description: "Result count (default 5)" })),
      freshness: Type.Optional(Type.String({ description: "Optional pd, pw, pm, py, or ISO-date range" })),
    }),
    async execute(_toolCallId, params, signal) {
      searchCalls += 1;
      if (searchCalls > maxSearchCalls) throw new Error(`search-call budget exceeded (${maxSearchCalls})`);
      const output = await braveSearch(params.query, {
        count: params.count,
        freshness: params.freshness,
        signal,
      });
      return { content: [{ type: "text", text: output }], details: { searchCalls, maxSearchCalls } };
    },
  });

  pi.registerTool({
    name: "fetch",
    label: "Guarded fetch",
    description: "Fetch readable text from one public http(s) URL with DNS, redirect, timeout, and byte-limit enforcement.",
    promptSnippet: "Fetch bounded readable text from a public URL",
    promptGuidelines: ["Use fetch only when a search snippet is insufficient; private/local addresses and non-standard ports are blocked."],
    parameters: Type.Object({
      url: Type.String({ description: "Public http(s) URL returned by search" }),
    }),
    async execute(_toolCallId, params, signal) {
      fetchCalls += 1;
      if (fetchCalls > maxInterests) throw new Error(`fetch-call budget exceeded (${maxInterests})`);
      const output = await fetchPage(params.url, { signal });
      return { content: [{ type: "text", text: output }], details: { fetchCalls, maxFetchCalls: maxInterests } };
    },
  });
}
