#!/usr/bin/env python3
"""Tests for sources/feedback_server.py (stdlib unittest).

Run from the repo root:
    python3 -m unittest discover tests -v

Covers the pure helpers (IP allowlist, RUN_ID validation, the
mark-update round-trip through the shared feedback grammar) plus one
end-to-end HTTP pass against a temp out dir.
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sources"))

import build_feedback_template  # noqa: E402
import feedback_server  # noqa: E402
import ingest_feedback  # noqa: E402
from review_feedback import parse_feedback_file  # noqa: E402

RUN_ID = "2026-01-02-0500"

BRIEF_MD = """# Pulse - test

A lede paragraph.

## First card (tracked)

Body one. [source](https://example.com/a)

## Second card (adjacent)

Body two. [source](https://example.com/b)

## Third card (bridge)

Body three. [source](https://example.com/c)
"""

BRIEF_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Pulse - test</title></head>
<body>
<main>
<h1>Pulse - test</h1>
<h2>First card (tracked)</h2><p>Body one.</p>
<h2>Second card (adjacent)</h2><p>Body two.</p>
<h2>Third card (bridge)</h2><p>Body three.</p>
</main>
</body>
</html>
"""


def make_feedback_text() -> str:
    titles = build_feedback_template.card_titles(BRIEF_MD)
    return build_feedback_template.render(RUN_ID, titles)


class TestIpAllowed(unittest.TestCase):
    def test_loopback_v4(self):
        self.assertTrue(feedback_server.ip_allowed("127.0.0.1"))
        self.assertTrue(feedback_server.ip_allowed("127.8.9.10"))

    def test_loopback_v6(self):
        self.assertTrue(feedback_server.ip_allowed("::1"))

    def test_tailscale_cgnat(self):
        self.assertTrue(feedback_server.ip_allowed("100.64.0.1"))
        self.assertTrue(feedback_server.ip_allowed("100.101.102.103"))
        self.assertTrue(feedback_server.ip_allowed("100.127.255.254"))

    def test_tailscale_v6(self):
        self.assertTrue(feedback_server.ip_allowed("fd7a:115c:a1e0::1234"))

    def test_lan_denied(self):
        self.assertFalse(feedback_server.ip_allowed("192.168.1.5"))
        self.assertFalse(feedback_server.ip_allowed("10.0.0.7"))

    def test_public_denied(self):
        self.assertFalse(feedback_server.ip_allowed("8.8.8.8"))
        self.assertFalse(feedback_server.ip_allowed("2001:4860:4860::8888"))

    def test_just_outside_cgnat_denied(self):
        self.assertFalse(feedback_server.ip_allowed("100.63.255.255"))
        self.assertFalse(feedback_server.ip_allowed("100.128.0.0"))

    def test_garbage_denied(self):
        self.assertFalse(feedback_server.ip_allowed("not-an-ip"))
        self.assertFalse(feedback_server.ip_allowed(""))

    def test_ipv4_mapped_v6(self):
        self.assertTrue(feedback_server.ip_allowed("::ffff:127.0.0.1"))
        self.assertFalse(feedback_server.ip_allowed("::ffff:192.168.1.5"))


class TestRunIdValidation(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(feedback_server.valid_run_id("2026-01-02-0500"))
        self.assertTrue(feedback_server.valid_run_id("2026-01-02"))  # legacy

    def test_invalid(self):
        for bad in (
            "../x",
            "../../etc/passwd",
            "2026-01-02-0500.html",
            "2026-01-02-0500/",
            "2026-1-2",
            "abc",
            "",
            "2026-01-02-05000",
        ):
            self.assertFalse(feedback_server.valid_run_id(bad), bad)


class TestOriginValidation(unittest.TestCase):
    HOSTS = feedback_server.allowed_origin_hosts("pulse.example")

    def test_absent_origin_is_allowed_for_non_browser_clients(self):
        self.assertTrue(feedback_server.origin_allowed(None, self.HOSTS, 8377))

    def test_same_origin_is_allowed(self):
        self.assertTrue(
            feedback_server.origin_allowed(
                "http://pulse.example:8377", self.HOSTS, 8377
            )
        )

    def test_foreign_or_malformed_origin_is_denied(self):
        self.assertFalse(
            feedback_server.origin_allowed(
                "https://attacker.example", self.HOSTS, 8377
            )
        )
        self.assertFalse(feedback_server.origin_allowed("null", self.HOSTS, 8377))

    def test_dns_rebinding_host_is_denied_despite_matching_host_header(self):
        # A rebinding page presents Origin: http://evil.example:8377 with a
        # matching Host header; validation is pinned to the bind host, so
        # the client-controlled pair must not matter.
        self.assertFalse(
            feedback_server.origin_allowed(
                "http://evil.example:8377", self.HOSTS, 8377
            )
        )

    def test_wrong_port_is_denied(self):
        self.assertFalse(
            feedback_server.origin_allowed(
                "http://pulse.example:9999", self.HOSTS, 8377
            )
        )

    def test_loopback_bind_allows_loopback_aliases(self):
        hosts = feedback_server.allowed_origin_hosts("127.0.0.1")
        self.assertTrue(
            feedback_server.origin_allowed("http://localhost:8377", hosts, 8377)
        )

    def test_extra_hosts_opt_in(self):
        hosts = feedback_server.allowed_origin_hosts(
            "100.64.1.2", "pulse.tail1234.ts.net"
        )
        self.assertTrue(
            feedback_server.origin_allowed(
                "http://pulse.tail1234.ts.net:8377", hosts, 8377
            )
        )
        self.assertFalse(
            feedback_server.origin_allowed("http://localhost:8377", hosts, 8377)
        )


class TestMarkRoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="pulse-fb-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.fb = self.dir / f"{RUN_ID}.feedback.md"
        self.fb.write_text(make_feedback_text())

    def test_set_mark_visible_to_ingest(self):
        feedback_server.set_mark(self.fb, card=2, mark="++")
        text = self.fb.read_text()
        rows = ingest_feedback.parse_feedback(text)
        self.assertEqual(rows, [{"card": 2, "rating": 2, "note": ""}])
        # Cards 1 and 3 stay unrated ([ ]) and are skipped by ingest.
        _, entries = parse_feedback_file(text)
        self.assertEqual([e["mark"] for e in entries], ["", "++", ""])
        self.assertEqual([e["num"] for e in entries], [1, 2, 3])

    def test_neutral_is_rated_zero(self):
        feedback_server.set_mark(self.fb, card=1, mark="=")
        rows = ingest_feedback.parse_feedback(self.fb.read_text())
        self.assertEqual(rows, [{"card": 1, "rating": 0, "note": ""}])

    def test_clear_back_to_unrated(self):
        feedback_server.set_mark(self.fb, card=3, mark="--")
        feedback_server.set_mark(self.fb, card=3, mark="")
        self.assertEqual(ingest_feedback.parse_feedback(self.fb.read_text()), [])

    def test_note_set_and_cleared(self):
        feedback_server.set_mark(self.fb, card=2, mark="+", note="great follow-up")
        rows = ingest_feedback.parse_feedback(self.fb.read_text())
        self.assertEqual(rows, [{"card": 2, "rating": 1, "note": "great follow-up"}])
        # Omitting the note leaves it untouched.
        feedback_server.set_mark(self.fb, card=2, mark="++")
        rows = ingest_feedback.parse_feedback(self.fb.read_text())
        self.assertEqual(rows, [{"card": 2, "rating": 2, "note": "great follow-up"}])
        # note="" clears it.
        feedback_server.set_mark(self.fb, card=2, mark="++", note="")
        rows = ingest_feedback.parse_feedback(self.fb.read_text())
        self.assertEqual(rows, [{"card": 2, "rating": 2, "note": ""}])

    def test_unknown_card_rejected(self):
        with self.assertRaises(ValueError):
            feedback_server.set_mark(self.fb, card=9, mark="+")

    def test_bad_mark_never_reaches_grammar(self):
        # The handler gates marks before set_mark; apply_rating itself
        # would happily store junk, so assert the gate set is right.
        self.assertEqual(feedback_server.VALID_MARKS, {"++", "+", "=", "-", "--"})

    def test_atomic_write_leaves_no_tmp(self):
        feedback_server.set_mark(self.fb, card=1, mark="+")
        leftovers = [p.name for p in self.dir.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])

    def test_clean_note(self):
        self.assertEqual(feedback_server.clean_note("  a\nb\r\nc  "), "a b c")
        self.assertEqual(feedback_server.clean_note("\n\n"), "")


class TestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="pulse-fb-http-"))
        (cls.dir / f"{RUN_ID}.md").write_text(BRIEF_MD)
        (cls.dir / f"{RUN_ID}.html").write_text(BRIEF_HTML)
        (cls.dir / f"{RUN_ID}.feedback.md").write_text(make_feedback_text())
        cls.server = feedback_server.make_server("127.0.0.1", 0, cls.dir)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str):
        with urllib.request.urlopen(self.url(path), timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")

    def post_json(self, obj: dict, *, origin: str | None = None):
        headers = {"Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        req = urllib.request.Request(
            self.url("/api/rate"),
            data=json.dumps(obj).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_index_lists_run(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(RUN_ID, body)
        self.assertIn("3 cards", body)

    def test_brief_page_injected(self):
        with urllib.request.urlopen(self.url(f"/brief/{RUN_ID}"), timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read().decode("utf-8")
            csp = resp.headers["Content-Security-Policy"]
        self.assertIn("window.__pulseState", body)
        self.assertIn("pulse-rate", body)
        self.assertIn("First card (tracked)", body)
        match = re.search(r"script-src 'nonce-([^']+)'", csp)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertGreaterEqual(body.count(f'nonce="{match.group(1)}"'), 2)
        self.assertNotIn("script-src 'unsafe-inline'", csp)
        self.assertIn("connect-src 'self'", csp)
        # The widget block lands before </body>.
        self.assertLess(body.index("window.__pulseState"), body.index("</body>"))

    def test_historical_script_does_not_receive_nonce(self):
        page = feedback_server.inject_widget(
            "<html><body><script>alert('old')</script></body></html>",
            RUN_ID,
            [],
            "test-nonce",
        )
        self.assertIn("<script>alert('old')</script>", page)
        self.assertNotIn('<script nonce="test-nonce">alert', page)

    def test_nonce_placeholder_in_hostile_script_is_not_activated(self):
        page = feedback_server.inject_widget(
            '<html><body><script nonce="__PI_PULSE_NONCE__">alert(1)</script></body></html>',
            RUN_ID,
            [],
            "test-nonce",
        )
        self.assertIn('<script nonce="__PI_PULSE_NONCE__">alert(1)</script>', page)
        self.assertNotIn('<script nonce="test-nonce">alert(1)</script>', page)

    def test_historical_mathjax_loader_is_upgraded_to_local_fixed_block(self):
        old_page = (
            '<html><head><script defer src="https://cdn.jsdelivr.net/npm/'
            'mathjax@3/es5/tex-mml-chtml.js"></script></head><body></body></html>'
        )
        page = feedback_server.inject_widget(old_page, RUN_ID, [], "test-nonce")
        self.assertNotIn("cdn.jsdelivr.net", page)
        self.assertIn('src="assets/mathjax/es5/tex-mml-chtml.js"', page)
        self.assertIn('<script nonce="test-nonce">\nwindow.MathJax', page)

    def test_mathjax_asset_route_serves_only_pinned_files(self):
        with urllib.request.urlopen(
            self.url("/brief/assets/mathjax/es5/tex-mml-chtml.js"), timeout=5
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get_content_type(), "text/javascript")
            self.assertTrue(resp.read())

    def test_lazy_tex_extension_is_vendored_and_served(self):
        # MathJax 3.2.2 bundles only the default TeX package set. `\boldsymbol`
        # and friends are fetched at typeset time from [tex]/extensions/. When
        # that fetch fails the typeset promise rejects and EVERY expression on
        # the page stays raw TeX -- so a missing extension is a whole-page
        # outage, not a single-macro degradation.
        with urllib.request.urlopen(
            self.url("/brief/assets/mathjax/es5/input/tex/extensions/boldsymbol.js"),
            timeout=5,
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get_content_type(), "text/javascript")
            self.assertTrue(resp.read())

    def test_brief_csp_allows_mathjax_to_inject_its_extension_loader(self):
        # MathJax 3.2.2 cannot stamp a nonce on the <script> it injects, so a
        # nonce-only script-src blocks the extension load and silences the
        # page's math. 'strict-dynamic' is what lets the already-trusted,
        # integrity-checked bundle pull its own same-origin extensions in.
        with urllib.request.urlopen(self.url(f"/brief/{RUN_ID}"), timeout=5) as resp:
            csp = resp.headers["Content-Security-Policy"]
        self.assertIn("'strict-dynamic'", csp)
        self.assertRegex(csp, r"script-src 'nonce-[^']+' 'strict-dynamic'")
        # Body-authored scripts still carry no nonce, so they stay inert.
        self.assertNotIn("script-src 'unsafe-inline'", csp)
        self.assertNotIn("'unsafe-eval'", csp)

    def test_rate_round_trip(self):
        status, res = self.post_json(
            {"run_id": RUN_ID, "card": 2, "mark": "++", "note": "web note"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            res,
            {"ok": True, "run_id": RUN_ID, "card": 2, "mark": "++", "note": "web note"},
        )
        text = (self.dir / f"{RUN_ID}.feedback.md").read_text()
        self.assertEqual(
            ingest_feedback.parse_feedback(text),
            [{"card": 2, "rating": 2, "note": "web note"}],
        )
        # Reset so other tests see the pristine file regardless of order.
        (self.dir / f"{RUN_ID}.feedback.md").write_text(make_feedback_text())

    def test_same_origin_post_succeeds(self):
        status, res = self.post_json(
            {"run_id": RUN_ID, "card": 1, "mark": "+"},
            origin=f"http://127.0.0.1:{self.port}",
        )
        self.assertEqual(status, 200)
        self.assertTrue(res["ok"])
        (self.dir / f"{RUN_ID}.feedback.md").write_text(make_feedback_text())

    def test_foreign_origin_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post_json(
                {"run_id": RUN_ID, "card": 1, "mark": "+"},
                origin="https://attacker.example",
            )
        self.assertEqual(cm.exception.code, 403)

    def test_non_json_content_type_is_refused(self):
        req = urllib.request.Request(
            self.url("/api/rate"),
            data=b"run_id=x",
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 415)

    def test_bad_run_id_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post_json({"run_id": "../x", "card": 1, "mark": "+"})
        self.assertEqual(cm.exception.code, 404)

    def test_missing_run_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post_json({"run_id": "2001-01-01-0101", "card": 1, "mark": "+"})
        self.assertEqual(cm.exception.code, 404)

    def test_bad_mark_400(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post_json({"run_id": RUN_ID, "card": 1, "mark": "+++"})
        self.assertEqual(cm.exception.code, 400)

    def test_unknown_card_400(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post_json({"run_id": RUN_ID, "card": 42, "mark": "+"})
        self.assertEqual(cm.exception.code, 400)

    def test_traversal_path_404(self):
        # urllib normalizes dot segments, so speak raw HTTP to guarantee
        # the hostile path actually reaches the server.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", "/brief/../../etc/passwd")
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 404)
        finally:
            conn.close()
        # And the fixture dir is untouched apart from the three files.
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(
            names,
            sorted([f"{RUN_ID}.md", f"{RUN_ID}.html", f"{RUN_ID}.feedback.md"]),
        )

    def test_unknown_route_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/etc/passwd")
        self.assertEqual(cm.exception.code, 404)

    def test_oversized_body_413(self):
        big = json.dumps({"run_id": RUN_ID, "card": 1, "mark": "+", "note": "x" * 20000})
        req = urllib.request.Request(
            self.url("/api/rate"),
            data=big.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 413)

    def test_negative_content_length_400(self):
        # urllib/http.client always compute Content-Length, so speak raw
        # HTTP. A negative length must be rejected BEFORE the body read:
        # rfile.read(-1) reads until EOF, i.e. unbounded memory and a
        # worker thread blocked until the client hangs up. We send no
        # body and no EOF -- a vulnerable server blocks and this recv
        # times out instead of returning 400.
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(
                b"POST /api/rate HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: -1\r\n"
                b"\r\n"
            )
            status_line = sock.recv(4096).split(b"\r\n", 1)[0]
        self.assertIn(b" 400 ", status_line)

    def test_security_headers(self):
        with urllib.request.urlopen(self.url("/"), timeout=5) as resp:
            self.assertEqual(resp.headers["Cache-Control"], "no-store")
            self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("default-src 'none'", resp.headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
