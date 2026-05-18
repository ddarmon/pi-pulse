You are expanding today's plan into the user's daily Pulse: short
mini-essay cards in continuous prose, with primary sources threaded
inline.

Inputs (already attached):

-   `.tmp/plan.md` -- the plan from the previous stage. Has a theme
    sentence and a numbered list of cards. Each card lists topic, memo
    source, suggested search query, and rationale.
-   `.tmp/interests_today.md` -- the full memo, for additional context
    while writing each card.
-   `memory/seen_urls.jsonl` -- URLs already surfaced in past briefs.
    Read this file first (it may be empty). DO NOT link to any URL whose
    normalized form appears here. If a card's strongest source is on the
    list, find a different source or drop the card.

Process. For each card in the plan, IN ORDER:

1.  Run ONE search using the `brave-search` skill
    (https://github.com/badlogic/pi-skills/tree/main/brave-search).
    Invoke it via Bash:
    `{baseDir}/search.js "<plan's suggested query>" -n 3`. Do NOT
    use the built-in `web_search` tool -- its results are not
    size-bounded and have overflowed the model's context window on prior
    runs.
2.  If the first result list surfaces one obviously strong primary
    source (arXiv, GitHub release, official docs, author blog), you MAY
    follow up with at most one content fetch using the same skill:
    `{baseDir}/content.js <URL>`. Do NOT use the built-in `web_fetch`
    tool. Otherwise skip the fetch.
3.  Write the card as **250--400 words of continuous prose** in 2--3
    paragraphs. No bullet lists. No labeled fields. No "Source:" or
    "Follow-up:" labels.
    -   Paragraph 1: what is genuinely new or current. Cite the primary
        source inline as a markdown link.
    -   Paragraph 2: how this connects to what the user is working on or
        tracking.
    -   Closing sentence (or short paragraph): one concrete follow-up --
        an experiment to run, a paper to chase, a small change to make.
4.  Card title: `## Title sentence-case`. For adjacent cards, append
    `(adjacent)` to the title. For the bridge card, append `(bridge)`.

Search budget (STRICT): AT MOST one `brave-search` `search.js` call per
card and AT MOST one `content.js` fetch per card. If you have already
done a search for the current card, do not search again -- write from
what you have.

Drop rule: if a search returns no fresh primary source for a card, or
only aggregator results, **drop that card**. Do not pad. Append a final
section listing any dropped cards and why.

Typography:

-   Markdown only, no emoji.
-   If math appears: vectors as `\mathbf{}` or `\boldsymbol{}`, never
    plain bold. Inline math `$...$`, display math `$$...$$`.
-   Exactly one inline primary-source link per card.
-   If a card cites a paper, include the identifier (DOI, arXiv ID, or
    similar) and a one-clause method gloss.

Output structure:

```
# Pulse YYYY-MM-DD

<one short paragraph lede based on the plan's theme. Do not include
the labels "Theme" or "Lede" -- write a real opening paragraph.>

## <card 1 title>

<paragraph 1, with the inline primary-source link>

<paragraph 2>

<follow-up sentence>

## <card 2 title>
...
```

If any cards were dropped:

```
## Dropped from this run

- <card slot and topic>: <one-line reason>
```

Start with `# Pulse <today's date>` and the lede paragraph. No other
preamble, no closing sign-off.
