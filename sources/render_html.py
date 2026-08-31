#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["markdown>=3.5"]
# ///
"""Render a Pulse markdown brief to sanitized HTML plus local assets.

Prefers `pandoc -s --mathjax` if pandoc is on PATH (richer markdown
support, better math handling); falls back to the Python `markdown`
package with sensible extensions. In both cases, the output is sanitized
and wrapped in our own HTML template with embedded CSS. Math briefs also
copy a pinned local MathJax tree beside the HTML under ``assets/``.

MathJax is loaded only if genuine `$...$` / `$$...$$` math is detected in
the source (currency dollars alone never trigger it); no CDN is contacted.

Currency-vs-math note: briefs freely mix currency ("Uber's $1,500/month")
and TeX ("$S_{\\text{token}} \\le X$") in one paragraph. MathJax is
configured with ONLY `\\(...\\)` / `\\[...\\]` delimiters, never bare
`$`, so its in-browser text scanner can never pair two currency dollars
into bogus italic math (the long-standing "200/weekAIspendingcap" bug).
Pandoc already emits `\\(...\\)` / `\\[...\\]` for real math and leaves
currency as literal `$` text; the markdown fallback path reproduces that
via `_protect_math`.

Usage:
    render_html.py <input.md> <output.html>
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
MATHJAX_VENDOR_DIR = REPO_ROOT / "vendor" / "mathjax"
MATHJAX_NONCE_TOKEN = "__PI_PULSE_NONCE__"

# Genuine inline math like `$x$` / `$X \le Y$`. The delimiter rules make
# currency and math separable in one pass so both render paths agree:
#   (?<!\\)     opening `$` is not an escaped `\$`
#   (?![\s$\d]) opening `$` is NOT followed by space, `$`, or a DIGIT.
#               Currency always leads with a digit ($611, $1,500, $0.05),
#               so it can never OPEN a span. This also stops a currency
#               `$` from swallowing a later math opener: "$800 ... $X" --
#               `$800` never opens, so `$X \approx 0.03$` is matched on
#               its own.
#   [^$\n]{,400}? shortest run up to the closing `$`, never crossing a
#               newline or another `$`
#   \$(?!\d)    the closing `$` is NOT followed by a digit, so two amounts
#               ("$50 then $611") never pair either.
# Trade-off: a genuine digit-leading span like `$0.05$` is treated as
# non-math here. That is rare and ambiguous with currency; pandoc, the
# primary path, still renders it via its own reader.
INLINE_MATH_RE = re.compile(r"(?<!\\)\$(?![\s$\d])[^$\n]{0,400}?\$(?!\d)")
# Display math `$$...$$` is unambiguous (currency is never written this
# way), so it is always treated as math.
DISPLAY_MATH_RE = re.compile(r"(?<!\\)\$\$[\s\S]+?\$\$")

# Alphanumeric placeholder for the markdown fallback path. Letters only,
# no underscores, so the markdown package leaves it untouched (it would
# otherwise strip the backslashes out of `\(`/`\[` -- see _protect_math).
_MATH_TOKEN = "zzmathjaxprotectedspan"


def has_math(md_text: str) -> bool:
    """True iff the source contains genuine math (never for currency).

    A brief with only currency dollars ("spend $611 ... $1,500/month")
    returns False, so MathJax is not injected at all.
    """
    return bool(DISPLAY_MATH_RE.search(md_text) or INLINE_MATH_RE.search(md_text))


def convert_math_delimiters(md_text: str) -> str:
    """Rewrite genuine `$...$`/`$$...$$` math as `\\(...\\)`/`\\[...\\]`,
    leaving currency dollars as literal `$`.

    This is the pure transform behind the markdown fallback; the live
    fallback path uses `_protect_math` instead because the markdown
    package would eat the backslash delimiters (`\\(x\\)` -> `(x)`).
    """
    text = DISPLAY_MATH_RE.sub(lambda m: r"\[" + m.group(0)[2:-2] + r"\]", md_text)
    return INLINE_MATH_RE.sub(lambda m: r"\(" + m.group(0)[1:-1] + r"\)", text)


def _protect_math(md_text: str) -> tuple[str, list[tuple[str, str]]]:
    """Replace genuine math spans with inert alphanumeric placeholders,
    returning the rewritten text and an ordered (token, tex) list.

    Used only on the markdown fallback path: the markdown package strips
    the backslashes out of `\\(`/`\\[`, so math must be pulled out before
    rendering and stitched back in afterwards as `\\(...\\)`/`\\[...\\]`.
    """
    spans: list[tuple[str, str]] = []

    def display_repl(m: re.Match[str]) -> str:
        tok = f"{_MATH_TOKEN}{len(spans)}zz"
        spans.append((tok, r"\[" + m.group(0)[2:-2] + r"\]"))
        return tok

    text = DISPLAY_MATH_RE.sub(display_repl, md_text)

    def inline_repl(m: re.Match[str]) -> str:
        tok = f"{_MATH_TOKEN}{len(spans)}zz"
        spans.append((tok, r"\(" + m.group(0)[1:-1] + r"\)"))
        return tok

    text = INLINE_MATH_RE.sub(inline_repl, text)
    return text, spans


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


def _restore_math(body: str, spans: list[tuple[str, str]]) -> str:
    # The TeX is spliced back as escaped text, never as markup: raw `<` in
    # math (`\frac{a<b}{2}`) must not open a tag, and math is exactly where
    # a model-authored `$<a href=...>$` would otherwise slip past the
    # sanitizer. MathJax reads delimiters from textContent, so escaping is
    # lossless in the browser.
    for token, tex in spans:
        body = body.replace(token, html.escape(tex, quote=False))
    return body


def render_with_markdown(md_text: str) -> str:
    import markdown

    # Pull math out before rendering: the markdown package strips the
    # backslashes from `\(`/`\[`, so we substitute inert placeholders,
    # render, then splice the `\(...\)`/`\[...\]` spans back in for
    # MathJax to pick up in the browser.
    protected, spans = _protect_math(md_text)
    body = markdown.markdown(
        protected,
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html5",
    )
    return _restore_math(body, spans)


# Render first, then sanitize the resulting HTML tree. This catches both raw
# HTML supplied in markdown and resource-bearing tags generated by ordinary
# markdown syntax (for example `![x](https://attacker/pixel)`).
ALLOWED_TAGS = {
    "p",
    "br",
    "hr",
    "em",
    "strong",
    "code",
    "pre",
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "ul",
    "ol",
    "li",
    "sup",
    "sub",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "a",
}
ALLOWED_ATTRS = {"a": {"href"}}
DROP_WITH_CONTENT = {"script", "style", "svg", "iframe", "object", "embed"}
VOID_TAGS = {"br", "hr"}


class HtmlAllowlistSanitizer(HTMLParser):
    """Small output-tree allowlist with no third-party dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.drop_depth = 0

    @staticmethod
    def _log(message: str) -> None:
        print(f"sanitizer: {message}", file=sys.stderr)

    def _safe_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        rendered: list[str] = []
        allowed = ALLOWED_ATTRS.get(tag, set())
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            if name not in allowed or raw_value is None:
                self._log(f"dropped attribute {name!r} from <{tag}>")
                continue
            value = raw_value.strip()
            if tag == "a" and name == "href":
                if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value):
                    self._log("dropped href containing whitespace/control characters")
                    continue
                parts = urlsplit(value)
                scheme = parts.scheme.lower()
                if scheme not in {"", "http", "https"}:
                    self._log(f"dropped unsafe href from <a> (scheme={scheme})")
                    continue
                if not scheme and parts.netloc:
                    # //host/path is an external navigation target, not a
                    # relative link.
                    self._log("dropped protocol-relative href")
                    continue
            rendered.append(f' {name}="{html.escape(value, quote=True)}"')
        return "".join(rendered)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.drop_depth:
            if tag in DROP_WITH_CONTENT:
                self.drop_depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            self._log(f"dropped <{tag}> and its contents")
            self.drop_depth = 1
            return
        if tag not in ALLOWED_TAGS:
            self._log(f"dropped unknown <{tag}> tag (kept contents)")
            return
        attrs_text = self._safe_attrs(tag, attrs)
        self.parts.append(f"<{tag}{attrs_text}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.drop_depth:
            return
        if tag in DROP_WITH_CONTENT:
            # Self-closing form has no contents; any following text is kept
            # (escaped), so don't claim otherwise in the log.
            self._log(f"dropped self-closing <{tag}>")
            return
        if tag not in ALLOWED_TAGS:
            self._log(f"dropped unknown <{tag}> tag (kept contents)")
            return
        attrs_text = self._safe_attrs(tag, attrs)
        self.parts.append(f"<{tag}{attrs_text}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.drop_depth:
            if tag in DROP_WITH_CONTENT:
                self.drop_depth -= 1
            return
        if tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.drop_depth:
            self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        # convert_charrefs=True normally routes entities through handle_data;
        # retain a safe fallback for parser edge cases.
        if not self.drop_depth:
            self.parts.append(html.escape(html.unescape(f"&{name};"), quote=False))

    def handle_charref(self, name: str) -> None:
        if not self.drop_depth:
            self.parts.append(html.escape(html.unescape(f"&#{name};"), quote=False))


def sanitize_html(body_html: str) -> str:
    sanitizer = HtmlAllowlistSanitizer()
    sanitizer.feed(body_html)
    sanitizer.close()
    return "".join(sanitizer.parts)


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


MATHJAX = r"""
<script nonce="__PI_PULSE_NONCE__">
window.MathJax = {
  tex: {
    // ONLY \(...\)/\[...\] -- never bare `$` -- so MathJax's in-browser
    // scanner cannot pair currency dollars ("$200 ... $1,500") into
    // bogus inline math. Both render paths emit these delimiters.
    inlineMath: [['\\(', '\\)']],
    displayMath: [['\\[', '\\]']]
  },
  options: { renderActions: { addMenu: [] } },
  chtml: { fontURL: 'assets/mathjax/es5/output/chtml/fonts/woff-v2' }
};
</script>
<script nonce="__PI_PULSE_NONCE__" defer src="assets/mathjax/es5/tex-mml-chtml.js"></script>
""".strip()


def verify_mathjax_vendor() -> None:
    """Fail closed if a pinned executable/font asset changed unexpectedly."""
    sums = MATHJAX_VENDOR_DIR / "SHA256SUMS"
    if not sums.is_file():
        raise RuntimeError(f"missing MathJax integrity manifest: {sums}")
    manifested: set[Path] = set()
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            expected, relative = line.split(maxsplit=1)
        except ValueError as exc:
            raise RuntimeError("malformed MathJax integrity manifest") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise RuntimeError("malformed MathJax integrity digest")
        asset = (MATHJAX_VENDOR_DIR / relative).resolve()
        if MATHJAX_VENDOR_DIR.resolve() not in asset.parents or not asset.is_file():
            raise RuntimeError(f"missing/unsafe MathJax asset: {relative}")
        manifested.add(asset)
        actual = hashlib.sha256(asset.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"MathJax integrity check failed: {relative}")
    actual_assets = {
        path.resolve()
        for path in MATHJAX_VENDOR_DIR.rglob("*")
        if path.is_file() and path.name not in {"README.md", "SHA256SUMS"}
    }
    if actual_assets != manifested:
        raise RuntimeError("MathJax vendor tree contains unmanifested/missing assets")


def install_mathjax_assets(output_path: Path) -> None:
    verify_mathjax_vendor()
    destination = output_path.parent / "assets" / "mathjax" / "es5"
    shutil.copytree(MATHJAX_VENDOR_DIR / "es5", destination, dirs_exist_ok=True)


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


def render_file(input_path: Path, output_path: Path) -> int:
    """Render a markdown brief and install any required local assets."""
    if not input_path.is_file():
        print(f"ERROR: not a file: {input_path}", file=sys.stderr)
        return 2

    md_text = input_path.read_text(encoding="utf-8")
    title = extract_title(md_text, input_path.stem)

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

    body = sanitize_html(body)

    with_mathjax = has_math(md_text)
    if with_mathjax:
        try:
            install_mathjax_assets(output_path)
        except (OSError, RuntimeError) as exc:
            print(f"ERROR: could not install verified MathJax assets ({exc})", file=sys.stderr)
            return 1

    html_doc = wrap(body, title=title, with_mathjax=with_mathjax)
    output_path.write_text(html_doc, encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    return render_file(args.input, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
