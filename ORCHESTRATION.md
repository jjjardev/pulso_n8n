# ORCHESTRATION.md

How this pipeline is wired, why each choice was made, and the n8n behaviours that
cost the most time. Written to be useful to someone building their own n8n
pipeline, not just this one.

The chronological record — every measurement, dead end, and wrong turn in build
order — is in [`agent_journal/JOURNAL.md`](agent_journal/JOURNAL.md). This
document is the distilled version: the decisions that would change if you built
it again.

---

## 1. The shape of the pipeline

Eleven nodes: a linear chain, plus one validation branch that answers with a
real error instead of dying.

| # | Node | Type | Consumes | Produces |
|---|---|---|---|---|
| 1 | `Node 1` | `webhook` | multipart POST | binary + `business_name`, `context` |
| 2 | `Node 2` | `extractFromFile` | binary CSV | items, one per row |
| 3 | `Node 3` | `code` (runOnceForAllItems) | items | batches, **or a rejection** |
| 3b | `Node 3b - Input valid?` | `if` | rejection? | routes true→3c, false→4 |
| 3c | `Node 3c - Respond bad input` | `respondToWebhook` | rejection | **HTTP 400 + the reason** |
| 4 | `Node 4` | `httpRequest` | payloads | `{results:[{label,score}]}` |
| 5 | `Node 5` | `code` | results | stats + rule-based insights + HTML |
| 6 | `Node 6` | `httpRequest` | HTML | PDF binary |
| 7 | `Node 7 - Filename` | `code` | PDF binary | safe filename |
| 8 | `Node 8 - Save PDF` | `readWriteFile` | binary | written file |
| 9 | `Node 1b - Respond` | `respondToWebhook` | stats | JSON response |

### The contract that matters most

n8n's data model is items-and-JSON, with binary as a separate side-channel. The
single most common cause of a silently empty report is a node that produces
valid JSON but loses the binary it was supposed to carry.

```
Node 6 (Gotenberg) returns the PDF as binary on item 0.
        │
        ├──► Node 7 must PRESERVE $input binary while ADDING pdf_filename.
        │    A Code node that returns a fresh object drops the binary.
        │    The fix is to read from $binary explicitly and re-emit it.
        │
        ├──► Node 8 (readWriteFile) consumes the binary, in BINARY mode.
        │
        └──► Node 1b responds with JSON only. It does NOT return the PDF.
             The PDF goes to disk; the response is statistics.
```

`test 06` (46 assertions) and `test 12` (46 security assertions) exist to catch
regressions in exactly this contract.

---

## 2. Throughput, and why the cap is 1000

Four separate measurements, all re-run for this document. **They do not agree,
and the disagreement is the interesting part** - quoting one number without the
others would be misleading.

| Measurement | Rate |
|---|---|
| `01` - direct ONNX, realistic short reviews | **39.0 rev/s** |
| `02` - via `run_many()`, same short reviews | **12.4 rev/s** |
| `02` - via `run_many()`, ~112-token reviews | **1.5 rev/s** *(3.1 rev/s unloaded)* |
| `04` - direct ONNX, every row padded to 128 tokens | **2.7 rev/s** *(threads=4)* |

Two things to be honest about:

- **`01` and `02` differ by 3x on identical input.** Same five texts, same
  model, same machine: 39.0 rev/s calling the session directly versus 12.4 rev/s
  through the service's `run_many()`. This is unexplained and worth chasing - it
  suggests `run_many()` costs something per batch that direct inference does not.
  It affects neither correctness nor the timeouts below.
- **These numbers are load-dependent.** The `02` long-review figure was 3.1 rev/s
  when the build ran and 1.5 rev/s when this document was written, on a machine at
  load average 4.72 across 6 cores. Same code. **Benchmark on an idle box or the
  numbers are not comparable.**

### Batch size and thread count

Batch size was chosen by measurement (`01`):

| Batch | Rate |
|---|---|
| **8** | **39.0 rev/s** |
| 16 | 38.9 rev/s |
| 32 | 36.0 rev/s |
| 64 | 32.6 rev/s |

Larger batches are *slower*. That is expected for a 24-layer, 1024-hidden
transformer on CPU: wide padded batches thrash cache. The batch size is measured,
not assumed.

Thread count matters more, and the library default is actively harmful (`04`):

| Threads | Rate |
|---|---|
| 2 | 2.4 rev/s |
| **4** | **2.7 rev/s** |
| 6 | 2.6 rev/s |
| 8 | 1.5 rev/s |
| 12 | 1.0 rev/s |

onnxruntime defaults to one thread per logical core. On a 6-core box that is
oversubscription, and it is roughly **2.7x slower** than four threads. The service
sets `intra_op_num_threads=4` explicitly.

