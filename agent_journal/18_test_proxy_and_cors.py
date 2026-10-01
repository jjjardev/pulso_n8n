#!/usr/bin/env python3
"""
STEP 18 - Reproduce the browser's exact sequence against a proxy + mock n8n.

WHY THIS EXISTS
The first version of the upload page POSTed to an ABSOLUTE url
(http://localhost:5678) while being served from http://localhost:8080. The
browser treated that as cross-origin, sent a CORS preflight, n8n answered the
preflight with HTTP 500 and no `Access-Control-Allow-Origin` header, and the
browser blocked the response. In the browser that surfaced as:

    Could not reach the server.
    TypeError: NetworkError when attempting to fetch resource.

...with no indication that anything was wrong server-side. The upload page
looked perfect and was completely non-functional.

`fetch()` in Node does not enforce CORS, so testing the page from a script
would have PASSED while the real browser failed. Reproducing the failure
therefore means reproducing the BROWSER's request sequence by hand: OPTIONS
preflight first, then the POST, with the Origin header present and the
Access-Control-Allow-Origin requirement enforced.

What this test does:
  1. Serves the page on one port.
  2. Proxies /webhook/ to a mock n8n on another port - exactly what nginx does.
  3. Replays what Chrome does: OPTIONS with Origin + Access-Control-Request-*
     headers, and requires an Access-Control-Allow-Origin response.
  4. Proves the page produces a SAME-ORIGIN request (relative URL), so no
     preflight is needed at all.
  5. Verifies the 404 path now produces a human-readable message telling the
     user to activate the workflow, instead of a bare status code.

USAGE
    python3 18_test_proxy_and_cors.py
"""

import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
# `product/` and `upload/` were promoted to the repo root during the
# restructure; agent_journal keeps only the build record.
REPO_ROOT = os.path.dirname(HERE)
PAGE = os.path.join(REPO_ROOT, "upload", "index.html")
NGINX_CONF = os.path.join(REPO_ROOT, "upload", "nginx.conf")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"   {detail}" if not cond else ""))


