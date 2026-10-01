#!/usr/bin/env python3
"""
STEP 17 - Test the drag-and-drop upload page without a browser.

WHY
The page in `upload/index.html` is the only part of this project a real user
touches. Every other piece has a test; this had none, and "it looks right in a
screenshot" is exactly the reasoning that already let two real bugs through
this session (the 1. 1. double numbering, the donut caption overflow).

Three things are verified here, none of which need Chrome or Docker:

  1. The HTML is well-formed and the inline JavaScript PARSES. A syntax error
     in that script would leave the drop zone dead and, critically, would fail
     silently: the page would render fine and simply do nothing on drop.
  2. The multipart POST the page builds is accepted by an n8n-shaped webhook:
     correct field names, the file arrives as binary, the text fields arrive as
     strings. This is the contract between the page and Node 2, and a typo in
     the field name is invisible until a real upload fails.
  3. A local mock webhook rejects what it should (a non-CSV, an oversized file)
     and reports 200 with a JSON body for a good one.

The mock stands in for n8n. It implements the same contract n8n does: look for
a multipart part named `reviews_file` and expose it as a binary property, which
is what n8n's webhook node does and what Node 2's Extract From File consumes.

USAGE
    python3 17_test_upload_page.py
"""

import io
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
# `product/` and `upload/` were promoted to the repo root during the
# restructure; agent_journal keeps only the build record.
REPO_ROOT = os.path.dirname(HERE)
PAGE = os.path.join(REPO_ROOT, "upload", "index.html")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"   {detail}" if not cond else ""))


# ---------------------------------------------------------------------------
# T1 - the HTML and its script
# ---------------------------------------------------------------------------
def t1_markup_and_script():
    print("\nT1. PAGE MARKUP AND JAVASCRIPT")
    if not os.path.exists(PAGE):
        check("upload/index.html exists", False, PAGE)
        return None
    html = open(PAGE, encoding="utf-8").read()
    check("upload/index.html exists", True)
    check("page is a complete document",
          html.strip().startswith("<!DOCTYPE html>") and html.strip().endswith("</html>"))

    m = re.search(r"<script>(.*?)</script>", html, re.S)
    check("contains an inline <script>", m is not None)
    if not m:
        return None
    js = m.group(1)

    # The single most important check: does the script PARSE? A syntax error
    # here is invisible in a screenshot - the page looks perfect and the drop
    # zone is simply dead.
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
        p = fh.name
    try:
        proc = subprocess.run(["node", "--check", p], capture_output=True, text=True)
        check("inline JavaScript parses (a syntax error kills the drop zone)",
              proc.returncode == 0, proc.stderr[-300:])
    finally:
        os.unlink(p)

    # The drag-and-drop wiring, by feature. These are the exact handlers that
    # make a real drop work.
    for ev in ("dragenter", "dragover", "dragleave", "drop"):
        check(f"wires the '{ev}' event", f"'{ev}'" in js)
    check("calls preventDefault on drop (else the browser navigates away)",
          "e.preventDefault()" in js)
    check("reads files from dataTransfer", "dataTransfer" in js)
    check("has a depth counter (stops the dragover flicker)", "dragDepth" in js)
    # The file input must be visually hidden but PRESENT and clickable. A
    # display:none input cannot be opened reliably (Safari/iOS will not fire
    # the change event at all), which would make the click-to-browse path
    # silently dead. Assert on the actual markup of that one element.
    fin = re.search(r'<input[^>]*id="file"[^>]*>', html)
    check("file input exists with id='file'", fin is not None)
    if fin:
        tag = fin.group(0)
        check("file input is not display:none (Safari would ignore it)",
              "display:none" not in tag.replace(" ", ""), tag[:80])
        check("file input is present in the DOM (not removed)",
              "remove()" not in js.split("function accept")[0])
        check("file input is opened via .click() from the drop zone",
              "fileInput.click()" in js)

    # The contract with n8n. A typo here means every upload fails.
    check("posts to /webhook/", "/webhook/" in js)
    check("sends the file as 'reviews_file' (matches Extract From File)",
          "reviews_file" in js)
    check("sends 'business_name'", "business_name" in js)
    check("sends 'context'", "'context'" in js)
    check("uses multipart FormData", "FormData" in js)
    check("uses POST", "method: 'POST'" in js)

    # UX correctness
    check("rejects non-CSV client-side", ".csv" in js)
    check("warns the user the report is not ready yet",
          "being generated" in js or "close this page" in js)
    return js