### Why the cap is 1000

The cap is derived from the worst case, not the average. `02` sizes it against
**Node 4's 600-second timeout**, using ~112-token reviews (a realistic upper
bound for a detailed complaint):

| Rows @ ~112 tokens | Unloaded | Fits 600s? |
|---|---|---|
| 500 | 163 s | yes |
| **1000** | **326 s** | **yes** |
| 2000 | 653 s | **no** |

So the cap of 1000 sits inside the timeout with ~1.8x margin, and 2000 - which
the original specification proposed - would exceed it. That measurement is what
corrected the spec's 120 s timeout / 2000 cap pairing.

**Known limit:** that margin is measured, not guaranteed. Under CPU contention the
same 1000 long reviews took 655 s and *would* have timed out. The cap is sized
for an unloaded machine. Two independent guards sit behind it:

1. **Node 3 rejects** uploads over 1000 rows, and deduplicates first, so repeated
   rows do not count against the cap.
2. **The service caps** at 2000 (`MAX_BATCH_REVIEWS`) for direct callers who
   bypass the workflow.

Node 4 has a 600 s timeout; the workflow itself has 900 s. Measured reality is
**4.6-13.7 s** end to end for a 100-review report, and 52 s for the 1000-row cap.
All measured, not projected.

### Why single-worker is deliberate

