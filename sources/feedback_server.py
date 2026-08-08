#!/usr/bin/env python3
"""Minimal stdlib web server for rating pulse briefs from any tailnet device.

Serves the already-rendered `out/*.html` briefs with an injected rating
widget and writes marks back into `out/{RUN_ID}.feedback.md` using the
exact grammar `review_feedback.py` / `ingest_feedback.py` already speak.
The markdown feedback files stay the single source of truth: the next
`pulse.sh` run sweeps them via `scripts/ingest-feedback.sh --all`
unchanged, so this server needs (and has) no ingest logic of its own.

Security posture
----------------
Transport security and authentication come from the Tailscale network
boundary: bind to the machine's tailnet IP (`--host tailscale`) and only
devices on your tailnet can reach the port at all, over WireGuard.
The server adds defense-in-depth on top:

* Per-request client-IP allowlist (`ip_allowed`): only loopback
  (127.0.0.0/8, ::1) and Tailscale addresses (CGNAT 100.64.0.0/10,
  IPv6 fd7a:115c:a1e0::/48) are served; anything else gets 403. This
  protects against an accidental `0.0.0.0` or LAN-interface binding.
* `RUN_ID` is validated against a strict regex and is the ONLY request
  data ever interpolated into a filesystem path -- paths are always
  constructed as `out_dir / f"{run_id}.html"` etc., never from raw
  request paths, and there is no generic static-file serving.
* Writes are confined to `out/*.feedback.md`, applied under a lock via
  parse -> mutate -> serialize -> atomic replace. POST bodies are
  capped at 16 KB.
* `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` on
  every response; brief scripts receive a per-response nonce and run
  under a strict CSP.
* Rating writes require JSON and reject a foreign browser Origin before
  reading the request body.
* No shell-outs except the optional `tailscale ip -4` lookup at startup.

Usage:
    feedback_server.py [--host HOST|tailscale] [--port PORT] [--out-dir DIR]

Env fallbacks: PI_PULSE_FEEDBACK_HOST (default 127.0.0.1),
PI_PULSE_FEEDBACK_PORT (default 8377). Makes zero model calls.
"""

from __future__ import annotations

import argparse
import html
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from render_html import MATHJAX, MATHJAX_NONCE_TOKEN, MATHJAX_VENDOR_DIR, verify_mathjax_vendor
from review_feedback import VALID_MARKS, parse_feedback_file, serialize_feedback

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "out"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8377

RUN_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(-\d{4})?$")
MAX_BODY_BYTES = 16 * 1024
MAX_NOTE_CHARS = 500

# Loopback plus the ranges Tailscale hands out (IPv4 CGNAT and its fixed
# IPv6 /48). A request from any other address -- e.g. after an accidental
# 0.0.0.0 binding exposes the port to the LAN -- is refused outright.
ALLOWED_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)

# Serializes feedback-file writes; reads happen inside the same critical
# section so two concurrent taps can't clobber each other's marks.
_WRITE_LOCK = threading.Lock()


# --- pure helpers (unit-testable) --------------------------------------