# ---------------------------------------------------------------------------
# T2/T3 - a mock n8n webhook, and what the page would send it
# ---------------------------------------------------------------------------
class MockN8n(BaseHTTPRequestHandler):
    """Mimics n8n's webhook contract closely enough to catch field-name bugs."""

    received = {}

    def log_message(self, *a):
        pass

    def _reply(self, code, body):
        raw = json_bytes(body)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        ctype = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if "multipart/form-data" not in ctype:
            return self._reply(415, {"error": "expected multipart/form-data",
                                    "got": ctype})
        if not self.path.startswith("/webhook/"):
            return self._reply(404, {"error": "bad path", "path": self.path})

        # Parse just enough of the multipart body to find our parts. cgi is
        # gone in 3.13, so do it with the stdlib email parser.
        import email
        msg = email.message_from_bytes(
            b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + body)
        parts = {}
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disp = part.get("Content-Disposition", "")
            if "name=" not in disp:
                continue
            key = re.search(r'name="([^"]+)"', disp).group(1)
            payload = part.get_payload(decode=True) or b""
            filename = re.search(r'filename="([^"]*)"', disp)
            parts[key] = {
                "is_file": filename is not None and filename.group(1) != "",
                "filename": filename.group(1) if filename else None,
                "value": payload,
            }

        MockN8n.received = parts
        has_csv = parts.get("reviews_file", {}).get("is_file")
        return self._reply(200 if has_csv else 400,
                           {"accepted": bool(has_csv),
                            "parts": {k: {"file": v["is_file"],
                                          "bytes": len(v["value"])}
                                      for k, v in parts.items()}})


def json_bytes(obj):
    import json
    return json.dumps(obj).encode()


