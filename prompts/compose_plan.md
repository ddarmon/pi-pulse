You are planning today's daily Pulse: select exactly the specified
number of topics from the user's interests memo, then hand the plan to a
second stage that will research each topic and write it as a mini-essay
card.

Inputs (already attached):

-   `.tmp/interests_today.md` -- today's memo with five sections: Active
    threads, Open questions, Persistent interests, Study reinforcement,
    Avoid.
-   `memory/seen_urls.jsonl` -- URLs already surfaced in past briefs
    (may be empty). Avoid selecting a topic whose only credible source
    is likely on this list.

Quotas (fixed for this run):

-   **Tracked cards: {{TRACKED}}** -- topics drawn from the "Active
    threads" or "Persistent interests" sections of the memo. These are
    updates on what the user is already following.
-   **Adjacent cards: {{ADJACENT}}** -- topics matching the user's
    durable profile but NOT named in any section of the memo. These are
    adjacent novelty: things they'd care about but aren't tracking yet.
-   **Bridge cards: {{BRIDGE}}** -- topics drawn from "Study
    reinforcement" that can plausibly be connected to a recent paper,
    post, or release. If the Study reinforcement section is empty or has
    nothing connectable, emit zero bridge cards and explain why in the
    rationale.

Output exactly this markdown shape. No preamble. No closing sign-off.
Use today's date.

```
# Plan YYYY-MM-DD

**Today's theme:** <one sentence naming the dominant thread(s) the
day's cards will hit. This becomes the brief's lede.>

## Card 1 (tracked)

- **Topic:** <specific and named -- "Sourdough cold-retard
  temperature for 70% hydration", not "bread baking">
- **Memo source:** <quote the bullet from interests_today.md that
  triggered this>
- **Search query:** <one short web query; NO site: filters; assume
  max_results=3 will be used>
- **Why this card:** <one sentence on why the user would care today>

(repeat for each tracked card, then each adjacent card, then each
bridge card -- number them sequentially 1, 2, 3, ...)

For an adjacent card, replace `(tracked)` with `(adjacent)` and the
`Memo source` line with:
- **Why this is adjacent (not in memo):** <name the durable interest
  this connects to>

For a bridge card, replace `(tracked)` with `(bridge)` and add a
final line:
- **Bridge hypothesis:** <one sentence on what current source might
  exist that connects the leech to active work>
```

Rules:

-   Topics must be SPECIFIC. Name the technique, release, or named
    entity --- "Late-blight resistance in heirloom tomato varietals" not
    "vegetable gardening." "Sourdough cold-retard at 70% hydration" not
    "bread baking."
-   Avoid any topic in the memo's "Avoid" section.
-   Diversify: do not select two cards on the same paper, library, or
    release. Spread across distinct memo bullets so the brief covers
    multiple threads.
-   For each tracked card, prefer bullets you can imagine a current
    primary source for (release notes, arXiv paper, GitHub release,
    author blog). If a thread is interesting but no plausible fresh
    source exists, do not select it.
-   The Search query is a starting point; the expand stage may follow up
    with a `brave-search` `content.js` fetch on a URL the first result
    reveals.
-   Do not use web search yourself -- you have no tools in this stage.
