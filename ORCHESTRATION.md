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

Nine nodes, one linear chain plus a terminal branch.

| # | Node | Type | Consumes | Produces |
|---|---|---|---|---|
| 1 | `Node 1` | `webhook` | multipart POST | binary + `business_name`, `context` |
| 2 | `Node 2` | `extractFromFile` | binary CSV | items, one per row |
| 3 | `Node 3` | `code` (runOnceForAllItems) | items | batched request payloads |
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

## 2. Batching, and why the cap is 1000

The ONNX service processes batches of 8. Benchmarks (`04`, `08`) established
that larger batches are **slower**, not faster:

| Batch | Throughput |
|---|---|
| 8 | **37.3 rev/s** |
| 32 | ~34 rev/s |
| 64 | 32.1 rev/s |

That is expected for a 24-layer, 1024-hidden transformer on CPU: wide padded
batches thrash cache. The batch size is measured, not assumed.

Thread count matters more than batch size, and the default is actively harmful:

| Threads | Throughput |
|---|---|
| 2 | 2.4 rev/s |
| **4** | **2.7 rev/s** |
| 8 | 1.5 rev/s |
| 12 | 1.0 rev/s |

onnxruntime defaults to one thread per logical core; on this box that is
oversubscription, and it is roughly **2× slower** than four threads. The service
sets `intra_op_num_threads=4` explicitly.

### Where 1000 comes from

`01` measures throughput on a corpus of reviews deliberately padded past the
128-token truncation limit, because a full-length row is the most expensive
thing you can ask the model to compute. Padded rows are dramatically slower than
the median row:

| Corpus | Rate |
|---|---|
| Median-length reviews | **37.3 rev/s** |
| Every row padded past 128 tokens (worst case) | **12.8 rev/s** |

Sizing uses the worst case, with 2× headroom for a loaded machine:

```
est_seconds = cap / 12.8
cap / 12.8 * 2  <  120s     ->     cap < 768
```

A 120 s budget yields a ceiling of **768** on pure worst case. The cap is set to
**1000** — deliberately above the worst-case ceiling, because a corpus where
*every* row is a 128-token row is not a real corpus. The benchmark is an upper
bound, not an expectation. Two independent guards sit behind it:

1. **Node 3 rejects** uploads over 1000 rows, before any inference runs.
2. **The service caps** at 2000 (`MAX_BATCH_REVIEWS`) as a backstop for direct
   callers who bypass the workflow.

Node 4 has a 600 s timeout; the workflow itself has 900 s. Measured reality is
4.7–12.4 s for a 100-review report — three orders of magnitude of headroom
against the pathological case.

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
(65 assertions) exists to validate node parameters against their declared enums
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
test 17   upload page, escaping, workflow wiring                40 assertions
test 18   proxy and CORS behaviour                              20 assertions
test 19   node parameter validation against declared enums      65 assertions
test 20   output directory writability
```

**243 assertions in the quick suite.**

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