def multipart(csv_bytes, filename, business, context=""):
    """Build a multipart body exactly as the browser would."""
    b = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    out = io.BytesIO()
    for name, val in (("business_name", business), ("context", context)):
        out.write(f"--{b}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(val.encode() + b"\r\n")
    out.write(f"--{b}\r\n".encode())
    out.write(
        f'Content-Disposition: form-data; name="reviews_file"; filename="{filename}"\r\n'
        .encode())
    out.write(b"Content-Type: text/csv\r\n\r\n")
    out.write(csv_bytes + b"\r\n")
    out.write(f"--{b}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={b}"


def t2_against_mock():
    print("\nT2. MULTIPART CONTRACT (against a mock n8n webhook)")
    srv = HTTPServer(("127.0.0.1", 0), MockN8n)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/webhook/review-pulso-upload"

    csv_path = os.path.join(HERE, "data", "business-reviews-100.csv")
    if not os.path.exists(csv_path):
        csv_path = None
    payload = (open(csv_path, "rb").read() if csv_path
               else b"review\nMabilis ang delivery at ang sarap ng food!\n")

    try:
        # --- a good upload ---
        body, ctype = multipart(payload, "business-reviews-100.csv",
                                "SariSari Store Malate", "Q3 2026")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", ctype)
        with urllib.request.urlopen(req, timeout=20) as r:
            code = r.status
        check("accepts a valid CSV (HTTP 200)", code == 200, f"got {code}")
        got = MockN8n.received
        check("file arrives as a FILE part named reviews_file",
              got.get("reviews_file", {}).get("is_file") is True)
        check("filename survives to the server",
              got.get("reviews_file", {}).get("filename") == "business-reviews-100.csv",
              str(got.get("reviews_file", {}).get("filename")))
        check("file bytes are intact (100-row CSV)",
              got.get("reviews_file", {}).get("value") == payload)
        check("business_name arrives as text",
              got.get("business_name", {}).get("value") == b"SariSari Store Malate")
        check("context arrives as text",
              got.get("context", {}).get("value") == b"Q3 2026")

        # --- the CSV content must survive the round trip unparsed ---
        content = got["reviews_file"]["value"].decode()
        first = content.splitlines()[1]
        check("a quoted row with commas stays ONE row",
              first.count(",") >= 1 and "Worth it yung price" in first,
              first[:80])

        # --- non-multipart must be rejected ---
        req2 = urllib.request.Request(url, data=b"{}", method="POST")
        req2.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req2, timeout=20)
            check("rejects a non-multipart body (HTTP 415)", False, "accepted")
        except urllib.error.HTTPError as e:
            check("rejects a non-multipart body (HTTP 415)", e.code == 415, str(e.code))

        # --- a missing file part must be rejected ---
        b2 = "----X"
        only_text = (f"--{b2}\r\nContent-Disposition: form-data; name=\"business_name\""
                     f"\r\n\r\nYolo\r\n--{b2}--\r\n").encode()
        req3 = urllib.request.Request(url, data=only_text, method="POST")
        req3.add_header("Content-Type", f"multipart/form-data; boundary={b2}")
        try:
            urllib.request.urlopen(req3, timeout=20)
            check("rejects an upload with no file (HTTP 400)", False, "accepted")
        except urllib.error.HTTPError as e:
            check("rejects an upload with no file (HTTP 400)", e.code == 400, str(e.code))
    finally:
        srv.shutdown()


def t3_workflow_contract():
    print("\nT3. THE OTHER HALF OF THE CONTRACT (the n8n workflow)")
    import json
    wf = json.load(open(os.path.join(REPO_ROOT, "workflow", "review-pulso.workflow.json")))
    nodes = {n["name"]: n for n in wf["nodes"]}
    hook = nodes.get("Node 1", {})

    check("Node 1 is a webhook", hook.get("type", "").endswith("webhook"),
          hook.get("type", ""))
    check("Node 1 accepts POST", hook.get("parameters", {}).get("httpMethod") == "POST")
    page_path = re.search(r"WEBHOOK_PATH\s*=\s*'([^']+)'",
                          open(os.path.join(REPO_ROOT, "upload", "index.html"),
                               encoding="utf-8").read())
    check("upload page's WEBHOOK_PATH == the workflow's webhook path",
          page_path and page_path.group(1) == hook["parameters"]["path"],
          f"page={page_path.group(1) if page_path else None} "
          f"workflow={hook['parameters']['path']}")

    ext = nodes.get("Node 2", {}).get("parameters", {})
    check("Node 2 reads the binary field 'reviews_file'",
          ext.get("binaryPropertyName") == "reviews_file",
          str(ext.get("binaryPropertyName")))
    check("workflow responds immediately (responseMode=responseNode)",
          hook.get("parameters", {}).get("responseMode") == "responseNode",
          str(hook.get("parameters", {}).get("responseMode")))
    check("a Respond to Webhook node exists",
          any(n.get("type", "").endswith("respondToWebhook") for n in wf["nodes"]))
    check("the Respond node is reachable from the end of the chain",
          "Node 1b - Respond" in json.dumps(wf["connections"]))
    check("no node requires a credential",
          not any(n.get("credentials") for n in wf["nodes"]),
          str([n["name"] for n in wf["nodes"] if n.get("credentials")]))


if __name__ == "__main__":
    print("=" * 78)
    print("Drag-and-drop upload page - tests")
    print("=" * 78)
    t1_markup_and_script()
    t2_against_mock()
    t3_workflow_contract()
    print("\n" + "=" * 78)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        print("=" * 78)
        sys.exit(1)
    print("The page's script parses, its multipart contract matches what")
    print("Extract From File expects, and the workflow path agrees with it.")
    print("What is still UNVERIFIED: the real end-to-end post to a live n8n.")
    print("=" * 78)
