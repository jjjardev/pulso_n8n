# Review Pulso

**Tagalog/Taglish customer reviews → ternary sentiment → one-page A4 PDF.**

A drag-and-drop web page takes a CSV of Filipino business reviews, scores every
review with a locally-run ONNX model, and renders a designed PDF report to disk.
No external AI service, no API keys, no per-review cost.

| | |
|---|---|
| **Model** | [`jjjardev/tagasenti_model`](https://huggingface.co/jjjardev/tagasenti_model) — XLM-RoBERTa-large, INT8 quantized, CPU-only |
| **Labels** | `Negative` / `Neutral` / `Positive` |
| **Runtime** | 4 Docker containers: n8n, tagasenti (FastAPI + ONNX Runtime), Gotenberg (PDF), nginx (upload page) |
| **Throughput** | ~37 reviews/sec on a 6-core CPU; a 100-review report lands in **4.7–12.4 s** |
| **Credentials** | **Zero.** No API key, no token, no SMTP password. |
| **Test coverage** | 258 assertions across 8 suites, plus 2 accuracy suites |

---

## Table of contents

- [What this actually does](#what-this-actually-does)
- [Quickstart](#quickstart)
- [How this was built](#how-this-was-built)
- [Repository layout](#repository-layout)
- [Accuracy — read this before trusting a report](#accuracy--read-this-before-trusting-a-report)
- [What is not protected](#what-is-not-protected)
- [Where to go next](#where-to-go-next)

---

## What this actually does

A client opens `http://localhost:8080/`, drops in a CSV, and gets a finished PDF.

```
browser ──POST──► nginx (upload page, 127.0.0.1:8080)
                     │  same-origin proxy, so no CORS
                     ▼
                  n8n webhook (127.0.0.1:5678)
                     │
                     ├─► 1  Extract CSV        binary → rows
                     ├─► 2  Code               dedupe, validate, cap 1000, batch
                     ├─► 2b IF                 input OK?   ──no──► 400 + the reason
                     ├─► 3  HTTP               POST batches → tagasenti:8000
                     ├─► 4  Code               stats + insight rules + HTML
                     ├─► 5  HTTP               HTML → Gotenberg → PDF bytes
                     ├─► 6  Code               safe filename (traversal-proof)
                     ├─► 7  Read/Write File    PDF → ~/Downloads/review-pulso/
                     └─► 8  Respond to Webhook JSON stats back to the browser
```

The report is a single A4 page: a donut with a net sentiment score, a stacked
positive/neutral/negative bar, per-class counts, a rules-derived insight list,
and up to three verbatim comment cards. Review text is HTML-escaped, so a
review containing `<script>` cannot break the render — there is a test for it.

A bad CSV — over the cap, or no usable rows — comes back as **HTTP 400 with the
specific reason and what to do about it**, shown in the page. That required a
branch node, because a `throw` in an n8n Code node is invisible to your own
Respond node: see [ORCHESTRATION.md](ORCHESTRATION.md#the-error-surface-and-why-it-needed-a-branch-node).

**The design decisions and why they were made** are in
[ORCHESTRATION.md](ORCHESTRATION.md). Three architectures were built and
abandoned before this one; that record is the most useful part of the repo.

---

## Quickstart

**Requires:** Docker with Compose v2, ~2 GB free RAM, `python3`, and
`node` (for two of the test suites). Model weights download automatically.

```bash
git clone https://github.com/jjjardev/pulso_n8n.git
cd pulso_n8n

# 1. Configuration. The encryption key is REQUIRED - compose refuses to
#    start without it rather than silently encrypting under a blank key.
cp .env.example .env
openssl rand -hex 32          # paste into N8N_ENCRYPTION_KEY in .env

# 2. Model weights (553 MB, verified against a pinned sha256).
#    MUST come before the build: the image does COPY tagasenti_int8.onnx, and
#    without this step the build fails with a message about a missing
#    "tokenizer" that does not explain itself.
bash tagasenti/fetch_model.sh

# 3. Reports directory, writable by the container user (uid 1000).
mkdir -p ~/Downloads/review-pulso && sudo chown 1000:1000 ~/Downloads/review-pulso

# 4. Check everything a first run needs, before it fails halfway.
bash tagasenti/preflight.sh

# 5. Start.
sudo docker compose up -d --build

# 6. Import the workflow: n8n UI → Workflows → ⋮ → Import from File
#    → workflow/review-pulso.workflow.json
#    THEN click PUBLISH (top right). n8n 2.x has no "Active" toggle;
#    Publish is what registers the production webhook.
```

### Only one copy can run at a time

n8n binds `127.0.0.1:5678` and the upload page `127.0.0.1:8080`. Docker Compose
derives the **project name from the directory name**, so a second checkout — or
a clone alongside your working copy — is a completely separate project: it will
fail to bind those ports, and it gets its own `n8n_data` volume, meaning a fresh
n8n with no owner account and no workflows. `preflight.sh` checks for the port
clash and tells you which stack to stop.

If you are migrating from an older checkout that held the compose file, stop it
first so the ports free up:

```bash
cd <old checkout> && sudo docker compose down      # NOT 'down -v' — keeps the volume
```

Then open **<http://localhost:8080/>**.

Verify the install:

```bash
bash agent_journal/run_all_tests.sh    # 258 assertions, no containers needed
sudo bash agent_journal/09_smoke_test.sh   # needs the stack running
```

Full operator guidance — starting, stopping, per-service control, logs,
backups, troubleshooting — is in **[OPERATIONS.md](OPERATIONS.md)**.

---

## How this was built

This is a worked example of **specification-driven delivery**, and the process
is part of the artifact.

**Phase 1 — specification.** The idea was worked out in a web UI, back and
forth, until it was worth writing down. That document is
[`review-pulso-pipeline-docs-v1.1.md`](review-pulso-pipeline-docs-v1.1.md). It is
kept **verbatim, including the parts that turned out to be wrong** — it
specifies model v4 (v6 shipped), and delivery by Telegram (delivery is a local
file). That drift is not sloppiness; it is the record of a design changing
under implementation pressure, which is exactly when documentation earns its
keep.

**Phase 2 — build.** The specification was handed over as an implementation
brief, together with a standing instruction to produce a pedantic build journal
as it went. The result is
[`agent_journal/JOURNAL.md`](agent_journal/JOURNAL.md) — 2,440 lines. The first half records the build: measurements, failures,
dead ends, and the reasoning behind each choice. The second half records
what testing the finished pipeline found, including eight defects that survived
that build.

**Phase 3 — artifact.** A complete, runnable, documented system: 21 numbered
build/test scripts, an 854-line operator manual, and a test suite that verifies
code *and* wiring.

### The timeline

```
Sep 30  17:54  specification written                     1 artifact
        18:00  first build scripts, tokenizers verified 16 artifacts
        19:00  ONNX export checks, thread benchmarks     8 artifacts
        20:00  workflow generation begins                5 artifacts
        21:00  tests, report layout, troubleshooting     21 artifacts
        22:00  operator manual, final passes            5 artifacts
        22:19  README complete
```

**Seventeen-fifty-four to twenty-two-nineteen on Sep 30, 2026 — about four and
a half hours from specification to a complete four-container system**, producing
252 automated assertions, a 2,440-line journal, and an 854-line operator
manual. The heaviest hour produced 21 artifacts.

Two things this timeline does *not* include: the model itself was trained
separately (the INT8 export is dated Sep 29), and elapsed time includes decision
pauses, not just hands-on work.

### Why the journal is the real deliverable

A working pipeline is a screenshot. A pipeline you can hand to someone else and
say "here is exactly how it got here, including the fifty things that went
wrong" is a different asset. Roughly fifty defects, dead ends, and wrong turns
are recorded there with their causes and fixes. A few that shaped the final
design:

- An **invalid enum value in a node parameter** posted an empty body and
  returned HTTP 200. Nothing errored; the report was simply empty.
- **Rotating the n8n encryption key on a live instance** put it into a crash
  loop, because n8n persists the first key it sees in a plaintext file inside
  its volume. It only survived because the volume held **zero credentials** —
  which is exactly why rotating it early is free and rotating it late is not.
- **A JSON sidecar** alongside the PDF was built, repaired across four cycles,
  and then deleted as not earning its complexity.
- The build spec called for **four tokenizer files**. Two do not exist upstream
  and the fast tokenizer does not need them. The wasted attempt is recorded.

---

## Repository layout

```
pulso_n8n/
├── docker-compose.yml          4 services, loopback-only, parameterized
├── .env.example                every setting documented, no secrets
├── workflow/
│   └── review-pulso.workflow.json    GENERATED — see below
├── upload/                     drag-and-drop page + nginx proxy config
├── tagasenti/
│   ├── main.py                 batched FastAPI ONNX inference service
│   ├── Dockerfile              CPU-only, non-root, single worker (deliberate)
│   └── fetch_model.sh          downloads + sha256-verifies the weights
├── review-pulso-pipeline-docs-v1.1.md   Phase 1 specification, verbatim
├── OPERATIONS.md               854-line operator manual
├── ORCHESTRATION.md            design decisions, contracts, n8n gotchas
└── agent_journal/              the build record
    ├── JOURNAL.md              2,440 lines: the build, then what testing found
    ├── 01…21_*                 numbered build & test scripts, in build order
    ├── run_all_tests.sh        one-command test runner (--full for slow suites)
    ├── data/  evidence/  history/  snippets/
```

> **`workflow/review-pulso.workflow.json` is generated.** Do not hand-edit it, and
> do not edit the workflow inside n8n and expect it to stick — n8n stores changes
> in its own database, and the next run of `agent_journal/05_generate_workflow_json.py`
> silently overwrites the file. Change the generator, then re-run it:
>
> ```bash
> python3 agent_journal/05_generate_workflow_json.py
> ```

---

## Accuracy — read this before trusting a report

Two different numbers exist, and conflating them would be dishonest:

| Figure | Source | What it means |
|---|---|---|
| **84.8%** accuracy, **0.848** macro-F1 | The model's own held-out test set (3,569 rows, TagaSenti v6) | **The number to trust.** Independently reproducible from the model card. |
| **91%** (91/100) | This repo, on `agent_journal/data/business-reviews-100.csv` | A hand-written 100-row corpus that *this project* authored. It is a smoke test, not a benchmark. |

The gap is corpus choice, not model improvement. The shipped corpus is roughly
half news/political, and the hand-written set is easier than the real
distribution. **Quote 84.8%.**

Known weaknesses, straight from the model card:

- **Idioms are the unsolved case** — 8 of 17 remaining adversarial errors (47%)
  are Filipino idioms and figurative language. Stable across three training
  generations. For a review-sentiment product, this is the sharpest edge.
- **Neutral is the weakest class** (F1 0.831 vs 0.861 / 0.853). Hedged and
  mixed-sentiment reviews land in Positive or Negative. Expect neutral counts to
  run low.
- **No confidence calibration.** The service returns a single hard label with no
  probability distribution, so it cannot express uncertainty.
- **Truncated at 128 tokens.** p99 is ~71, so this is rare — but long reviews
  are cut mid-sentence.
- **Domain gaps.** Training covers e-commerce (63%), news (18%), social (19%).
  Food, restaurant, beauty, and education reviews are out of distribution.
- **Business accuracy is unvalidated.** No human-labelled client dataset exists.
  The validation gate in `OPERATIONS.md` §13 has never been run.

**The test suite verifies that the pipeline works. It cannot verify that the
model is right for your reviews.** Only labelled data can.

---

## What is not protected

Stated plainly, because a portfolio repo that hides its edges is not worth
reading.

1. **The webhook has no authentication.** `POST /webhook/review-pulso-upload`
   accepts any CSV from anything that can reach it. Mitigated by binding n8n
   to `127.0.0.1`, but this is obscurity-free access control: **do not expose
   this to a network you do not control.**
2. **The encryption key is the documented placeholder** in `.env`. It is
   harmless *only* because n8n stores zero credentials. Rotate it before adding
   any — see `OPERATIONS.md` §10 and the crash-loop note in `docker-compose.yml`.
3. **Anyone who can POST can fill the disk** at ~120 KB per upload.
4. **The inference service is single-worker on purpose.** The ONNX session holds
   ~600 MB; a second worker would load a second copy. Concurrent uploads queue
   behind each other with no backpressure.
5. **Shared output directory.** All reports land in one folder. Fine for one
   operator, wrong for multiple users.
6. **Review text is written to disk in plaintext.** Reports are not encrypted.
7. **Upstream training data has no PII scrubbing** — the model card's own
   notice. Not this repo's doing, but worth knowing before deploying on
   anything sensitive.

---

## Where to go next

| If you want to… | Read |
|---|---|
| Run it day to day | [OPERATIONS.md](OPERATIONS.md) |
| Understand the design | [ORCHESTRATION.md](ORCHESTRATION.md) |
| Follow the build | [agent_journal/JOURNAL.md](agent_journal/JOURNAL.md) |
| See the original spec | [review-pulso-pipeline-docs-v1.1.md](review-pulso-pipeline-docs-v1.1.md) |
| Understand the model | [model card](https://huggingface.co/jjjardev/tagasenti_model) · [dataset](https://huggingface.co/datasets/jjjjardev/tagasenti) |

## Licences

Code and configuration: **Apache 2.0** (this repository).
Model weights: **Apache 2.0** (upstream, by Jessie James Jarder).
Training dataset: **CC BY-SA 4.0** — *not redistributed here*; fetched from the
Hub if you want it. See the model's `LICENSES.md` for the tri-partite breakdown.

Built by [Jessie James Jarder](https://huggingface.co/jjjardev) ·
`jj.jarder.dev@gmail.com`