def ip_allowed(addr: str) -> bool:
    """True iff addr is loopback or a Tailscale address."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])  # strip zone id
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 -> 127.0.0.1
    return any(ip in net for net in ALLOWED_NETS)


def valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_RE.match(run_id))


def clean_note(raw: str) -> str:
    """Collapse whitespace (killing newlines, which would break the
    one-line `note:` grammar) and strip."""
    return " ".join(raw.split())


LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def allowed_origin_hosts(bind_host: str, extra_hosts: str | None = None) -> frozenset[str]:
    """Hostnames that count as this server's own origin.

    The bind address itself, loopback aliases when bound to loopback, and
    any comma-separated extras (e.g. a MagicDNS name) the operator opted
    into via PI_PULSE_FEEDBACK_ALLOWED_HOSTS.
    """
    hosts = {bind_host.rstrip(".").lower()}
    if hosts & LOOPBACK_HOSTS:
        hosts |= LOOPBACK_HOSTS
    for name in (extra_hosts or "").split(","):
        name = name.strip().rstrip(".").lower()
        if name:
            hosts.add(name)
    return frozenset(hosts)


def origin_allowed(origin: str | None, allowed_hosts: frozenset[str], port: int) -> bool:
    """True for no Origin (non-browser clients) or an Origin naming this
    server's own configured identity.

    The Origin is compared against the *configured bind host*, never the
    client-supplied Host header: trusting Host lets a DNS-rebinding page
    (an attacker name resolving to this server's IP) present a matching
    Origin/Host pair and drive the API cross-origin.
    """
    if origin is None:
        return True
    if origin == "null":
        return False
    try:
        parsed_origin = urlsplit(origin)
        origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
    except ValueError:
        return False
    if parsed_origin.scheme not in {"http", "https"}:
        return False
    if parsed_origin.username or parsed_origin.password:
        return False
    if parsed_origin.path not in {"", "/"} or parsed_origin.query or parsed_origin.fragment:
        return False
    hostname = (parsed_origin.hostname or "").rstrip(".").lower()
    return hostname in allowed_hosts and origin_port == port


def apply_rating(text: str, card: int, mark: str, note: str | None) -> tuple[str, dict]:
    """Set card's mark (and note, if given) in a feedback file's text.

    Returns (new_text, updated_entry). note=None leaves the existing
    note untouched; note="" clears it. Raises ValueError on an unknown
    card number.
    """
    header, entries = parse_feedback_file(text)
    entry = next((e for e in entries if e["num"] == card), None)
    if entry is None:
        raise ValueError(f"card {card} not found in feedback file")
    entry["mark"] = mark
    if note is not None:
        entry["note"] = note
    return serialize_feedback(header, entries), entry


def set_mark(path: Path, card: int, mark: str, note: str | None = None) -> dict:
    """Re-read, mutate, and atomically rewrite a feedback file."""
    with _WRITE_LOCK:
        text = path.read_text(errors="replace")
        new_text, entry = apply_rating(text, card, mark, note)
        # Same-directory temp file so os.replace is an atomic rename:
        # a reader (or the ingest sweep) never sees a half-written file.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(new_text)
        os.replace(tmp, path)
    return entry


def discover_runs(out_dir: Path) -> list[dict]:
    """[{run_id, total, rated}] newest-first from out/*.feedback.md."""
    runs: list[dict] = []
    for fb in sorted(out_dir.glob("*.feedback.md"), reverse=True):
        if "_backup" in fb.name:
            continue
        run_id = fb.name[: -len(".feedback.md")]
        _, entries = parse_feedback_file(fb.read_text(errors="replace"))
        runs.append(
            {
                "run_id": run_id,
                "total": len(entries),
                "rated": sum(1 for e in entries if e["mark"]),
            }
        )
    return runs


def resolve_tailscale_ip() -> str:
    """Autodetect this machine's Tailscale IPv4 (for --host tailscale)."""
    candidates = (
        ["tailscale", "ip", "-4"],
        ["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "ip", "-4"],
    )
    for cmd in candidates:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        ip = proc.stdout.strip().splitlines()[0].strip() if proc.stdout.strip() else ""
        if proc.returncode == 0 and ip:
            return ip
    raise SystemExit(
        "ERROR: could not autodetect a Tailscale IPv4 address. Tried "
        "`tailscale ip -4` on PATH and the Tailscale.app CLI. Is "
        "Tailscale installed and connected? (Or pass an explicit --host.)"
    )


def resolve_tailscale_dns_name() -> str | None:
    """This machine's own MagicDNS name, if available.

    Browsers reaching the server via http://<machine-tailnet-name>:PORT/
    send that name in the Origin header, so it must count as the server's
    own identity for the rating API's origin check.
    """
    candidates = (
        ["tailscale", "status", "--json"],
        ["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "status", "--json"],
    )
    for cmd in candidates:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            name = json.loads(proc.stdout).get("Self", {}).get("DNSName", "")
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, AttributeError):
            continue
        if proc.returncode == 0 and isinstance(name, str) and name.strip():
            return name.strip().rstrip(".")
    return None


# --- HTML generation ----------------------------------------------------

INDEX_CSS = """
:root { --fg:#1a1a1a; --bg:#fafaf7; --muted:#555; --link:#0a58ca;
        --card:#ffffff; --rule:#d8d4cc; --hot:#b45309; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e7e3da; --bg:#1c1b18; --muted:#a8a39a; --link:#7fb3ff;
          --card:#26241f; --rule:#3a3733; --hot:#f0a350; }
}
* { box-sizing: border-box; }
body { margin:0; padding:1.5rem 1rem 4rem; color:var(--fg); background:var(--bg);
  font:17px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width:38rem; margin:0 auto; }
h1 { font-size:1.4rem; margin:0 0 1rem; }
a.run { display:flex; justify-content:space-between; align-items:baseline; gap:1rem;
  padding:0.9rem 1rem; margin:0.5rem 0; border:1px solid var(--rule); border-radius:10px;
  background:var(--card); color:var(--link); text-decoration:none; font-size:1.05rem; }
a.run .counts { color:var(--muted); font-size:0.9rem; white-space:nowrap; }
a.run.unrated { border-color:var(--hot); }
a.run.unrated .counts { color:var(--hot); font-weight:600; }
p.empty { color:var(--muted); }
""".strip()


def index_html(runs: list[dict]) -> str:
    items: list[str] = []
    for r in runs:
        rid = html.escape(r["run_id"])
        unrated = r["total"] - r["rated"]
        cls = "run unrated" if unrated else "run"
        counts = f"{unrated} unrated" if unrated else "all rated"
        items.append(
            f'<a class="{cls}" href="/brief/{rid}"><span>{rid}</span>'
            f'<span class="counts">{r["total"]} cards &middot; {counts}</span></a>'
        )
    body = "\n".join(items) if items else '<p class="empty">No feedback files found.</p>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pi-pulse feedback</title>
<style>
{INDEX_CSS}
</style>
</head>
<body>
<main>
<h1>pi-pulse feedback</h1>
{body}
</main>
</body>
</html>
"""


WIDGET_CSS = """
body { padding-top: 3.2rem !important; }
#pulse-topbar { position:fixed; top:0; left:0; right:0; z-index:1000;
  display:flex; justify-content:space-between; align-items:center;
  padding:0.5rem 1rem; font-size:0.95rem;
  background:#eee8db; color:#1a1a1a; border-bottom:1px solid #d8d4cc; }
#pulse-topbar a { color:#0a58ca; text-decoration:none; padding:0.3rem 0; }
@media (prefers-color-scheme: dark) {
  #pulse-topbar { background:#2a2723; color:#e7e3da; border-color:#3a3733; }
  #pulse-topbar a { color:#7fb3ff; }
}
.pulse-rate { display:flex; flex-wrap:wrap; gap:0.4rem; align-items:center;
  margin:0.5rem 0 0.8rem; }
.pulse-rate button { min-width:2.8rem; min-height:2.6rem; padding:0.3rem 0.6rem;
  font-size:1rem; border:1px solid #8888; border-radius:8px;
  background:transparent; color:inherit; cursor:pointer; }
.pulse-rate button.on { background:#0a58ca; border-color:#0a58ca; color:#fff; }
.pulse-status { font-size:0.85rem; opacity:0.8; }
.pulse-note { display:flex; gap:0.4rem; margin:0 0 0.8rem; }
.pulse-note input { flex:1; min-height:2.4rem; padding:0.3rem 0.6rem;
  font-size:1rem; border:1px solid #8888; border-radius:8px;
  background:transparent; color:inherit; }
.pulse-note button { min-height:2.4rem; padding:0.3rem 0.8rem; font-size:1rem;
  border:1px solid #8888; border-radius:8px; background:transparent;
  color:inherit; cursor:pointer; }
""".strip()

WIDGET_JS = """
(function () {
  var state = window.__pulseState || { run_id: "", entries: [] };

  function post(body, ok, fail) {
    fetch("/api/rate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(ok).catch(fail);
  }

  var MARKS = [["++","++"],["+","+"],["=","="],["-","\\u2212"],["--","\\u2212\\u2212"],["","clear"]];
  var h2s = document.querySelectorAll("h2");
  var n = Math.min(h2s.length, state.entries.length);

  // One factory per card. Every bar built for the same card registers
  // itself in a shared `instances` list; `syncMarks`/`syncNote` push the
  // authoritative `entry` state back onto all of them, so rating (or
  // taking a note) from the top bar instantly updates the bottom bar and
  // vice versa. `entry` is the single source of truth: clicks mutate it
  // optimistically, a successful POST confirms it, a failed POST reverts.
  function cardFactory(entry) {
    var instances = [];
    function syncMarks() {
      instances.forEach(function (inst) { inst.setOn(entry.mark); });
    }
    function syncNote() {
      instances.forEach(function (inst) { inst.renderNote(); });
    }

    function bar() {
      var barEl = document.createElement("div");
      barEl.className = "pulse-rate";
      var btns = [];
      function setOn(mark) {
        btns.forEach(function (p) { p[1].className = p[0] === mark ? "on" : ""; });
      }
      var status = document.createElement("span");
      status.className = "pulse-status";
      var timer;
      function flash(msg, isErr) {
        status.textContent = msg;
        status.style.color = isErr ? "#e05252" : "";
        clearTimeout(timer);
        if (!isErr) timer = setTimeout(function () { status.textContent = ""; }, 1500);
      }
      MARKS.forEach(function (mk) {
        var b = document.createElement("button");
        b.type = "button";
        b.textContent = mk[1];
        b.addEventListener("click", function () {
          var prev = entry.mark;
          entry.mark = mk[0];
          syncMarks();
          post({ run_id: state.run_id, card: entry.num, mark: mk[0] }, function (res) {
            entry.mark = res.mark;
            syncMarks();
            flash("saved");
          }, function (e) {
            entry.mark = prev;
            syncMarks();
            flash("error: " + e.message, true);
          });
        });
        btns.push([mk[0], b]);
        barEl.appendChild(b);
      });

      var noteRow = document.createElement("div");
      noteRow.className = "pulse-note";
      noteRow.hidden = true;
      var input = document.createElement("input");
      input.type = "text";
      input.maxLength = 500;
      input.placeholder = "note";
      var save = document.createElement("button");
      save.type = "button";
      save.textContent = "save";
      save.addEventListener("click", function () {
        post({ run_id: state.run_id, card: entry.num, mark: entry.mark, note: input.value }, function (res) {
          entry.note = res.note;
          syncNote();
          flash("saved");
        }, function (e) {
          flash("error: " + e.message, true);
        });
      });
      noteRow.appendChild(input);
      noteRow.appendChild(save);

      var toggle = document.createElement("button");
      toggle.type = "button";
      toggle.addEventListener("click", function () {
        noteRow.hidden = !noteRow.hidden;
        if (!noteRow.hidden) input.focus();
      });
      function renderNote() {
        toggle.textContent = entry.note ? "note \\u2022" : "note";
        // Don't clobber what the reader is actively typing in this input.
        if (document.activeElement !== input) input.value = entry.note || "";
      }
      barEl.appendChild(toggle);
      barEl.appendChild(status);

      instances.push({ setOn: setOn, renderNote: renderNote });
      setOn(entry.mark);
      renderNote();
      return { bar: barEl, noteRow: noteRow };
    }

    return bar;
  }

  for (var i = 0; i < n; i++) {
    var h2 = h2s[i];
    var makeBar = cardFactory(state.entries[i]);
    // Top bar: immediately after the heading.
    var topW = makeBar();
    h2.insertAdjacentElement("afterend", topW.noteRow);
    h2.insertAdjacentElement("afterend", topW.bar);
    // Bottom bar: at the end of this card's content, i.e. just before
    // the next card's <h2>. The last card has no following <h2>, so its
    // content ends at the close of the h2's parent container (the
    // <main> render_html.py emits, in both the pandoc and markdown-
    // fallback shapes) -- append there, ahead of nothing.
    var botW = makeBar();
    var nextH2 = h2s[i + 1];
    if (nextH2) {
      nextH2.insertAdjacentElement("beforebegin", botW.bar);
      nextH2.insertAdjacentElement("beforebegin", botW.noteRow);
    } else {
      var parent = h2.parentNode;
      parent.appendChild(botW.bar);
      parent.appendChild(botW.noteRow);
    }
  }

  var top = document.createElement("div");
  top.id = "pulse-topbar";
  var back = document.createElement("a");
  back.href = "/";
  back.textContent = "\\u2190 all briefs";
  var label = document.createElement("span");
  label.textContent = state.run_id;
  top.appendChild(back);
  top.appendChild(label);
  document.body.insertBefore(top, document.body.firstChild);
})();
""".strip()


MATHJAX_SRC_RE = re.compile(
    r"<script\b(?=[^>]*\bsrc\s*=\s*(['\"])[^'\"]*"
    r"tex-mml-chtml\.js[^'\"]*\1)[^>]*>\s*</script\s*>",
    re.IGNORECASE,
)


def activate_mathjax(brief_html: str, nonce: str) -> str:
    """Nonce only the fixed renderer-owned MathJax program.

    Never replace the nonce marker globally: a historical or hostile script
    could otherwise copy that marker and inherit the response nonce. Old CDN
    loader tags are removed and upgraded to the same fixed local build, which
    keeps historical math working when served under the new CSP.
    """
    has_current_block = MATHJAX in brief_html
    has_mathjax_loader = bool(MATHJAX_SRC_RE.search(brief_html))
    if not has_current_block and not has_mathjax_loader:
        return brief_html

    brief_html = brief_html.replace(MATHJAX, "")
    brief_html = MATHJAX_SRC_RE.sub("", brief_html)
    trusted_block = MATHJAX.replace(MATHJAX_NONCE_TOKEN, html.escape(nonce, quote=True))
    head_end = re.search(r"</head\s*>", brief_html, re.IGNORECASE)
    if head_end:
        return brief_html[: head_end.start()] + trusted_block + "\n" + brief_html[head_end.start() :]
    return trusted_block + "\n" + brief_html


def inject_widget(brief_html: str, run_id: str, entries: list[dict], nonce: str) -> str:
    """Insert the rating state + widget before </body> (or append)."""
    state = json.dumps({"run_id": run_id, "entries": entries})
    # "</" inside the JSON (e.g. a title containing "</script>") would
    # terminate the script element early; escape it per the HTML spec.
    state = state.replace("</", "<\\/")
    safe_nonce = html.escape(nonce, quote=True)
    brief_html = activate_mathjax(brief_html, nonce)
    block = (
        f'\n<script nonce="{safe_nonce}">window.__pulseState = {state};</script>\n'
        f"<style>\n{WIDGET_CSS}\n</style>\n"
        f'<script nonce="{safe_nonce}">\n{WIDGET_JS}\n</script>\n'
    )
    m = re.search(r"</body\s*>", brief_html, re.IGNORECASE)
    if m:
        return brief_html[: m.start()] + block + brief_html[m.start() :]
    return brief_html + block


def missing_html_page(run_id: str) -> str:
    rid = html.escape(run_id)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{rid} - no HTML render</title>
<style>{INDEX_CSS}</style>
</head>
<body>
<main>
<h1>{rid}</h1>
<p class="empty">The markdown brief exists but its HTML render
(out/{rid}.html) is missing, so there is nothing to rate here.
Re-run the deliver step to regenerate it.</p>
<p><a href="/">&larr; all briefs</a></p>
</main>
</body>
</html>
"""


# --- HTTP handler -------------------------------------------------------

# CSP for pages this module generates itself: no scripts on the index,
# inline styles only. Brief pages get only nonce-bearing renderer/widget
# scripts; model-authored and historical raw script tags stay inert.
INDEX_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'none'"
)


def brief_csp(nonce: str) -> str:
    return (
        f"default-src 'none'; script-src 'nonce-{nonce}'; "
        "style-src 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'none'"
    )


@lru_cache(maxsize=1)
def verified_mathjax_vendor() -> bool:
    """Verify the pinned executable assets once per server process."""
    verify_mathjax_vendor()
    return True


class FeedbackHandler(BaseHTTPRequestHandler):
    server_version = "pi-pulse-feedback"

    @property
    def out_dir(self) -> Path:
        return self.server.out_dir  # type: ignore[attr-defined]

    # -- response helpers --

    def _send(self, status: int, body: bytes, ctype: str, csp: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, text: str, csp: str | None = None) -> None:
        self._send(status, text.encode("utf-8"), "text/html; charset=utf-8", csp)

    def _send_json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

    def _not_found(self) -> None:
        self._send(404, b"not found\n", "text/plain; charset=utf-8")

    def _client_ok(self) -> bool:
        if ip_allowed(self.client_address[0]):
            return True
        self._send(403, b"forbidden\n", "text/plain; charset=utf-8")
        # The request body (if any) is never read on this path; keeping the
        # connection alive would let its bytes corrupt the next request.
        self.close_connection = True
        return False

    # -- routes --

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if not self._client_ok():
            return
        path = urlsplit(self.path).path
        if path == "/":
            self._send_html(200, index_html(discover_runs(self.out_dir)), csp=INDEX_CSP)
        elif path.startswith("/brief/assets/mathjax/"):
            self._serve_mathjax_asset(path[len("/brief/assets/mathjax/") :])
        elif path.startswith("/assets/mathjax/"):
            self._serve_mathjax_asset(path[len("/assets/mathjax/") :])
        elif path.startswith("/brief/"):
            self._serve_brief(path[len("/brief/") :])
        else:
            self._not_found()

    def _serve_mathjax_asset(self, relative: str) -> None:
        """Serve only integrity-checked assets from the pinned vendor tree."""
        if not relative or not re.fullmatch(r"[A-Za-z0-9_./-]+", relative):
            self._not_found()
            return
        try:
            verified_mathjax_vendor()
        except (OSError, RuntimeError):
            self._not_found()
            return
        root = MATHJAX_VENDOR_DIR.resolve()
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            self._not_found()
            return
        if target.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        elif target.suffix == ".woff":
            ctype = "font/woff"
        else:
            self._not_found()
            return
        self._send(200, target.read_bytes(), ctype)

    def _serve_brief(self, run_id: str) -> None:
        # The regex gate is what makes the path constructions below safe:
        # a valid RUN_ID cannot contain a separator or dot-dot segment.
        if not valid_run_id(run_id):
            self._not_found()
            return
        html_path = self.out_dir / f"{run_id}.html"
        md_path = self.out_dir / f"{run_id}.md"
        fb_path = self.out_dir / f"{run_id}.feedback.md"
        if not html_path.exists():
            if md_path.exists():
                self._send_html(200, missing_html_page(run_id), csp=INDEX_CSP)
            else:
                self._not_found()
            return
        entries: list[dict] = []
        if fb_path.exists():
            _, entries = parse_feedback_file(fb_path.read_text(errors="replace"))
        nonce = secrets.token_urlsafe(18)
        page = inject_widget(html_path.read_text(errors="replace"), run_id, entries, nonce)
        self._send_html(200, page, csp=brief_csp(nonce))

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if not self._client_ok():
            return
        if urlsplit(self.path).path != "/api/rate":
            self._not_found()
            return
        origin = self.headers.get("Origin")
        allowed_hosts = self.server.allowed_hosts  # type: ignore[attr-defined]
        if not origin_allowed(origin, allowed_hosts, self.server.server_address[1]):
            self._send_json(403, {"ok": False, "error": "bad origin"})
            self.close_connection = True
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(415, {"ok": False, "error": "expected JSON"})
            self.close_connection = True
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(400, {"ok": False, "error": "missing/invalid Content-Length"})
            return
        if length < 0:
            # A negative Content-Length would reach rfile.read(-1), which
            # reads until EOF -- unbounded memory and a worker thread that
            # blocks until the client closes. Reject before reading.
            self._send_json(400, {"ok": False, "error": "missing/invalid Content-Length"})
            self.close_connection = True
            return
        if length > MAX_BODY_BYTES:
            # Refuse without reading the oversized body.
            self.send_response(413)
            body = json.dumps({"ok": False, "error": "body too large"}).encode("utf-8")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "error": "invalid JSON body"})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"ok": False, "error": "body must be a JSON object"})
            return

        run_id = data.get("run_id")
        if not isinstance(run_id, str) or not valid_run_id(run_id):
            self._not_found()
            return
        fb_path = self.out_dir / f"{run_id}.feedback.md"
        if not fb_path.exists():
            self._not_found()
            return

        mark = data.get("mark")
        if not isinstance(mark, str) or (mark != "" and mark not in VALID_MARKS):
            self._send_json(400, {"ok": False, "error": f"invalid mark: {mark!r}"})
            return
        card = data.get("card")
        if isinstance(card, bool) or not isinstance(card, int):
            self._send_json(400, {"ok": False, "error": "card must be an integer"})
            return

        note: str | None = None
        if "note" in data:
            raw_note = data["note"]
            if not isinstance(raw_note, str):
                self._send_json(400, {"ok": False, "error": "note must be a string"})
                return
            note = clean_note(raw_note)
            if len(note) > MAX_NOTE_CHARS:
                self._send_json(400, {"ok": False, "error": f"note exceeds {MAX_NOTE_CHARS} chars"})
                return

        try:
            entry = set_mark(fb_path, card, mark, note)
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        self._send_json(
            200,
            {
                "ok": True,
                "run_id": run_id,
                "card": entry["num"],
                "mark": entry["mark"],
                "note": entry["note"],
            },
        )


# --- server plumbing ----------------------------------------------------


def make_server(
    host: str, port: int, out_dir: Path, extra_hosts: str | None = None
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), FeedbackHandler)
    server.out_dir = out_dir  # type: ignore[attr-defined]
    if extra_hosts is None:
        extra_hosts = os.environ.get("PI_PULSE_FEEDBACK_ALLOWED_HOSTS")
    server.allowed_hosts = allowed_origin_hosts(host, extra_hosts)  # type: ignore[attr-defined]
    return server


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--host",
        default=os.environ.get("PI_PULSE_FEEDBACK_HOST", DEFAULT_HOST),
        help="bind address, or the keyword 'tailscale' to autodetect the tailnet IPv4",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PI_PULSE_FEEDBACK_PORT", DEFAULT_PORT)),
    )
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = ap.parse_args()

    host = args.host
    extra_hosts = os.environ.get("PI_PULSE_FEEDBACK_ALLOWED_HOSTS", "")
    if host == "tailscale":
        host = resolve_tailscale_ip()
        dns_name = resolve_tailscale_dns_name()
        if dns_name:
            extra_hosts = f"{extra_hosts},{dns_name}" if extra_hosts else dns_name
    out_dir = args.out_dir.resolve()
    if not out_dir.is_dir():
        print(f"ERROR: out dir does not exist: {out_dir}", file=sys.stderr)
        return 1

    server = make_server(host, args.port, out_dir, extra_hosts=extra_hosts or None)
    print(
        f"pi-pulse feedback server: http://{host}:{args.port}/ (out dir: {out_dir})",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