```
tagasenti/Dockerfile:
  CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

The ONNX session holds ~600 MB of int8 weights and is loaded **per worker**.
Four workers would hold ~2.4 GB in one container. On the 7.6 GB build machine,
that plus Gotenberg plus n8n was enough to hit an OOM kill — which did happen,
during a full test run.

The consequence, stated plainly: **the inference service handles one request at
a time.** Concurrent uploads queue with no backpressure. That is fine for one
operator and wrong for a multi-user service. See §7.

---

## 3. Three architectures that were built and abandoned

The n8n Form Trigger is the default answer to "user uploads a file," and it does
not work here. Recording why saves the next person the same three weeks.

### Telegram delivery (the original spec)

The Phase 1 specification delivers the PDF to a Telegram chat ID.

**Why it was abandoned.** Telegram caps document uploads at 50 MB and the API is
awkward for binary PDFs. More importantly it puts a third-party service in the
middle of a pipeline whose entire appeal is that it runs entirely local. Every
send is also a rate-limit risk.

### Form Trigger → self-hosted upload page

The obvious replacement. A custom nginx page (`upload/index.html`) posts the file
to the webhook itself.

**The problem was CORS.** The browser blocked the response, so `fetch()` threw a
bare `TypeError` with no diagnostic. Two rounds of debugging produced "works in
curl, fails in browser" with nothing in any log to explain it.

**The fix was architectural, not a header tweak:** make the page and the webhook
**same-origin** by proxying through nginx.

```nginx
# upload/nginx.conf — the whole trick is this location block
location /webhook/ {
    proxy_pass http://n8n:5678/webhook/;
}
```

The browser sees one origin. No CORS preflight, no header negotiation.

### JSON sidecar

A per-report JSON file alongside each PDF, holding the same statistics.

**Why it was abandoned.** Repaired across four cycles, and then deleted. The
statistics were already in three places: the PDF itself, the webhook response,
and the filename (`pos52__neu6__neg42__net10`). A fourth copy on disk had no
consumer, and maintaining a file format nothing read is pure liability.

The lesson generalises: **before adding an artefact, name its reader.**

### What shipped

Web Trigger + local file. Same-origin through nginx, PDF written by
`readWriteFile`, statistics returned as JSON.

---

## 4. n8n behaviours that cost the most time

These are the ones that produce no error message.

### `Publish`, not `Active`

n8n 2.x has no "Active" toggle in the editor. Production webhooks register only
after clicking **Publish**. Until you do, `/webhook/...` returns 404 while
`/webhook-test/...` works perfectly — which is the worst possible combination,
because the test URL is the one you were just using successfully.

### Invalid enum values fail silently

A node parameter set to a value outside its enum is **not validated**. The node
runs, the HTTP request goes out, and the service returns an empty body. The
resulting report had correct layout, correct headers, and zero reviews, with
every status code at 200.

This is the defect class that behavioural tests cannot catch. `test 19`
(75 assertions) exists to validate node parameters against their declared enums
and types — it checks the *wiring*, which is the layer a functional test skips.

### `readWriteFile` is binary-only

"Read/Write File from Disk" does not take a JSON payload. Writing text to it
produces a file containing the JSON wrapper, not the value. The node must run in
binary mode with `dataPropertyName` pointing at the binary field.

### `Respond to Webhook` must come last

`responseMode: responseNode` means nothing is returned until this node executes.
Placing it early — to make the browser feel fast — returns before the PDF exists.
The upload page therefore waits for the whole pipeline. At 4.7–12.4 s for 100
reviews that is acceptable; it would not be at 1000.

### Relative paths resolve against `N8N_USER_FOLDER`

`readWriteFile` resolves relative paths against `~/.n8n-files`. The compose file
mounts a host directory there. Hardcoding an absolute host path would break on
any machine that is not this one, so the mount point is the contract:

```yaml
- ${DOWNLOAD_DIR:-${HOME}/Downloads/review-pulso}:/home/node/.n8n-files/downloads
```

### Environment variables fail open, not closed

Moving the encryption key to `${N8N_ENCRYPTION_KEY}` looked like a
straightforward improvement. Without a `.env` file, compose substitutes an **empty
string** and warns — it does not fail. An empty encryption key is worse than the
placeholder it replaced: n8n would either crash-loop against its stored config or,
on a fresh volume, encrypt credentials under a key that is not a secret.

The fix is `${VAR:?message}`, which makes compose refuse to start:

```yaml
- N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY:?N8N_ENCRYPTION_KEY is not set - copy .env.example to .env and set it}
```

**General rule: any secret reaching a config file should fail closed.**

---

## 5. Safety properties worth copying

### Path traversal from user-controlled filenames

`business_name` comes from the browser and is used to build the output filename.
Unsanitised, `../../etc/cron.d/x` escapes the output directory entirely. Node 7
is deliberately paranoid:

1. **Slugify** `business_name` to `[a-z0-9-]`, which structurally cannot contain
   `/`, `.`, or a null byte.
2. **Assert** the finished filename against a strict allowlist regex.

Belt and braces: the slug makes traversal impossible, and the assertion means
that if the slug function is ever changed to something less strict, the workflow
**fails loudly** instead of writing outside the directory.

`test 12` fires 46 hostile inputs at this logic.

### XSS in a rendered PDF

Review text is client-supplied and lands in generated HTML. It is escaped at
every interpolation point. A review containing `<script>` renders as visible
text, not markup. `test 17` covers it.

### Bounded work

Three independent caps on the expensive operation: Node 3 rejects >1000 rows,
the service caps at 2000, and the HTTP layer times out. Any one of them alone
would be enough; three means no single mistake opens the door.

Verified by live upload, not by inspection:

| Input | Result |
|---|---|
| 100 reviews, normal | HTTP 200, PDF written, 4.6-13.7 s |
| `business_name=<script>alert(1)</script>` | HTTP 200, filename slugified to `script-alert-1-script`, payload rendered as **literal text** in the PDF header |
| `business_name=../../../../etc/cron.d/pwned` | HTTP 200, filename slugified to `etc-cron-d-pwned`, **nothing written outside the output directory** |
| header-only CSV (zero data rows) | **HTTP 400** - "No usable reviews found" |
| blank review cells among valid rows | HTTP 200 - blanks skipped, valid rows scored |
| 1100 rows, 100 unique (repeated 11x) | HTTP 200, 100 scored — **deduped before the cap is applied** |
| 1100 rows, all unique | **HTTP 500** — rejected by the cap |

### The error surface, and why it needed a branch node

The last row above is worth reading twice, because the bug it exposed was not
about the cap.

Node 3 builds a detailed, actionable message:

```
Too many reviews: 1100 unique rows found, but the cap is 1000.
Split the CSV into files of 1000 rows or fewer and upload each one - each file
gets its own report. Uploads of ~500 rows finish in about a minute; the cap is a
safety limit, not a target. Duplicate rows were removed before counting, so this
is genuinely 1100 different reviews.
```

...and then `throw`s it. **A throw aborts the execution, so `Respond to Webhook`
never runs**, and n8n substitutes a bare `{"message":"Error in workflow"}` with
HTTP 500. A client who uploaded 1001 rows was told the workflow errored - not
that the cap is 1000, and not how to fix it. The message was constructed in one
node and discarded by the framework in the next.

The general rule: **in n8n, a `throw` is invisible to your own Respond node.**
Any check whose message is meant for a human needs a routing path, not an
exception.

### The fix

Node 3 now *returns* the rejection instead of throwing, and a branch carries it
to a dedicated responder:

```
Node 3 ──▶ Node 3b (IF: pipeline_error present?)
             ├── true  ─▶ Node 3c ──▶ HTTP 400 { accepted:false, error, hint }
             └── false ─▶ Node 4 (unchanged)
