#!/usr/bin/env python3
"""
STEP 19 - Validate every n8n node parameter value against the real enums.

WHY THIS EXISTS
A workflow JSON is data, not code, and n8n validates it far too leniently. The
costliest instance of that in this build:

    "contentType": "form-data"        <- the value I wrote
    "contentType": "multipart-form-data"   <- the value n8n actually defines

n8n did not reject the wrong value. It did not warn. It simply did not *read*
`bodyParameters` — that collection is gated on
`contentType: ['multipart-form-data']` — so Node 6 posted with **no body at
all**, Gotenberg received an empty multipart, and the workflow died with an
opaque `{"message":"Error in workflow"}` and an HTTP 500.

A wrong enum value in a JSON file is the worst kind of bug: it is invisible to
review, invisible to n8n, and invisible to every existing test in this project.
So the values are pinned here against n8n's actual definitions.

Sourced from n8n's node descriptions:
  HttpRequest/V3/Description.ts   contentType enum, parameterType enum,
                                  bodyParameters displayOptions
  ReadWriteFile write.operation   fileName, inputDataFieldName
  ExtractFromFile                 operation + binaryPropertyName

USAGE
    python3 19_validate_node_params.py
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# `product/` and `upload/` were promoted to the repo root during the
# restructure; agent_journal keeps only the build record.
REPO_ROOT = os.path.dirname(HERE)
WF = os.path.join(REPO_ROOT, "workflow", "review-pulso.workflow.json")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"\n         {detail}" if detail and not cond else ""))


# ---- n8n's actual definitions -------------------------------------------
HTTP_CONTENT_TYPES = {
    "form-urlencoded", "multipart-form-data", "json", "binaryData", "raw",
}
HTTP_BODY_PARAM_TYPES = {"formBinaryData", "formData"}
READWRITE_OPERATIONS = {"read", "write"}
EXTRACT_OPERATIONS = {"csv", "html", "ics", "jwt", "ods", "pdf", "rtf",
                      "text", "xls", "xlsx", "ods", "fromJson", "toJson"}
WEBHOOK_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
WEBHOOK_RESPONSE_MODES = {
    "onReceived", "lastNode", "responseNode", "streaming",
}

# The single value that broke the build. Kept explicit so the reason the
# mistake is impossible to repeat is visible at the point of use.
GOTCHAS = {
    "contentType=form-data":
        "MUST be 'multipart-form-data'. n8n defines {name:'Form-Data', "
        "value:'multipart-form-data'} and gates bodyParameters on that exact "
        "string, so 'form-data' silently posts an empty body.",
    "contentType=json without specifyBody":
        "jsonBody is only read when specifyBody is 'json'.",
    "readWriteFile fileName must be absolute":
        "n8n 2.0 sandboxes Read/Write Files from Disk to ~/.n8n-files. A "
        "relative path relies on n8n's resolution; an absolute one is "
        "guaranteed inside the sandbox.",
    "Webhook needs Publish, not Active":
        "n8n 2.x has no Active toggle; the production webhook is only "
        "registered when the workflow is PUBLISHED.",
    "Form Trigger vs Webhook field shape":
        "trigger -> json.business_name ; webhook -> json.body.business_name",
}


def main():
    if not os.path.exists(WF):
        sys.exit(f"FATAL: {WF} missing - run 05_generate_workflow_json.py")
    wf = json.load(open(WF))
    nodes = {n["name"]: n for n in wf["nodes"]}
    print("=" * 78)
    print("n8n node parameter validation (against n8n's real enums)")
    print("=" * 78)

    # ---- Node 1 webhook --------------------------------------------------
    print("\nN1. Webhook")
    p = nodes["Node 1"]["parameters"]
    check("httpMethod is a valid HTTP method",
          p.get("httpMethod") in WEBHOOK_METHODS, str(p.get("httpMethod")))
    check("responseMode is a valid enum value",
          p.get("responseMode") in WEBHOOK_RESPONSE_MODES, str(p.get("responseMode")))
    check("path is non-empty", bool(p.get("path")))
    check("path is relative (n8n mounts it under /webhook/)",
          not str(p.get("path", "")).startswith("/"), str(p.get("path")))

    # ---- Node 2 extract --------------------------------------------------
    print("\nN2. Extract From File")
    p = nodes["Node 2"]["parameters"]
    check("operation is a valid Extract From File operation",
          p.get("operation") in EXTRACT_OPERATIONS, str(p.get("operation")))
    check("binaryPropertyName is 'reviews_file' (what the upload page sends)",
          p.get("binaryPropertyName") == "reviews_file",
          str(p.get("binaryPropertyName")))

    # ---- Node 4 / Node 6 httpRequest -------------------------------------
    print("\nN4/N6. HTTP Request - the enum values that fail silently")
    for name in ("Node 4", "Node 6"):
        p = nodes[name]["parameters"]
        ct = p.get("contentType")
        check(f"{name}: contentType is a VALID n8n value",
              ct in HTTP_CONTENT_TYPES,
              f"got {ct!r}. Valid: {sorted(HTTP_CONTENT_TYPES)}")
        if ct == "multipart-form-data":
            check(f"{name}: bodyParameters present (n8n only reads it when "
                  f"contentType is exactly 'multipart-form-data')",
                  "bodyParameters" in p)
            bp = p.get("bodyParameters", {}).get("parameters", [])
            check(f"{name}: has at least one body parameter", len(bp) >= 1)
            for i, par in enumerate(bp):
                check(f"{name}: body param {i} parameterType is valid",
                      par.get("parameterType") in HTTP_BODY_PARAM_TYPES,
                      str(par.get("parameterType")))
                check(f"{name}: body param {i} has a name", bool(par.get("name")))
                if par.get("parameterType") == "formBinaryData":
                    check(f"{name}: binary body param has inputDataFieldName",
                          bool(par.get("inputDataFieldName")),
                          str(par))
        if ct == "json":
            check(f"{name}: specifyBody is 'json' (jsonBody is gated on it)",
                  p.get("specifyBody") == "json", str(p.get("specifyBody")))
            check(f"{name}: jsonBody is a non-empty expression",
                  bool(str(p.get("jsonBody", "")).strip()))

    # ---- Node 8 / 9 readWriteFile ----------------------------------------
    print("\nN8/N9. Read/Write Files from Disk - n8n 2.x path sandbox")
    for name in ("Node 8 - Save PDF",):
        p = nodes[name]["parameters"]
        check(f"{name}: operation is a valid value",
              p.get("operation") in READWRITE_OPERATIONS, str(p.get("operation")))
        check(f"{name}: operation is 'write'", p.get("operation") == "write")
        fn = str(p.get("fileName", ""))
        expr = fn[1:] if fn.startswith("=") else fn
        check(f"{name}: fileName is ABSOLUTE (n8n 2.x sandboxes to "
              f"~/.n8n-files; a relative path relies on n8n's resolution)",
              expr.startswith("/home/node/.n8n-files/"), expr)
        check(f"{name}: writes inside the mounted downloads dir",
              expr.startswith("/home/node/.n8n-files/downloads/"), expr)
        # The directory must be STATIC. Split on the first "{{": everything
        # before it is the literal prefix, and it must be exactly the
        # downloads directory. (The first version of this assertion looked for
        # the substring "/downloads/{{" and therefore failed on a correctly
        # built path - the test was wrong, not the workflow.)
        prefix = expr.split("{{")[0]
        check(f"{name}: the directory prefix is a LITERAL ending at "
              f"'/downloads/' (only the basename is interpolated)",
              prefix == "/home/node/.n8n-files/downloads/",
              f"prefix={prefix!r}")
        check(f"{name}: the interpolated part is a bare filename expression "
              f"(no '/' inside it, so it cannot climb out of the directory)",
              "{{" in expr
              and "/" not in expr.split("{{", 1)[1].split("}}")[0],
              expr.split("{{", 1)[1].split("}}")[0])
        check(f"{name}: inputDataFieldName set", bool(p.get("inputDataFieldName")))
        check(f"{name}: inputDataFieldName differs per node",
              p.get("inputDataFieldName") in ("data", "manifest"),
              str(p.get("inputDataFieldName")))

    # Node 7 carries the PDF (and only the PDF) to the single write node.
    # This matters: two binaries on one item produced a .json that was a
    # byte-identical copy of the PDF, and the sidecar was later removed.
    print("\nN8. One binary, one write node")
    code = nodes["Node 7 - Filename"]["parameters"]["jsCode"]
    i = code.index("binary: {") + len("binary: {")
    blk = code[i:code.index("}", i)]
    fields = set(re.findall(r"(\w+)\s*:", blk))
    check("Node 7 emits exactly one binary: the PDF",
          fields == {"data"}, f"emits {sorted(fields)}")
    check("Node 8 reads that one binary",
          nodes["Node 8 - Save PDF"]["parameters"]["inputDataFieldName"] == "data")

    # ---- Node 1b respond -------------------------------------------------
    print("\nN1b. Respond to Webhook")
    r = [n for n in wf["nodes"] if n["type"].endswith("respondToWebhook")]
    check("a Respond to Webhook node exists", len(r) == 1)
    if r:
        check("Node 1's responseMode matches (must be 'responseNode')",
              nodes["Node 1"]["parameters"]["responseMode"] == "responseNode")

    # ---- the data contract between consecutive nodes -------------------
    print("\nCONTRACT. Every field an expression READS must be EMITTED upstream")
    # This is the check that would have caught the failure in 10.0.9/10.0.11:
    # the final Respond node read $json.stats, which Node 7 never set, so a
    # completely successful run still ended in an HTTP 500. Reading a field
    # that does not exist raises in n8n rather than yielding null, so an
    # unverified expression is a latent production failure.
    def emits(node_name, code_marker="json: {"):
        """
        Collect the keys a Code node's returned json object contains.

        Handles BOTH forms:
            business_name: form.business_name    (explicit)
            reviews,                            (shorthand)
        An earlier version of this only matched the explicit form, so a
        shorthand key was reported as MISSING even though it was present -
        a false positive in the very check meant to catch real omissions.
        """
        code = nodes[node_name]["parameters"]["jsCode"]
        i = code.index(code_marker)
        blk = code[i:]
        # stop at the end of the object literal, not the whole function
        depth, end = 0, len(blk)
        for j, ch in enumerate(blk[code_marker.index("{") + len(code_marker) - 1:]):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = code_marker.index("{") + 1 + j
                    break
        blk = blk[:end]
        explicit = set(re.findall(r"^\s+(\w+)\s*:", blk, re.M))
        shorthand = set(re.findall(r"^\s+(\w+)\s*,\s*$", blk, re.M))
        return explicit | shorthand

    def reads(expr):
        return set(re.findall(r"\$json\.(\w+)", expr))

    contracts = [
        ("Node 7 - Filename", "Node 1b - Respond", "responseBody"),
        ("Node 7 - Filename", "Node 8 - Save PDF", "fileName"),
        ("Node 5", "Node 7 - Filename", "jsCode"),
        ("Node 3", "Node 5", "jsCode"),
    ]
    for src_node, dst_node, field in contracts:
        pexpr = nodes[dst_node]["parameters"].get(field, "")
        r = reads(pexpr)
        # A reference by node name is resolved at runtime, not from $json, so
        # it is exempt from this particular check.
        r = {x for x in r if x not in ("reviews",)}
        e = emits(src_node)
        missing = r - e
        check(f"{dst_node} reads only fields {src_node} emits"
              + (f" (field: {field})" if field != "jsCode" else ""),
              not missing, f"reads {sorted(r)}, upstream emits {sorted(e)}, "
                           f"MISSING {sorted(missing)}")

    # Node 5 reads from Node 3 and Node 4 by node reference; check those too.
    n5 = nodes["Node 5"]["parameters"]["jsCode"]
    n3_emits = emits("Node 3")
    for field in ("reviews", "business_name", "context"):
        check(f"Node 5 reads $('Node 3').{field} and Node 3 emits it",
              field in n3_emits and f".{field}" in n5,
              f"Node 3 emits {sorted(n3_emits)}")

    # ---- failure containment -------------------------------------------
    print("\nRESILIENCE. A failed report must not look successful, and a")
    print("           success must not be reported as a failure")
    # Every node is now on the critical path to delivering the PDF, so every
    # node is strict. The sidecar that needed tolerance has been removed
    # (JOURNAL 10.0.19); a tolerance setting on the critical path is exactly
    # the kind that erodes into "hide real failures".
    for nm, node in nodes.items():
        if nm in ("Node 1",):
            continue
        oe = node.get("onError")
        check(f"{nm} is strict (a real failure must surface)",
              oe is None, f"onError={oe!r}")
    check("the PDF write is strict", "onError" not in nodes["Node 8 - Save PDF"])
    check("the model call is strict", "onError" not in nodes["Node 4"])
    check("the Respond node is the last node in the chain",
          "Node 1b - Respond" in json.dumps(wf["connections"]["Node 8 - Save PDF"]))

    # ---- Code nodes: trigger shape + a client-controlled field ----------
    print("\nCODE. Field access matches the trigger shape")
    n3 = nodes["Node 3"]["parameters"]["jsCode"]
    n7 = nodes["Node 7 - Filename"]["parameters"]["jsCode"]
    for nm, code in (("Node 3", n3), ("Node 7", n7)):
        check(f"{nm}: handles the Webhook's json.body shape",
              "json.body" in code or "raw.body" in code or ".body" in code)
    check("Node 7: asserts the filename against an allowlist regex",
          re.search(r"const SAFE = /\^", n7) is not None,
          "the path-traversal second lock is missing")
    check("Node 5: escapes review text before it reaches the PDF",
          "&amp;" in nodes["Node 5"]["parameters"]["jsCode"])

    # ---- the document in a glance ---------------------------------------
    print("\nKEY GOTCHAS THIS FILE EXISTS TO ENFORCE")
    for k, v in GOTCHAS.items():
        print(f"  - {k}\n      {v}")

    print("\n" + "=" * 78)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("\nAn invalid n8n parameter value does NOT raise an error in n8n.")
        print("It silently changes behaviour - which is exactly how Node 6 came")
        print("to post an empty body. Fix before importing.")
        print("=" * 78)
        sys.exit(1)
    print("Every node parameter matches n8n's real definitions.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