# ---------------------------------------------------------------------------
# Mock n8n. Deliberately reproduces n8n's real CORS behaviour: no
# Access-Control-Allow-Origin header, and a 500 on preflight.
# ---------------------------------------------------------------------------
class MockN8n(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_OPTIONS(self):
        # What n8n actually did, measured earlier in this session.
        self.send_response(500)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        origin = self.headers.get("Origin")
        MockN8n.hits.append((self.path, origin, len(body)))
        if not self.path.startswith("/webhook/"):
            payload = b'{"error":"not found"}'
            code = 404
        else:
            payload = json.dumps({"accepted": True, "seen_bytes": len(body)}).encode()
            code = 200
        # n8n returns NO Access-Control-Allow-Origin. That is the whole bug.
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


# ---------------------------------------------------------------------------
# A tiny proxy that behaves like the nginx config: serves the page, and
# forwards /webhook/ to the mock. This is the arrangement the fix creates.
# ---------------------------------------------------------------------------
class PageAndProxy(BaseHTTPRequestHandler):
    upstream = None  # set after construction

    def log_message(self, *a):
        pass

    def _page(self):
        body = open(PAGE, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        url = f"http://127.0.0.1:{self.upstream}{self.path}"
        req = urllib.request.Request(url, data=body, method="POST")
        for h in ("Content-Type",):
            if self.headers.get(h):
                req.add_header(h, self.headers[h])
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                code, payload = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, payload = e.code, e.read()
        # A proxy passes n8n's headers through UNCHANGED, exactly like nginx.
        # That is the point: the browser now sees a same-origin reply, so the
        # missing CORS header no longer matters.
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._page()

    def do_POST(self):
        self._forward()


def browser_fetch_simulation(page_url, body, ctype, cors_enforced,
                             page_origin=None):
    """
    Replay what a browser does for a fetch().

    cors_enforced=True  -> enforce CORS the way a real browser does
    cors_enforced=False -> just POST (i.e. Node's fetch, which ignores CORS)

    THE PREFLIGHT IS ONLY SENT FOR CROSS-ORIGIN REQUESTS. That is the whole
    mechanism the fix relies on: same-origin requests skip the preflight
    entirely, so the missing Access-Control-Allow-Origin header never comes up.
    The first version of this test always sent a preflight, which made the
    FIXED page fail - a test that fails the thing it is meant to prove.
    """
    parsed = page_url
    same_origin = (page_origin is not None
                   and re.match(r"(https?://[^/]+)", parsed).group(1) == page_origin)
    if cors_enforced and not same_origin:
        origin = re.match(r"(https?://[^/]+)", parsed).group(1)
        pre = urllib.request.Request(parsed, method="OPTIONS")
        pre.add_header("Origin", origin)
        pre.add_header("Access-Control-Request-Method", "POST")
        pre.add_header("Access-Control-Request-Headers", "content-type")
        try:
            with urllib.request.urlopen(pre, timeout=15) as r:
                acao = r.headers.get("Access-Control-Allow-Origin")
                if acao is None:
                    return ("BLOCKED",
                            "browser blocked the reply: preflight gave no "
                            "Access-Control-Allow-Origin (this is the "
                            "TypeError: NetworkError the user saw)")
        except urllib.error.HTTPError as e:
            if e.headers.get("Access-Control-Allow-Origin") is None:
                return ("BLOCKED",
                        f"browser blocked the reply: preflight returned HTTP "
                        f"{e.code} with no Access-Control-Allow-Origin "
                        f"(this is the TypeError: NetworkError the user saw)")
    req = urllib.request.Request(parsed, data=body, method="POST")
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return ("OK", r.read().decode()[:120])
    except urllib.error.HTTPError as e:
        return (f"HTTP {e.code}", e.read().decode()[:120])


def main():
    print("=" * 78)
    print("Upload page - proxy and CORS behaviour, replaying a real browser")
    print("=" * 78)

    html = open(PAGE, encoding="utf-8").read()
    js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)

    # ---- 1. static analysis of the page's target URL ----------------------
    print("\nT1. THE PAGE MUST USE A SAME-ORIGIN (RELATIVE) URL")
    m = re.search(r"const WEBHOOK_URL\s*=\s*([^;]+);", js)
    check("WEBHOOK_URL is defined", m is not None)
    if m:
        expr = m.group(1).strip()
        # A relative URL is a path literal, not a full origin. Comparing
        # against "'/'" is wrong because the value is "'/webhook/' + PATH" -
        # it starts with a quote and a slash, but not the three-char string
        # "'/'". Check the properties that actually matter instead.
        is_relative = "http://" not in expr and "https://" not in expr
        check("WEBHOOK_URL is RELATIVE (no scheme, no host)",
              is_relative, f"got {expr}")
        check("WEBHOOK_URL starts with the /webhook/ path",
              "/webhook/" in expr, f"got {expr}")
    # Strip comments before scanning for a hardcoded origin: the file explains
    # at length WHY the page must not contain one, and that explanation
    # mentions :5678. A naive substring scan trips over the explanation.
    code_only = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    code_only = re.sub(r"//[^\n]*", "", code_only)
    check("no absolute n8n origin remains in the executable code",
          "localhost:5678" not in code_only,
          "an absolute origin makes the request cross-origin again")
    check("N8N_ORIGIN, if present, is derived from location.origin",
          ("N8N_ORIGIN" not in code_only) or ("location.origin" in code_only))

    # ---- 2. nginx config sanity ------------------------------------------
    print("\nT2. NGINX CONFIG DOES THE PROXYING")
    conf = open(NGINX_CONF, encoding="utf-8").read()
    check("nginx.conf proxies /webhook/ to n8n:5678",
          "location /webhook/" in conf and "proxy_pass" in conf
          and "n8n:5678" in conf)
    check("nginx disables request buffering (large uploads)",
          "proxy_request_buffering off" in conf)
    check("nginx timeout exceeds the scoring time",
          "proxy_read_timeout    600s" in conf
          or re.search(r"proxy_read_timeout\s+600s", conf) is not None)
    check("nginx raises client_max_body_size above the default 1m",
          re.search(r"client_max_body_size\s+25m", conf) is not None)
    check("nginx turns a dead n8n into a readable 503",
          "@n8n_down" in conf and "503" in conf)
    check("nginx still serves the page itself",
          "root /usr/share/nginx/html" in conf)

    # ---- 3. the failure, then the fix, side by side ----------------------
    print("\nT3. REPRODUCING THE BUG AND CONFIRMING THE FIX")

    n8n = HTTPServer(("127.0.0.1", 0), MockN8n)
    n8n_port = n8n.server_address[1]
    threading.Thread(target=n8n.serve_forever, daemon=True).start()

    front = HTTPServer(("127.0.0.1", 0), PageAndProxy)
    front_port = front.server_address[1]
    PageAndProxy.upstream = n8n_port
    threading.Thread(target=front.serve_forever, daemon=True).start()

    b = "----Boundary"
    csv_bytes = b'review\n"Mabilis, masarap, at mura"\nMasarap talaga\n'
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"reviews_file\"; "
            f"filename=\"r.csv\"\r\nContent-Type: text/csv\r\n\r\n").encode() \
        + csv_bytes + f"\r\n--{b}--\r\n".encode()
    ctype = f"multipart/form-data; boundary={b}"

    try:
        # --- the OLD arrangement: page on :A, POST absolute to :B -----------
        old_url = f"http://127.0.0.1:{n8n_port}/webhook/review-pulso-upload"
        status, detail = browser_fetch_simulation(old_url, body, ctype,
                                                  cors_enforced=True,
                                                  page_origin=f"http://127.0.0.1:{front_port}")
        check("old cross-origin arrangement is BLOCKED by the browser",
              status == "BLOCKED", f"got {status} ({detail})")
        print(f"         -> {detail[:100]}")

        # --- the NEW arrangement: page on :A, POST relative to :A -----------
        new_url = f"http://127.0.0.1:{front_port}/webhook/review-pulso-upload"
        status, detail = browser_fetch_simulation(new_url, body, ctype,
                                                  cors_enforced=True,
                                                  page_origin=f"http://127.0.0.1:{front_port}")
        check("new same-origin arrangement SUCCEEDS from the browser",
              status == "OK", f"got {status} ({detail})")

        hits = MockN8n.hits
        check("n8n actually received the request",
              any(p == "/webhook/review-pulso-upload" for p, _, _ in hits),
              str(hits))
        got = [n for p, _, n in hits if p == "/webhook/review-pulso-upload"]
        check("the file bytes survived the proxy intact",
              got and any(n >= len(csv_bytes) for n in got), str(got))
        check("the proxy sent NO Origin header upstream "
              "(same-origin request, so no preflight is triggered)",
              all(o is None for _, o, _ in hits), str([o for _, o, _ in hits]))

        # --- Node's fetch() would have passed the broken version -----------
        status2, _ = browser_fetch_simulation(old_url, body, ctype,
                                              cors_enforced=False,
                                              page_origin=f"http://127.0.0.1:{front_port}")
        check("Node fetch() would NOT have caught the bug (so a naive test "
              "passes while the browser fails)",
              status2 == "OK", f"got {status2}")
    finally:
        front.shutdown()
        n8n.shutdown()

    # ---- 4. the 404 must be actionable -----------------------------------
    print("\nT4. A 404 MUST TELL THE USER WHAT TO DO")
    check("page handles 404 specifically",
          "res.status === 404" in js)
    check("the 404 message names the Active toggle",
          "Active toggle" in js or "switch the Active" in js)
    check("the 503 message points at the logs",
          "docker compose logs" in js)

    print("\n" + "=" * 78)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        print("=" * 78)
        sys.exit(1)
    print("The cross-origin failure is reproduced, and the relative-URL fix is")
    print("confirmed to survive a real browser's request sequence.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