```

Two nodes added; the happy path is byte-for-byte unchanged. `upload/index.html`
gained a matching `res.status === 400` branch that parses the JSON body and
shows `error` and `hint`, because a 400 falling through to the generic 500
handler would have displayed a raw JSON string.

Both halves are asserted, because either alone is half a fix:

- `test 19` checks the IF condition tests `pipeline_error`, that its **true**
  branch (output 0) reaches the rejection responder, and that its **false**
  branch (output 1) continues to `Node 4`.
- `test 17` checks the page handles 400, parses the body, and surfaces `error`
  and `hint`.

### Two throws deliberately left in place

`Node 5` and `Node 7` still throw, and that is intentional:

| Node | Why it stays a hard failure |
|---|---|
| `Node 5` | Fires when tagasenti returns a different number of results than reviews sent. That is a broken contract between two of our own services, not bad user input - it belongs in the n8n execution log where an operator will look, and a 4xx would mislabel it as the client's fault. |
| `Node 7` | The filename safety assertion. It is a deliberate second lock against path traversal, and it must prevent `Node 8` from writing. Converting it to a soft error risks a file landing outside the output directory. Security controls should fail closed and be loud. |

The distinction that matters: **Node 3's throws were about the client's file, so
the client deserves an answer. Nodes 5 and 7 are about our own integrity, so the
operator deserves a log entry.**

### `responseCode` is an option, not a parameter

Worth recording because the first attempt at this fix appeared to work and did not.

`Respond to Webhook` takes its status code from `parameters.options.responseCode`,
not from a top-level `responseCode`. Putting it at the top level **does not error.**
n8n ignores the unknown key and answers `200`.

The failure was therefore nastier than no status at all: the rejection body arrived
correctly, carrying the real reason, under a *success* status. A client checking
`res.ok` would treat a rejected file as accepted. Live test caught it — the body
was right and the status was wrong, which is exactly the combination that survives
a casual look.

```
parameters: { respondWith: "json", responseBody: "...", options: { responseCode: 400 } }
```

Both `test 17` and `test 19` now assert the *location*, and `test 19` additionally
asserts no top-level `responseCode` exists, because the bug's signature is
"correct value, wrong place".

---

## 6. Testing strategy

`agent_journal/run_all_tests.sh` runs everything; `--full` adds the two slow
accuracy suites.

```
test 01   ONNX loads, labels verified against the model card    (slow)
test 02   inference service contract                            (slow)
test 03   padding sensitivity
test 04   thread count and size cap benchmarks                  (slow)
test 06   Code node logic — the CSV parser                      46 assertions
test 10   CSV parser edge cases (node)                          26 assertions
test 12   filename safety                                       46 assertions
test 13   report fits on one A4 page
test 16   accuracy scoring                                      (slow)
test 17   upload page, escaping, 400 path, success claims    54 assertions
test 18   proxy and CORS behaviour                              20 assertions
test 19   node parameter validation, branch wiring            75 assertions
test 20   output directory writability
```

**267 assertions in the quick suite.**

The important structural point: these tests verify **code and wiring**, not
business correctness. Nothing here can tell you the model is accurate for your
reviews. Only human-labelled data can, and that gate has never been run — see
`OPERATIONS.md` §13.

`test 03` and `test 04` are the reason the performance numbers in §2 are real.
They were run once, and their output is committed under
`agent_journal/evidence/`, so the numbers can be checked rather than trusted.

---

## 7. Honest limits

Recorded so a reader can decide whether this fits their situation.

**Not a multi-user service.** One shared output directory, no authentication, no
per-user isolation, no quotas. Concurrent uploads queue with no backpressure
because the model service is single-worker by necessity.

**The webhook is unauthenticated.** Anything that can reach it can upload.
Mitigated by binding to loopback, which is obscurity rather than access
control.

**The model is not validated for business reviews.** 84.8% is measured on
TagaSenti's own distribution; idioms are 47% of remaining errors and neutral is
the weakest class.

**The encryption key is a placeholder.** Safe only because zero credentials are
stored. It must be rotated before any are.

### If you take this further

In rough dependency order:

1. **Authenticate the webhook** before it leaves localhost. This is the
   precondition for everything else.
2. **Queue the inference requests.** A real queue decouples the single-worker
   model service from n8n's execution concurrency and makes backpressure
   explicit instead of accidental.
3. **Isolate output per user**, and define retention. Review data on disk with
   no expiry is a liability.
4. **Validate against labelled client data.** Until this happens, every accuracy
   number in this repo describes someone else's dataset.

---

## Reference

| | |
|---|---|
| Model card | <https://huggingface.co/jjjardev/tagasenti_model> |
| Dataset | <https://huggingface.co/datasets/jjjjardev/tagasenti> |
| n8n | 2.40.7 — `Publish` semantics, `readWriteFile`, `respondToWebhook` |
| Gotenberg | 8 — HTML → PDF |
| ONNX Runtime | CPU execution provider only |