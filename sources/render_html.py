#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["markdown>=3.5"]
# ///
"""Render a Pulse markdown brief to a self-contained HTML file.

Prefers `pandoc -s --mathjax` if pandoc is on PATH (richer markdown
support, better math handling); falls back to the Python `markdown`
package with sensible extensions. In both cases, the output is wrapped
in our own HTML template with embedded CSS so the result is one file
that renders acceptably on iPhone and desktop without further styling.

MathJax is loaded from a CDN only if `$...$` or `$$...$$` is detected
in the source.

Usage:
    render_html.py <input.md> <output.html>
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Inline math like $x$ or display math $$ ... $$. The first alternative
# refuses to match across blank lines and avoids capturing a stray `$`
# in prose (e.g. a price). The second is greedy across lines.
MATH_RE = re.compile(r"\$[^\s$][^$\n]{0,400}\$|\$\$[\s\S]+?\$\$")


def has_math(md_text: str) -> bool:
    return bool(MATH_RE.search(md_text))


def render_with_pandoc(md_text: str) -> str | None:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        return None
    try:
        result = subprocess.run(
            [pandoc, "--from", "markdown", "--to", "html5", "--mathjax"],
            input=md_text,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"pandoc failed, falling back: {exc}", file=sys.stderr)
        return None
    return result.stdout


def render_with_markdown(md_text: str) -> str:
    import markdown

    return markdown.markdown(
        md_text,
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html5",
    )


# Single embedded stylesheet. Mobile-first, system fonts, dark-mode aware,
# capped line length for readability. No external assets.
CSS = """
:root {
  --fg: #1a1a1a;
  --bg: #fafaf7;
  --muted: #555;
  --link: #0a58ca;
  --link-hover: #084298;
  --rule: #d8d4cc;
  --code-bg: #eee8db;
  --quote: #6a6a6a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg: #e7e3da;
    --bg: #1c1b18;
    --muted: #a8a39a;
    --link: #7fb3ff;
    --link-hover: #b8d4ff;
    --rule: #3a3733;
    --code-bg: #2a2723;
    --quote: #9a958c;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  font: 17px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif;
  color: var(--fg);
  background: var(--bg);
  padding: 1.5rem 1rem 4rem;
}
main {
  max-width: 38rem;
  margin: 0 auto;
}
h1, h2, h3 {
  line-height: 1.25;
  margin: 2.2rem 0 0.6rem;
  font-weight: 650;
}
h1 { font-size: 1.7rem; margin-top: 0; }
h2 { font-size: 1.25rem; padding-top: 1rem; border-top: 1px solid var(--rule); }
h2:first-of-type { border-top: none; }
h3 { font-size: 1.05rem; color: var(--muted); }
p { margin: 0.8rem 0; }
a { color: var(--link); text-decoration: underline; text-underline-offset: 2px; }
a:hover { color: var(--link-hover); }
code {
  font: 0.92em/1.4 ui-monospace, SFMono-Regular, "SF Mono", Menlo,
    Consolas, monospace;
  background: var(--code-bg);
  padding: 0.1em 0.35em;
  border-radius: 3px;
}
pre {
  background: var(--code-bg);
  padding: 0.8rem 1rem;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 0.92em;
  line-height: 1.45;
}
pre code { background: none; padding: 0; }
blockquote {
  margin: 1rem 0;
  padding: 0.2rem 1rem;
  border-left: 3px solid var(--rule);
  color: var(--quote);
}
ul, ol { padding-left: 1.4rem; }
li { margin: 0.2rem 0; }
hr { border: 0; border-top: 1px solid var(--rule); margin: 2rem 0; }
table {
  border-collapse: collapse;
  margin: 1rem 0;
  font-size: 0.95em;
}
th, td {
  border: 1px solid var(--rule);
  padding: 0.4rem 0.7rem;
  text-align: left;
}
@media (min-width: 48rem) {
  body { padding: 3rem 2rem; font-size: 18px; }
}
""".strip()


MATHJAX = """
<script>
window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']]
  },
  options: { renderActions: { addMenu: [] } }
};
</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
""".strip()


def wrap(body_html: str, title: str, with_mathjax: bool) -> str:
    mathjax_block = MATHJAX if with_mathjax else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
{CSS}
</style>
{mathjax_block}
</head>
<body>
<main>
{body_html}
</main>
</body>
</html>
"""


def extract_title(md_text: str, fallback: str) -> str:
    for line in md_text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()

    if not args.input.is_file():
        print(f"ERROR: not a file: {args.input}", file=sys.stderr)
        return 2

    md_text = args.input.read_text(encoding="utf-8")
    title = extract_title(md_text, args.input.stem)

    body = render_with_pandoc(md_text)
    if body is None:
        try:
            body = render_with_markdown(md_text)
        except ImportError as exc:
            print(
                f"ERROR: pandoc not available and `markdown` package not "
                f"installed ({exc}). Run via `uv run` or `pip install markdown`.",
                file=sys.stderr,
            )
            return 1

    html_doc = wrap(body, title=title, with_mathjax=has_math(md_text))
    args.output.write_text(html_doc, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
