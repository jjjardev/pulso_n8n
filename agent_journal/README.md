# agent_journal — the build record

This directory is the **history and tooling** for Review Pulso. It is not the
running system.

- **The running system** is the repository root, one level up from here. Start
  there if you want to *use* the tool.
- **This directory** is for when you want to know *how* it works, *why* it is
  built this way, or you need to *change* it safely.

> `product/` and `upload/` used to live here. They were promoted to the
  repository root (`workflow/` and `upload/`) so that a clone contains the
> running system rather than only its history. `product/README.md`'s warning
> about the generated JSON moved with them and is now in `../../README.md`.

---

## Start here

| If you want to… | Read |
|---|---|
| Use the tool | `../OPERATIONS.md` |
| Understand a decision | `JOURNAL.md` — the full record, with the reasoning and the mistakes |
| Change the workflow | `../README.md` § Repository layout — **the JSON is generated, do not hand-edit** |
| Check nothing is broken | `bash run_all_tests.sh` |
| Diagnose a running problem | `../OPERATIONS.md` §9 Troubleshooting |

---

## Layout

```
agent_journal/
├── README.md              this file
├── JOURNAL.md             the build record (2200 lines)
├── run_all_tests.sh       one command to run every check
│
├── 01…21 *.py *.sh *.js   the scripts, in build order
│   └── (see the table below)
│
├── data/                  test CSVs
├── evidence/              the raw output behind every claim in JOURNAL.md
├── history/               superseded files, kept for reference
│
└── snippets/              the fallback CSV parser
```

The workflow JSON (`../workflow/`) and the upload page (`../upload/`) are at
the repository root, since both are part of the running system.

### Why the scripts stay flat and numbered

The numbers are not decoration — they are **build order**, and they map onto
`JOURNAL.md`'s sections (§3 is Test 01, §8 is Test 08, and so on). Reordering
or renumbering them would break every cross-reference in the journal.

`JOURNAL.md` refers to most of these files by bare name, so the journal needed
no edits when the non-script files moved into subdirectories.

---

## The scripts

### Tests — run these

| # | What it checks | Needs |
|---|---|---|
| **02** | The scoring service: batch ordering, the review cap, guard rails, real throughput | model load (~4 min) |
| **06** | The two Code nodes: CSV cleaning, statistics, report HTML, XSS escaping, the 3-card cap | — |
| **10** | The fallback CSV parser: quoted commas, embedded newlines, BOMs, CRLF endings | — |
| **12** | The output filename against 17 path-traversal attacks | — |
| **13** | The report still fits on **one** A4 page (incl. a worst case) | — |
| **16** | Model accuracy against labelled data — see the caveat in `data/README.md` | model load |
| **17** | The upload page: its script parses, and its multipart contract matches n8n | — |
| **18** | Reproduces a real browser's CORS sequence through the nginx proxy | — |
| **19** | Every n8n node parameter against n8n's real enums, **plus the data contract between consecutive nodes** | — |
| **20** | Whether the container's user can actually write the reports folder | — |
| **09** | End-to-end: all four containers, live inference, PDF output, network isolation | **Docker** |
| **14** | Renders the report to PNG so it can be *looked at* | `google-chrome` |

```bash
bash run_all_tests.sh          # the 8 fast ones, ~5 seconds
bash run_all_tests.sh --full   # + 02 and 16, which load the 537MB model
bash run_all_tests.sh --list   # show what would run
```

### Generators

| # | What it does |
|---|---|
| **05** | Builds `../workflow/review-pulso.workflow.json`. **The source of truth for the workflow.** |
| **07** | Renders the report HTML/PDF for inspection |
| **15** | Samples the labelled test CSVs into `data/` |

### Diagnostics — these answered a question, then stayed

| # | The question it answered |
|---|---|
| **01** | Is the model correct, and is the label order right? |
| **03** | Why did batched inference disagree with one-at-a-time? |
| **04** | What thread count is fastest? |
| **08** | How fast is it *really*? — this disproved the source document's capacity claim |
| **11** | Back up / inspect the n8n data volume |
| **16** | How accurate is the model? |
| **21** | Fix permissions on the reports folder |

Diagnostics 01, 03, 04, 08 and 16 are slow and were each run once. They are
kept because `JOURNAL.md` cites their numbers, and a reader who wants to check
the arithmetic should be able to.

---

## Reading `JOURNAL.md`

It is a chronological log, not a manual. It is written so a human can audit
every decision, which means it contains the failures alongside the successes.

Two sections are worth reading even if you skip the rest:

- **§17 — every correction to the source document.** The brief this was built
  from contains twelve real errors, several of which would have failed in
  production. This is the list.
- **§18 — every mistake the build made.** Forty-eight of them, including a
  false test result, a diagnostic that silently stopped measuring, and a
  workflow feature that was removed for costing more than it was worth.

If a number in the journal is surprising, the raw output is in `evidence/`.

---

## Things not to do

- **Do not hand-edit `../workflow/review-pulso.workflow.json`,** and do not
  edit the workflow inside n8n and expect it to persist. It is generated: edit
  `05_generate_workflow_json.py` and re-run, or the next run silently reverts
  your change.
- **Do not edit anything in `evidence/`.** Those are records, not inputs.
- **Do not delete `JOURNAL.md`.** It is the only place the reasoning exists.
- **Do not copy anything from `history/` back into use** without reading
  `history/README.md` first.
