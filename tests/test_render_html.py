"""Tests for sources/render_html.py currency-vs-math handling.

Regression coverage for the long-standing bug where currency dollars in a
brief ("Uber's $1,500/month") were paired by MathJax's in-browser scanner
into bogus inline math, while genuine TeX ("$S_{\\text{token}} \\le X$") in
the same paragraph rendered fine.

Run: python3 -m unittest discover tests
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "sources" / "render_html.py"
_spec = importlib.util.spec_from_file_location("render_html", _MODULE_PATH)
assert _spec and _spec.loader
render_html = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_html)


# The exact problem sentences lifted from out/2026-07-05-0514.md: currency
# and TeX mixed in one paragraph.
MIXED_SENTENCE = (
    "The formula $S_{\\text{token}} \\leq X \\cdot \\kappa \\cdot L / m$ needs "
    "an anchor. Tesla's ~$800/month cap implies $X \\approx 0.03$--$0.05$, "
    "tighter than your $75--100/day ($2,250--$3,000/month) budget. Uber's "
    "$1,500/month-per-tool figure is bounded; the median firm spends just "
    "$11.38 and the top 10% spend $611. The multiplier $m$ is underestimated."
)

CURRENCY_ONLY = (
    "Tesla's $200/week AI spending cap, Uber's $1,500/month-per-tool limit, "
    "$7,449/employee/month, the top 10% spend $611, the median firm spends "
    "just $11.38 -- a spread from $50 to $90,000/year."
)

DISPLAY_MATH = (
    "The counterexample you derived,\n\n"
    "$$E = \\frac{1}{2}\\log\\frac{(1-ab)^2}{(1-a^2)(1-b^2)}$$\n\n"
    "predicts a distortion."
)


class InlineMathDiscriminatorTests(unittest.TestCase):
    """The INLINE_MATH_RE delimiter rules separate math from currency."""

    def _spans(self, text: str) -> list[str]:
        return [m.group(0) for m in render_html.INLINE_MATH_RE.finditer(text)]

    def test_variables_and_commands_match(self) -> None:
        for span in ["$X$", "$m$", "$\\kappa$", "$S_{\\text{token}}$", "$X \\approx 0.03$"]:
            self.assertEqual(self._spans(f"a {span} b"), [span], span)

    def test_currency_never_matches(self) -> None:
        # Leading digit means it can never open a span.
        for amt in ["$611", "$1,500/month", "$0.05", "$11.38", "$200/week"]:
            self.assertEqual(self._spans(f"cap {amt} end"), [], amt)

    def test_currency_does_not_swallow_following_math_opener(self) -> None:
        # "$800 ... $X ... $" -- $800 must not pair with $X's opener.
        spans = self._spans("~$800/month cap implies $X \\approx 0.03$ ok")
        self.assertEqual(spans, ["$X \\approx 0.03$"])

    def test_two_adjacent_amounts_do_not_pair(self) -> None:
        self.assertEqual(self._spans("prices $50 then $611 done"), [])


class HasMathTests(unittest.TestCase):
    def test_genuine_inline_math_detected(self) -> None:
        self.assertTrue(render_html.has_math("worth $X \\approx 0.03$ today"))

    def test_display_math_detected(self) -> None:
        self.assertTrue(render_html.has_math(DISPLAY_MATH))

    def test_currency_only_brief_loads_no_mathjax(self) -> None:
        # The load-bearing assertion: a brief with ONLY currency dollars
        # must NOT trigger MathJax injection.
        self.assertFalse(render_html.has_math(CURRENCY_ONLY))

    def test_two_adjacent_amounts_not_paired(self) -> None:
        self.assertFalse(render_html.has_math("prices $50 then $611 done"))

    def test_mixed_sentence_detected(self) -> None:
        self.assertTrue(render_html.has_math(MIXED_SENTENCE))


class ConvertMathDelimitersTests(unittest.TestCase):
    def test_currency_untouched(self) -> None:
        out = render_html.convert_math_delimiters(CURRENCY_ONLY)
        self.assertEqual(out, CURRENCY_ONLY)
        self.assertNotIn("\\(", out)

    def test_inline_math_converted(self) -> None:
        out = render_html.convert_math_delimiters("worth $X \\approx 0.03$ ok")
        self.assertIn("\\(X \\approx 0.03\\)", out)
        self.assertNotIn("$", out)

    def test_display_math_converted(self) -> None:
        out = render_html.convert_math_delimiters(DISPLAY_MATH)
        self.assertIn("\\[E = \\frac{1}{2}", out)
        self.assertNotIn("$$", out)

    def test_mixed_sentence_currency_survives_math_converts(self) -> None:
        out = render_html.convert_math_delimiters(MIXED_SENTENCE)
        # Genuine math wrapped.
        self.assertIn("\\(S_{\\text{token}} \\leq X \\cdot \\kappa \\cdot L / m\\)", out)
        self.assertIn("\\(m\\)", out)
        # Currency preserved verbatim, never wrapped.
        self.assertIn("~$800/month", out)
        self.assertIn("$1,500/month-per-tool", out)
        self.assertIn("$11.38", out)
        self.assertIn("$611", out)
        # No math span leaked a currency word.
        for m in render_html.INLINE_MATH_RE.finditer(out):
            self.fail(f"unconverted $-span remained: {m.group(0)!r}")


class ProtectMathTests(unittest.TestCase):
    def test_currency_left_as_literal_dollars(self) -> None:
        text, spans = render_html._protect_math(CURRENCY_ONLY)
        self.assertEqual(text, CURRENCY_ONLY)
        self.assertEqual(spans, [])

    def test_math_replaced_by_tokens(self) -> None:
        text, spans = render_html._protect_math("worth $X \\approx 0.03$ now")
        self.assertNotIn("$", text)
        self.assertEqual(len(spans), 1)
        token, tex = spans[0]
        self.assertIn(token, text)
        self.assertEqual(tex, "\\(X \\approx 0.03\\)")

    def test_display_and_inline_both_protected(self) -> None:
        text, spans = render_html._protect_math(MIXED_SENTENCE)
        self.assertNotIn("\\text{token}", text)  # pulled out
        self.assertTrue(any(t.startswith("\\(") for _, t in spans))
        # Currency dollars still present as literal text.
        self.assertIn("$1,500/month-per-tool", text)
        self.assertIn("$611", text)


_HAVE_MARKDOWN = importlib.util.find_spec("markdown") is not None


@unittest.skipUnless(_HAVE_MARKDOWN, "markdown package not installed")
class MarkdownFallbackTests(unittest.TestCase):
    """The fallback path must keep the `\\(`/`\\[` backslashes that the
    markdown package would otherwise strip."""

    def test_backslash_delimiters_survive(self) -> None:
        body = render_html.render_with_markdown("worth $X \\approx 0.03$ now")
        self.assertIn("\\(X \\approx 0.03\\)", body)

    def test_currency_stays_literal(self) -> None:
        body = render_html.render_with_markdown(CURRENCY_ONLY)
        self.assertIn("$611", body)
        self.assertNotIn("\\(", body)


class RenderIntegrationTests(unittest.TestCase):
    """Full render() on a fixture with the exact problem sentences.

    Pandoc assertions are skipped gracefully when pandoc is absent, but do
    run locally (pandoc is the primary path).
    """

    def _render(self, md: str) -> str:
        # render() reads/writes files; drive it via a temp dir.
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "brief.md"
            dst = Path(d) / "brief.html"
            src.write_text(md, encoding="utf-8")
            rc = render_html.render_file(src, dst)
            self.assertEqual(rc, 0)
            return dst.read_text(encoding="utf-8")

    def test_mathjax_config_has_no_bare_dollar_delimiter(self) -> None:
        # The MathJax config block must never register `$` as a delimiter.
        self.assertNotIn("['$', '$']", render_html.MATHJAX)
        self.assertIn("\\\\(", render_html.MATHJAX)

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc not installed")
    def test_pandoc_path_no_currency_in_math_and_math_wrapped(self) -> None:
        body = render_html.render_with_pandoc(MIXED_SENTENCE + "\n\n" + DISPLAY_MATH)
        self.assertIsNotNone(body)
        assert body is not None
        # No math span containing a currency word.
        import re

        for span in re.findall(r'class="math[^"]*">([^<]*)', body):
            for word in ("week", "Uber", "spend", "firms", "month"):
                self.assertNotIn(word, span, f"currency leaked into math: {span!r}")
        # Genuine formulas ARE inside \(...\)/\[...\].
        self.assertIn("S_{\\text{token}}", body)
        self.assertIn("\\frac{1}{2}", body)

    @unittest.skipUnless(
        shutil.which("pandoc") or _HAVE_MARKDOWN,
        "neither pandoc nor markdown available",
    )
    def test_full_render_currency_safe_and_math_present(self) -> None:
        import re

        html_doc = self._render("# Brief\n\n" + MIXED_SENTENCE + "\n\n" + DISPLAY_MATH)
        # (a) No math span/delimiter pair whose content carries a currency word.
        pairs = re.findall(r'class="math[^"]*">([^<]*)', html_doc)
        pairs += re.findall(r"\\\(([^)]*?)\\\)", html_doc)
        pairs += re.findall(r"\\\[([\s\S]*?)\\\]", html_doc)
        for span in pairs:
            for word in ("week", "Uber", "spend", "firms", "month"):
                self.assertNotIn(word, span, f"currency leaked into math: {span!r}")
        # (b) Genuine formulas ARE inside math delimiters.
        self.assertIn("S_{\\text{token}}", html_doc)
        self.assertIn("\\frac{1}{2}", html_doc)
        # (c) MathJax loaded (math present) but config has no bare `$`.
        self.assertIn("assets/mathjax/es5/tex-mml-chtml.js", html_doc)
        self.assertNotIn("cdn.jsdelivr.net", html_doc)
        self.assertNotIn("['$', '$']", html_doc)

    def test_wrap_injects_mathjax_only_when_math_present(self) -> None:
        with_math = render_html.wrap("<p>x</p>", "t", with_mathjax=True)
        self.assertIn("MathJax", with_math)
        without = render_html.wrap("<p>x</p>", "t", with_mathjax=False)
        self.assertNotIn("MathJax", without)

    def test_math_render_copies_verified_local_assets(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "brief.md"
            dst = Path(d) / "brief.html"
            src.write_text("# Brief\n\n$$x^2$$\n", encoding="utf-8")
            self.assertEqual(render_html.render_file(src, dst), 0)
            asset = Path(d) / "assets" / "mathjax" / "es5" / "tex-mml-chtml.js"
            self.assertTrue(asset.is_file())
            self.assertIn(render_html.MATHJAX_NONCE_TOKEN, dst.read_text())


class HtmlSanitizerTests(unittest.TestCase):
    def test_markdown_image_output_is_removed(self) -> None:
        dirty = '<p>before <img src="https://attacker.example/pixel.png" alt="x"> after</p>'
        clean = render_html.sanitize_html(dirty)
        self.assertNotIn("<img", clean)
        self.assertNotIn("attacker.example", clean)
        self.assertIn("before", clean)
        self.assertIn("after", clean)

    def test_script_and_contents_are_removed(self) -> None:
        clean = render_html.sanitize_html("<p>safe</p><script>steal(secret)</script><p>end</p>")
        self.assertNotIn("script", clean)
        self.assertNotIn("steal", clean)
        self.assertNotIn("secret", clean)
        self.assertIn("safe", clean)
        self.assertIn("end", clean)

    def test_javascript_href_is_removed_but_link_text_survives(self) -> None:
        clean = render_html.sanitize_html('<a href="javascript:alert(1)">read me</a>')
        self.assertEqual(clean, "<a>read me</a>")

    def test_control_obfuscated_javascript_href_is_removed(self) -> None:
        clean = render_html.sanitize_html(
            '<a href="\x00javascript:alert(1)">read me</a>'
        )
        self.assertEqual(clean, "<a>read me</a>")

    def test_http_link_survives_and_event_attributes_do_not(self) -> None:
        clean = render_html.sanitize_html(
            '<a href="https://example.com/paper" onclick="steal()" style="display:none">paper</a>'
        )
        self.assertEqual(clean, '<a href="https://example.com/paper">paper</a>')

    def test_unknown_tag_keeps_text_and_display_math_delimiters(self) -> None:
        clean = render_html.sanitize_html('<span class="math display">\\[x^2\\]</span>')
        self.assertEqual(clean, "\\[x^2\\]")

    def test_protocol_relative_href_is_removed(self) -> None:
        clean = render_html.sanitize_html('<a href="//attacker.example/x">go</a>')
        self.assertEqual(clean, "<a>go</a>")

    def test_relative_href_survives(self) -> None:
        clean = render_html.sanitize_html('<a href="/brief/2026-08-08-0500">go</a>')
        self.assertEqual(clean, '<a href="/brief/2026-08-08-0500">go</a>')

    def test_restored_math_is_escaped_not_parsed_as_markup(self) -> None:
        # The markdown-fallback path splices protected TeX back into the
        # rendered HTML; a `<` inside math must stay text (not truncate the
        # paragraph) and markup inside math must not become live elements.
        spans = [
            ("zzPIMATH0zz", "\\[E = \\frac{a<b}{2}\\]"),
            ("zzPIMATH1zz", '\\(<a href="https://evil.example">x</a>\\)'),
        ]
        body = "<p>zzPIMATH0zz and zzPIMATH1zz</p>"
        clean = render_html.sanitize_html(render_html._restore_math(body, spans))
        self.assertIn("\\[E = \\frac{a&lt;b}{2}\\]", clean)
        self.assertNotIn("<a", clean)
        self.assertNotIn("evil.example</a>", clean)
        self.assertTrue(clean.endswith("</p>"))

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc not installed")
    def test_full_markdown_render_drops_image_and_raw_html(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "brief.md"
            dst = Path(d) / "brief.html"
            src.write_text(
                "# Brief\n\n![x](https://attacker.example/p.png)\n\n"
                "<script>forged feedback</script>\n\n"
                "[source](https://example.com/paper)\n",
                encoding="utf-8",
            )
            self.assertEqual(render_html.render_file(src, dst), 0)
            output = dst.read_text(encoding="utf-8")
        self.assertNotIn("<img", output)
        self.assertNotIn("attacker.example", output)
        self.assertNotIn("forged feedback", output)
        self.assertIn('<a href="https://example.com/paper">source</a>', output)


if __name__ == "__main__":
    unittest.main()
