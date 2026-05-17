You are reading the user's recent activity to produce a memo that will
feed a research agent. The user's durable profile is in `interests.md`
(attached) -- treat it as authoritative for role, projects, and topics
of interest.

Inputs (already attached as files):

-   `chats_recent.md` -- recent LLM conversations exported from the
    user's notes (claude.ai, ChatGPT, Gemini, etc.). EXPLORATORY
    threads: what they've been reading, reasoning about, and asking
    questions on.
-   `sesh_recent.md` -- recent coding sessions via `sesh` (Claude Code,
    Codex, Cursor, Copilot, pi). ACTIVE threads: what they're actually
    debugging or building.
-   `anki_signals.md` -- three sections: recently reviewed cards,
    recently added cards, and leeches. STUDY signals.
-   `interests.md` -- hand-maintained durable profile.

Produce a memo with exactly these five sections, in this order:

1.  **Active threads.** Concrete and named: specific libraries, error
    messages, papers in flight, projects currently shipping.
2.  **Open questions.** Things asked but not resolved, problems hit but
    parked.
3.  **Persistent interests.** Topics that recur across multiple inputs
    or across weeks.
4.  **Study reinforcement.** Topics surfaced by Anki leeches or
    recently-added cards that connect to other work.
5.  **Avoid.** One-off tangents, settled topics, areas explicitly marked
    "you were wrong" or rejected.

Rules:

-   Be specific. "Sourdough cold-retard temperature for 70% hydration"
    not "bread baking." Name the technique, release, person, or
    identifier; avoid field-level abstractions.
-   Cite the input that triggered each bullet, in parentheses:
    `(chats)`, `(sesh)`, `(anki)`, or `(interests)`. Multiple sources
    okay: `(chats, sesh)`.
-   This memo feeds a research agent. A vague memo produces a generic
    brief; a specific memo produces a useful brief.
-   Markdown only. No preamble or sign-off -- start with `# Memo:` and
    the five sections.
