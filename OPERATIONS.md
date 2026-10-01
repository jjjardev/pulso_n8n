# Review Pulso — Operator Manual

A tool that takes a CSV of customer reviews, works out whether each one is
**positive, neutral or negative** in Filipino, and produces a one-page PDF
report summarising the lot.

You upload a spreadsheet. A PDF appears in a folder on your computer. That is
the whole product.

> **This is the operator manual** — how to run, inspect, back up, and repair the
> system. For what the project is and how it was built, see
> [`README.md`](README.md); for design decisions and n8n pitfalls, see
> [`ORCHESTRATION.md`](ORCHESTRATION.md).
>
> Accuracy figures in this manual describe a smoke test. The number to trust is
> in [`README.md` § Accuracy](README.md#accuracy--read-this-before-trusting-a-report).

---

## Table of contents

| | |
|---|---|
| **[1. What this actually does](#1-what-this-actually-does)** | The product, in plain language |
| [2. What you need](#2-what-you-need) | Requirements, and the one permission thing that trips people up |
| [3. Everyday use: running a report](#3-everyday-use-running-a-report) | **Start here** — the 7 steps |
| [4. Where the files go](#4-where-the-files-go) | Which folder holds what |
| **[5. Starting, stopping, and controlling the system](#5-starting-stopping-and-controlling-the-system)** | **All the start/stop commands** — including how to stop one service to free memory |
| [6. Checking that things are healthy](#6-checking-that-things-are-healthy) | The one command that checks everything |
| [7. How to add the workflow to n8n](#7-how-to-add-the-workflow-to-n8n) | Import and Publish (not "Activate") |
| [8. Reading the log files](#8-reading-the-log-files) | And why the log can't tell you why a run failed |
| [9. Troubleshooting](#9-troubleshooting) | Every fault that actually happened |
| [10. Backing up](#10-backing-up) | Reports and n8n data |
| [11. Maintenance](#11-maintenance) | Running the tests; the memory warning |
| [12. Security](#12-security) | What's protected, and what isn't yet |
| [13. Things that are not finished yet](#13-things-that-are-not-finished-yet) | **Read before showing this to a client** |
| [14. Glossary](#14-glossary) | Docker and n8n terms explained |
| [15. Where everything lives](#15-where-everything-lives) | File map |

### The three commands you'll use most

```bash
cd <repo>

sudo docker compose ps                                    # what's running
sudo docker compose stop tagasenti                        # free 1.2 GB of RAM
sudo docker compose start tagasenti                       # put it back
```

---

## 1. What this actually does

A client has a spreadsheet of customer reviews. They were thinking of sending
it to someone to read and summarise, which takes an hour and varies depending
on who does it. This tool reads it in about a minute and produces a consistent
report.

### The steps, in order

```
1.  YOU drag a CSV onto a web page  →  2.  n8n receives the file
3.  the CSV is parsed into rows     →  4.  empty rows removed, duplicates dropped
5.  TagaSenti scores every review    →  6.  counts, percentages, net score
7.  an HTML report is built          →  8.  Gotenberg turns it into a PDF
9.  the PDF is written to a folder   →  10. the browser says "done"
```

### The four programs involved

| Program | What it does | Why it exists |
|---|---|---|
| **n8n** | Moves the data from one step to the next | It's the "glue". You build the pipeline by dragging boxes around in a web page. |
| **tagasenti** | Reads a review and says positive / neutral / negative | This is the actual intelligence. It's a 537 MB AI model that speaks Filipino. |
| **Gotenberg** | Turns a web page into a PDF | It runs the hidden Chromium browser. It's why the report looks like a designed page rather than plain text. |
| **nginx** | Serves the web page you upload from | One small file. It exists only because the upload page needs to be served from somewhere. |

All four run inside Docker. Docker is just a way of running several programs
side by side without them fighting over ports or libraries. You start and stop
them together with one command.

### The words in the filename

Every report is named so you can understand it without opening it:

```
review-pulso__sarisari-store-malate__20260930-125313__n100__pos37__neu26__neg37__net0.pdf
 └─ what it is ─┘└──── the business ────┘└── when (UTC) ──┘ └n─┘└─ positives ─┘└ neutral ┘└negative┘└net┘
```

`n100` = 100 reviews were analysed. `pos37` = 37% positive. `net0` = the net
score, where positives minus negatives happens to be zero. Because the files
sort alphabetically by date, `ls` shows you the whole history in order.

### What the report contains

- A header with the business name and a plain-English verdict
  (*Excellent / Good / Mixed / At risk / Critical*)
- Four big numbers: % positive, % neutral, % negative, and the net score
- A donut chart of the sentiment split, and a gauge showing the net score
  between −100 (all bad) and +100 (all good)
- Written observations, e.g. "3 negative reviews with ≥85% confidence — treat
  these as confirmed complaints"
- The top 3 negative and top 3 positive reviews, with a confidence bar
- Recommended actions
- Up to 3 of each polarity is shown. If there were more, the report says so
  explicitly rather than quietly hiding them.

---

## 2. What you need

You already have all of this. This section is so you can check, and so the
project can be rebuilt on another machine.

| Requirement | Version in use | How to check |
|---|---|---|
| Docker | 29.8.1 | `docker --version` |
| Docker Compose | v5.5.1 | `docker compose version` |
| ~8 GB free disk | 61 GB free here | `df -h ~` |
| Docker permission | your user in the `docker` group | `docker ps` |

### The one thing that trips people up

Your user must be in the `docker` group, or every `docker` command fails with:

```
permission denied while trying to connect to the docker API at unix:///var/run/docker.sock
```

Fix it once:

```bash
sudo usermod -aG docker $USER
```

Then **log out and log back in**. Group membership is read at login, so a new
terminal window is not enough — you need a new session. If you cannot re-login
now, use `newgrp docker` in a terminal, or just prefix commands with `sudo`.

> **Note:** after `usermod`, running `docker ps` in an existing terminal will
> *still* fail. That is expected, not a second problem.

---

## 3. Everyday use: running a report

This is the part you'll do most.

1. **Open the upload page:** <http://localhost:8080>
2. **Drag your CSV onto the big dashed box.** Or click it to browse for a file.
3. **Type the business name.** Required.
4. **Context is optional** — e.g. "Q3 2026, Shopee + Google Maps".
5. **Click "Generate report".**
6. The page says it's done in about a second. **The report is not finished
   yet** — scoring takes a minute or two.
7. **Open the reports folder:** `~/Downloads/review-pulso` (in Files, it's
   under Downloads → review-pulso).

### How long it takes

| Reviews | Measured end to end | Notes |
|---|---|---|
| 100 | **4.6 – 13.7 s** | the spread is machine load, not the model |
| 500 | **~24 s** | |
| 1000 | **~52 s** | the cap; comfortably inside the 600 s timeout |

These are wall-clock timings for the whole pipeline — upload, inference, PDF
render, write — measured on this 6-core machine, not inferred from the
throughput benchmark.

Long, detailed reviews cost far more: on a worst-case corpus (~112-token rows)
1000 reviews took ~173 s against the 600 s timeout. That margin is comfortable
here but shrinks under CPU load — the same work has run 2-4x slower on a busy
machine, which would overrun the timeout. The 1000-row cap is conservative
rather than tuned.

The hard limit is **1000 reviews per upload**. Beyond it the page returns
HTTP 400 and tells you to split the file — a deliberate safety limit, not a
fault. Duplicate rows are removed *before* the count, so uploading the same
file twice does not hit the cap.

### What your CSV must look like

- One review per row.
- The first row must be a **header** with a column name.
- The column name must be one of: `review`, `text`, `comment`, `content`,
  `feedback` (case doesn't matter).
- Reviews containing commas are fine, as long as the CSV quotes them — which is
  what Excel does automatically.

Minimal example:

```csv
review
"Ang bilis ng delivery at ang sarap ng food!"
"Grabe ang panget ng packaging, basag na basag."
"Sulit na sulit, babalik ulit ako dito"
```

Ready-made test files are in `agent_journal/`:
`business-reviews-100.csv` (100 hand-written business reviews) and
`test-100-reviews.csv` (100 sampled from the TagaSenti corpus).

---

## 4. Where the files go

| What | Where | Notes |
|---|---|---|
| **Reports (PDFs)** | `~/Downloads/review-pulso/` | The product. Filename carries the statistics. |
| The AI model | `tagasenti/tagasenti_int8.onnx` | 537 MB. Do not delete. |
| The tokenizer | `tagasenti/tokenizer/` | 17 MB, three files. |
| n8n's own data | Docker volume `n8n_n8n_data` | Workflows, credentials, history. |
| Reports of the future | `tagasenti/` → no, the **uploads** you send | You send CSVs; they are not stored. |
| Backups | `~/n8n-backups/` | Created only when you run the backup command. |

**Reports are the only thing you'll regularly interact with.** Everything else
is machinery.

---

## 5. Starting, stopping, and controlling the system

**Every command in this section must be run from the repo root**, because Docker
Compose reads the `docker-compose.yml` that lives there:

```bash
cd <repo>
```

### 5.1 The four services

| Service | What it is | Where you see it | Uses |
|---|---|---|---|
| `n8n` | The workflow engine. Your reports are built here. | <http://localhost:5678> | 5678 |
| `tagasenti` | The AI. Reads a review, says positive/neutral/negative. | never — it has no web page | ~1.2 GB RAM |
| `gotenberg` | Turns the report into a PDF. | never | small |
| `uploader` | Serves the drag-and-drop page. | <http://localhost:8080> | 8080 |

You only ever *visit* two of them. The other two are machinery.

### 5.2 The commands you actually need

| I want to… | Command |
|---|---|
| **Start everything** | `sudo docker compose up -d` |
| **Stop everything** (keeps all data) | `sudo docker compose down` |
| **Restart everything** | `sudo docker compose down && sudo docker compose up -d` |
| Start, rebuilding the AI image | `sudo docker compose up -d --build` |
| **See what's running** | `sudo docker compose ps` |
| **Stop one service** (e.g. the AI) | `sudo docker compose stop tagasenti` |
| **Start one service back up** | `sudo docker compose start tagasenti` |
| Restart one service | `sudo docker compose restart n8n` |
| Follow the logs live (Ctrl-C to stop) | `sudo docker compose logs -f` |
| Logs for one service | `sudo docker compose logs n8n --tail 60` |
| **Stop everything and delete all n8n data** ⚠️ | `sudo docker compose down -v` |

> ⚠️ **`down -v` deletes the n8n database** — every workflow, credential, and
> all execution history. There is no undo. Back up first, see §10.

### 5.3 After a reboot

**You don't need to do anything.** Every service is set to
`restart: unless-stopped`, so Docker brings them all back on its own. This was
verified when the machine ran out of memory and had to reboot mid-session:
all four came back unaided and the very next upload worked.

To check they're up:

```bash
cd <repo> && sudo docker compose ps
```

### 5.4 Stopping just the AI service to free memory

`tagasenti` holds the 537 MB model **in memory** between requests — that is
deliberate, since reloading it per request would be far slower. It uses about
**1.2 GB** of RAM, which on an 8 GB machine is a lot.

If you need memory back, stop just that service:

```bash
cd <repo>
sudo docker compose stop tagasenti
free -h          # confirm ~1.2 GB came back
```

**What you give up:** the pipeline can no longer score reviews. The upload
page and n8n stay up, but an upload will reach n8n and fail at the model step.
This is the intended way to free memory before running the slow tests
(§11), which load the model into the host's memory as well.

Start it back when you're done:

```bash
cd <repo>
sudo docker compose start tagasenti
```

Then wait about ten seconds for the model to load off disk, and confirm:

```bash
sudo bash ~/Desktop/PROJECT_with_deps/pulso_n8n/agent_journal/09_smoke_test.sh
```

> ### ⚠️ Do **not** use `kill` to stop a service
>
> `kill <pid>` makes the process die, and because of
> `restart: unless-stopped` Docker **immediately restarts the container** — so
> the memory comes straight back and you have gained nothing.
>
> Use `docker compose stop <service>`. That marks the container as
> deliberately stopped, so Docker leaves it alone.
>
> For the record: port 8000 (the AI service) is **not open on your computer
> at all** — it only exists inside the container's private network. If you
> `kill` whatever you find on "port 8000" you will find nothing; the memory
> lives in the container's `uvicorn` process, not in a host port.

### 5.5 `up` versus `build`

`up -d` starts containers from images that already exist. If you changed any
file inside `tagasenti/` — such as `main.py` — use `up -d --build`, or
Docker reuses the old image and **your change silently does nothing**. That
has cost real debugging time on this project.

Use `--build` when you change:
- `tagasenti/main.py` or its `Dockerfile`
- `docker-compose.yml` (plain `up -d` is enough)

You do **not** need `--build` after changing the workflow JSON or the upload
page — those are read by the running containers, not baked into an image. For
the upload page, recreate it so the new file is picked up:

```bash
cd <repo> && sudo docker compose up -d --force-recreate uploader
```

### 5.6 Reading `docker compose ps`

```
NAME              STATUS                      PORTS
n8n-n8n-1         Up 2 hours (healthy)         0.0.0.0:5678->5678/tcp
n8n-tagasenti-1   Up 2 hours (healthy)         8000/tcp
n8n-gotenberg-1   Up 2 hours (healthy)         3000/tcp
n8n-uploader-1    Up 2 hours (healthy)         127.0.0.1:8080->80/tcp
```

- **`(healthy)`** is what you want — the service answered a real request.
- **`Up 8 seconds`** next to `CREATED 4 minutes ago` means a **crash loop**.
  The health column catches this; the status column on its own does not.
- **PORTS** shows who can reach what. `0.0.0.0:5678` means anything on your
  network can. `8000/tcp` with no host address means **only other containers
  can** — that is deliberate for the AI and Gotenberg.

### 5.7 If a service is stuck or misbehaving, in order

```bash
cd <repo>

# 1. is it even running?
sudo docker compose ps

# 2. what does it say?
sudo docker compose logs <service> --tail 60

# 3. restart just that one
sudo docker compose restart <service>

# 4. still broken? rebuild it
sudo docker compose up -d --build

# 5. nuclear option, keeps data
sudo docker compose down && sudo docker compose up -d
```

Valid service names: `n8n`, `tagasenti`, `gotenberg`, `uploader`.

### 5.8 Common mistakes

| Mistake | What happens | Do instead |
|---|---|---|
| Running `docker compose ps` from the wrong folder | "no configuration file provided" | `cd` to the repo root first |
| `docker compose up -d` after editing `main.py` | Old image runs; your change is ignored | add `--build` |
| `kill <pid>` on a service | Docker restarts it; nothing gained | `docker compose stop <service>` |
| `docker compose down -v` by accident | All n8n data gone, no undo | Avoid `-v` unless you mean it |
| Expecting `Up 20 minutes` to mean "working" | It may be crash-looping | Look for `(healthy)` |
| Running the slow tests with the AI service up | Machine runs out of memory, may need a reboot | Stop `tagasenti` first — see §5.4 and §11 |

---

## 6. Checking that things are healthy

Run this. It checks the four services, confirms the security isolation is
intact, and sends the AI model three test sentences with known answers.

```bash
sudo bash ~/Desktop/PROJECT_with_deps/pulso_n8n/agent_journal/09_smoke_test.sh
```

You are looking for:

```
RESULT: ALL SMOKE TESTS PASSED
```

### Reading `docker compose ps` output

```
NAME              STATUS                      PORTS
n8n-n8n-1         Up 2 hours (healthy)         0.0.0.0:5678->5678/tcp
n8n-tagasenti-1   Up 2 hours (healthy)         8000/tcp
n8n-gotenberg-1   Up 2 hours (healthy)         3000/tcp
n8n-uploader-1    Up 2 hours (healthy)         127.0.0.1:8080->80/tcp
```

- **`(healthy)`** is what you want. It means the service answered a real request.
- **`Up 8 seconds`** when `CREATED` says `4 minutes ago` means a **crash
  loop** — the container keeps restarting. The health column catches this; the
  status column alone does not.
- **PORTS** tells you who can reach the service. `0.0.0.0:5678` means anything
  on your network can. `8000/tcp` with no host address means **only other
  containers** can — that is deliberate for the model and Gotenberg.

### If something is unhealthy

```bash
sudo docker compose logs n8n --tail 60      # or tagasenti, gotenberg, uploader
```

---

## 7. How to add the workflow to n8n

You need to do this once, and again whenever the workflow file changes.

1. Open <http://localhost:5678> and log in.
2. Click **Workflows** in the left sidebar.
3. Click the **⋮** (three dots) at the top right → **Import from File**.
4. Choose `workflow/review-pulso.workflow.json`.
5. Click **Publish**.

   > The workflow has **11 nodes**. If you see 9, you have an older copy of the
   > JSON — regenerate it with `python3 agent_journal/05_generate_workflow_json.py`
   > and import again. The two extra nodes are the input-validation branch that
   > turns a rejected CSV into a readable HTTP 400 instead of *"Error in
   > workflow"*.

> ### ⚠️ "Publish", not "Activate"
>
> n8n version 2.x **removed the Active/Inactive switch**. A production webhook
> only exists once a workflow is **published**. If you skip this, every upload
> fails with an error like *"The requested webhook is not registered"* or
> *"Error in workflow"*.
>
> If a published workflow still returns 404: unpublish it, nudge a node
> slightly (drag it and put it back), and publish again. That forces n8n to
> re-register the webhook. This is a known n8n quirk.

### Confirming it worked

```bash
curl -s -X POST http://localhost:5678/webhook/review-pulso-upload \
  -F "reviews_file=@/path/to/any.csv" -F "business_name=check"
```

You should get a JSON reply containing `"accepted": true` and the filename of
the report. **An empty reply means the workflow is out of date** — re-import it.

---

## 8. Reading the log files

```bash
sudo docker compose logs n8n            # everything, all history
sudo docker compose logs n8n --tail 60 # last 60 lines
sudo docker compose logs -f n8n         # follow live, Ctrl-C to stop
sudo docker compose logs --since 10m    # last 10 minutes
```

Errors you'll recognise:

| In the log | Meaning |
|---|---|
| `Received request for unknown webhook` | The workflow isn't published. See §7. |
| `Mismatching encryption keys` | `N8N_ENCRYPTION_KEY` doesn't match what's stored. See §9. |
| `Forbidden by access permissions` | The container can't write the reports folder. See §9. |
| `This operation expects ... binary file 'reviews_file'` | The file didn't arrive. Re-import the workflow. |

**Important:** n8n does *not* log details of why an individual execution
failed. For that you must use the n8n interface: **Workflows → Executions →
click the red failed run**. It names the exact node and error. This is the
fastest way to diagnose any problem, and it is what the log cannot tell you.

---

## 9. Troubleshooting

Each of these actually happened during the build.

### "That CSV was rejected" (HTTP 400)

Working as intended — the pipeline refused the file and told you why. Two
checks produce this:

| Message | Cause | Fix |
|---|---|---|
| `No usable reviews found` | No row had a usable `review`/`text`/`comment`/`content`/`feedback` cell. Rows under 3 characters are skipped, and duplicates removed, so a file can reach zero. | Check the column name and that cells are not empty. |
| `Too many reviews: N unique rows` | Over the 1000-review cap. | Split the CSV into files of ≤1000 rows. ~500 rows finishes in about a minute. The cap is a safety limit, not a target. |

If you instead see **"Error in workflow"** for input that should be fine, that
is the *old* 9-node workflow, which threw instead of answering. Regenerate the
JSON and re-import:

```bash
python3 agent_journal/05_generate_workflow_json.py
```

### "Error in workflow" (HTTP 500)

A node failed and n8n could not tell you which. Check the execution log:

```bash
sudo docker compose logs n8n --tail 60
```

Two throws are intentional and land here — see `ORCHESTRATION.md` for why they
stay hard failures: **Node 5** fires when tagasenti returns a different number
of results than reviews sent (a broken contract between our own services), and
**Node 7** is the filename safety assertion that must block the write.

### "Could not reach the server" / `TypeError: NetworkError`

The browser could not deliver your file.

- Check the upload page is served: `curl -s -o /dev/null -w '%{http_code}'
  http://localhost:8080/` should print `200`.
- Do **not** open `index.html` as a file (`file:///...`). It must be served.
- If the uploader was recently changed, recreate it:
  `sudo docker compose up -d --force-recreate uploader`.

### "The report service is not running (HTTP 404)"

The workflow isn't published. Go to §7.

### "The report service is not running (HTTP 404)" but it *is* published

Unpublish, drag a node, publish again. Known n8n behaviour.

### `Mismatching encryption keys`

n8n will not start. The key in `docker-compose.yml` must match the key
n8n stored the first time it ever started. That stored key is written in
**plaintext** to a file called `config`, which lives *inside the container* at
`/home/node/.n8n/config` (not on your computer — read it through Docker):

```bash
sudo docker run --rm -v n8n_n8n_data:/data:ro busybox cat /data/config
```

It should read `{"encryptionKey": "change-me-to-a-long-random-string"}`. If
your compose file says something else, put the stored value back.

> This happened during setup. The original key was a placeholder, and
> replacing it with a strong one stopped n8n from starting. Because there were
> **no saved credentials at the time**, nothing was lost and the original key
> was restored. See §12 before changing it now that the pipeline is live.

### `Forbidden by access permissions`

The reports folder isn't writable by the user inside the container.

```bash
bash ~/Desktop/PROJECT_with_deps/pulso_n8n/agent_journal/20_check_downloads_permissions.sh
```

The two users have different ids: the host user is **1001**, n8n inside the
container is **1000**. The folder must be writable by others:

```bash
chmod 777 ~/Downloads/review-pulso
```

### The PDFs are read-only

Also a permissions issue: n8n creates files as uid 1000 with the container's
default umask, giving mode 644 — and you are uid 1001, so you get read but not
write. You can still read, delete, rename and create in the folder; only
editing a PDF in place is blocked.

**Zero-risk fix**, `sudo crontab -e`, add:

```
  # Replace /path/to/pulso_n8n with your checkout path.
  */5 * * * * /bin/bash /path/to/pulso_n8n/agent_journal/21_fix_report_permissions.sh >/dev/null 2>&1
  ```

It must be the **root** crontab — you cannot change permissions on a file you
don't own.

**Cleaner fix:** uncomment this line in `docker-compose.yml`:

```yaml
command: ["sh", "-c", "umask 000 && exec n8n"]
```

then `sudo docker compose up -d n8n`. Rollback: delete the line and run the
same command again. **This was never tested** — see `agent_journal/JOURNAL.md`
§10.0.20 for why.

### The n8n web interface is blank or broken after a restart

Browser cache. n8n's web files are named with a content hash that changes
every time the container is recreated, so a cached page points at a file that
no longer exists.

1. Open <http://localhost:5678> in an **incognito/private window**. If it works
   there, it's definitely the cache.
2. Otherwise hard-reload: **Ctrl+Shift+R** (Cmd+Shift+R on Mac).
3. Still broken: browser developer tools → Application → Storage → **Clear
   site data**.

### A report is missing but the page said "done"

The page confirms the file was *uploaded*, not that the report was *finished*.
Check the folder a minute later. If nothing appears, open **Workflows →
Executions** and look for a red failed run.

### Uploads fail after about 1000 reviews

That is the safety limit. Split the CSV into chunks of 1000 or fewer and
upload each separately — each gets its own report.

### A report takes far longer than the table in §3 says

The machine is also running other work (you have a Postgres, Redis and
Flaresolverr container). The timings assume an otherwise idle machine.

### The machine ran out of memory and you had to reboot

Symptoms: everything is very slow, or the desktop starts refusing
applications, and Linux eventually kills a process to free memory. The usual
culprit here is **memory pressure, not a bug in the pipeline** — see the
warning in §11 about `run_all_tests.sh --full` loading extra copies of the
model.

Check what is using memory:

```bash
free -h
ps -eo pid,rss,comm --sort=-rss | head -10   # sizes are in KB
```

Expected baseline while the stack is idle: the `tagasenti` container
(`uvicorn`) is the largest single consumer at roughly **1.2 GB**, because the
model stays in memory between requests. That is normal and intentional —
reloading the model per request would be far slower.

If you need headroom temporarily:

```bash
sudo docker compose stop tagasenti    # frees ~1.2 GB; no reports while stopped
sudo docker compose start tagasenti
```

**If you do get OOM-killed**, everything survives. The containers are set to
restart on their own, the reports folder is on disk, and n8n's data is in a
volume — a reboot loses nothing except any test run that was in progress.

---

## 10. Backing up

**The reports folder is your product, and it is not backed up by anything.**
Copy it somewhere safe periodically. That is a decision for you; the tool
cannot know what your reports are worth.

The AI model (537 MB) and the code can be re-downloaded or re-obtained, so
there is no point backing those up.

### Backing up n8n's own data

Contains your workflow, credentials and history.

```bash
sudo bash ~/Desktop/PROJECT_with_deps/pulso_n8n/agent_journal/11_n8n_volume.sh backup
```

Writes a timestamped tarball to `~/n8n-backups/`. The restore command is
printed when it finishes — **keep the tarball**, and read the restore command
before you need it, not during an incident.

### To wipe everything and start over

```bash
cd <repo>
sudo docker compose down -v            # deletes n8n data
rm -rf ~/Downloads/review-pulso/*      # deletes reports
sudo docker compose up -d --build      # rebuilds from scratch
```

You will then need to re-import the workflow (§7) and create the Telegram-style
credentials, if you had any. There are none at present.

---

## 11. Maintenance

### Monthly

- Copy `~/Downloads/review-pulso` somewhere backed up.
- Run the smoke test (§6). If it fails, something has broken.

### When you change the code

| You changed | Then run |
|---|---|
| Anything in `tagasenti/` | `sudo docker compose up -d --build` |
| `docker-compose.yml` | `sudo docker compose up -d` |
| `agent_journal/05_generate_workflow_json.py` | Re-import the workflow and Publish (§7) |
| `upload/index.html` or `nginx.conf` | `sudo docker compose up -d --force-recreate uploader` |

### Running the test suite

Before and after any change, the tests should all pass:

```bash
cd ~/Desktop/PROJECT_with_deps/pulso_n8n/agent_journal
python3 06_test_code_nodes.py            # the report-building code
python3 17_test_upload_page.py           # the upload page
python3 18_test_proxy_and_cors.py        # browser behaviour
python3 19_validate_node_params.py       # n8n wiring and data contracts
bash    20_check_downloads_permissions.sh
node    10_test_csv_parser.js            # CSV edge cases
node    12_test_filename_logic.js        # filename safety
python3 13_fit_one_page.py --stress      # the report fits on one page
```

`09_smoke_test.sh` needs the containers running; the rest do not.

---

## 12. Security

### What is protected

- The AI model (port 8000) and Gotenberg (3000) **publish no ports at all** —
  they are reachable only from inside the Docker network. The smoke test
  verifies this, and it also checks that n8n *is* reachable, so the check cannot
  pass by silently testing nothing.
  - **n8n is bound to `127.0.0.1:5678`** and the upload page to `127.0.0.1:8080`.
    Nothing is reachable from the network. This was previously n8n on `0.0.0.0`,
    which left an unauthenticated webhook answering on the LAN.
    Re-check it after any n8n upgrade, because the default is `0.0.0.0`:

    ```bash
    sudo docker compose ps --format '{{.Service}} {{.Ports}}'
    # want:  n8n  127.0.0.1:5678->5678/tcp
    # if it says 0.0.0.0:5678->5678/tcp, the webhook is open to your network
    ```
- Review text in a report is escaped, so a review containing `<script>` cannot
  break the PDF. There is a test for this (`17`).
- The output filename is slugified *and* asserted against an allowlist regex,
  so a client-supplied `business_name` cannot escape the output directory.
  `test 12` fires 46 hostile inputs at it.

### What is not protected — read this before adding a second user

1. **The webhook has no authentication at all.** The path is a fixed,
   guessable string (`/webhook/review-pulso-upload`) and anyone who can reach
   it can upload. Loopback binding is what protects you today; that is
   network-level obscurity, not access control. **Do not expose this to a
   network you do not control.**
2. **The encryption key is still `change-me-to-a-long-random-string`.** It is
   harmless *only* because n8n stores zero credentials. Rotate it before adding
   any: back up the volume (§10), set a real key in `.env`, delete the `config`
   file inside the volume, restart n8n, and re-enter any credentials. Setting
   only `.env` while the volume still holds the old key puts n8n into a crash
   loop — see the long note in `docker-compose.yml`.
3. **Anyone who can POST can fill the disk.** Each upload writes ~120 KB.
4. **All reports share one directory.** Fine for one operator; a data leak the
   moment there are two.
5. **Report contents are not encrypted at rest.** Review text sits in plaintext
   PDFs.

### Reviewing before you trust it

Every report shows the model, its licence and a DOI. Keep that attribution if
you share reports — TagaSenti is Apache-2.0 and requires it.

---

## 13. Things that are not finished yet

**Current state: working, verified end to end.** An upload produces a
one-page A4 PDF in `~/Downloads/review-pulso`, and the browser receives a
completion receipt:

```json
{
  "accepted": true,
  "message": "Report generated and saved to the download folder.",
  "business_name": "README-Final",
  "pdf": "review-pulso__readme-final__20260930-133014__n100__pos37__neu26__neg37__net0.pdf",
  "reviews": 100,
  "net_score": 0,
  "verdict": "Mixed - room to improve",
  "low_confidence_reviews": 2
}
```

The honest caveat is below, and it is about **trustworthiness, not
function**.

Please read this before showing the tool to a client.

### The accuracy has not been validated for your use

The AI model was measured at **89.9% macro F1** on 100 reviews drawn from the
TagaSenti dataset. But that dataset is mostly **news and political text**, and
only about 15 of those 100 rows were business reviews. Review Pulso is for
Google Maps and Shopee feedback, which is a different domain.

**A trustworthy number needs ~200 real business reviews, labelled by a human
who reads Filipino.** That is a day of work and it is not optional before
someone pays for this.

### It under-reports neutral reviews

In both test sets, **every single mistake was a Neutral-boundary mistake** —
reviews that are genuinely mixed being called positive. The model is reluctant
to say "neutral".

Practical effect: a client with lots of mixed reviews will see the Neutral
figure too low, and a 3-star average will not look like 3 stars. Expect
questions about it. §13.1 of the validation run should check this specifically.

### Things deliberately left out

- **No cleanup of old reports.** The folder grows forever.
- **No email or notification.** You have to look in the folder.
- **No multi-business tracking.** Each upload is independent; the tool does not
  remember that Café Korner ran a report in March.
- **The 1000-review cap** is a safety limit, not a throughput target.

---

## 14. Glossary

Plain explanations of every term used above.

| Term | Meaning |
|---|---|
| **Container** | One program, packaged with everything it needs so it can't interfere with others. Like a separate computer that shares the CPU. |
| **Image** | The packaged program, before it runs. Building an image is making it; running a container is starting it. |
| **Docker Compose** | A file (`docker-compose.yml`) describing several containers that should run together. One command starts them all. |
| **Volume** | Storage that survives a container being deleted. Without one, deleting the container deletes the data. |
| **Port mapping** | `5678:5678` means "the host's port 5678 connects to the container's port 5678". Without a mapping, only other containers can reach it. |
| **`expose` vs `ports`** | `expose` = visible to other containers only. `ports` = visible to the host, and possibly your whole network. |
| **`-d`** (detached) | Start in the background so you get your prompt back. |
| **`restart: unless-stopped`** | Start again automatically after a crash or reboot. |
| **n8n** | A workflow tool. You connect boxes; data flows between them. |
| **Workflow** | The diagram of boxes — one report run. |
| **Node** | One box in the diagram. |
| **Trigger / Webhook** | The starting point. A webhook is a URL that starts the workflow when something is sent to it. |
| **Test URL vs Production URL** | Test works for 120 seconds while you edit. Production works while the workflow is *published*. |
| **Publish** | n8n 2.x's way of "going live". Without it, webhooks don't exist. |
| **Execute / execution** | One run of the workflow. Listed under Workflows → Executions. |
| **Binary field** | A file travelling through a workflow, as opposed to text. |
| **int8 / quantisation** | Compressing a model from 32-bit to 8-bit. 4× smaller, slightly less accurate. |
| **ONNX** | A model file format that many tools can run. |
| **Tokenizer** | Turns text into numbers the model can read. Must match the model exactly. |
| **umask** | A default that decides which permissions new files get. |
| **uid** | A user id. The host user is 1001; n8n in the container is 1000. They are different users. |
| **macro F1** | An accuracy measure that treats positive, neutral and negative equally. High accuracy with a rare class ignored is not good; macro F1 catches that. |

---

## 15. Where everything lives

```
./                                       the repository
├── docker-compose.yml                  how the four programs fit together
├── .env                                your settings (gitignored)
├── README.md                           overview + honest accuracy figures
├── ORCHESTRATION.md                    design decisions and n8n gotchas
├── tagasenti/
│   ├── main.py                         the AI scoring service
│   ├── Dockerfile                      how to package it
│   ├── fetch_model.sh                  downloads + verifies the weights
│   ├── tagasenti_int8.onnx             the model (537 MB, not in git)
│   └── tokenizer/                      the model's text reader
├── workflow/
│   └── review-pulso.workflow.json      ← THE FILE YOU IMPORT INTO n8n
│                                       (GENERATED - edit the generator)
├── upload/
│   ├── index.html                       the drag-and-drop page
│   └── nginx.conf                       how it talks to n8n
├── OPERATIONS.md                       ← this file
├── review-pulso-pipeline-docs-v1.1.md  the original brief, kept verbatim
│                                       (superseded - read its header note)
└── agent_journal/                      build record and tooling
    ├── README.md                       index: what each file is
    ├── JOURNAL.md                      full build record
    ├── run_all_tests.sh                run every check, one command
    ├── 01…21 scripts                   tests, generators, diagnostics
    ├── data/                           test CSVs
    ├── evidence/                       raw output behind the journal's numbers
    ├── history/                        superseded files
    └── snippets/                       the fallback CSV parser

~/Downloads/review-pulso/              YOUR REPORTS
~/n8n-backups/                         volume backups, created on demand

  ### When something is wrong, read these in order

1. The **error in the browser** — it is written to be actionable.
2. **This document**, §9 Troubleshooting.
3. `agent_journal/JOURNAL.md` — the complete record of every problem hit
   during the build and how it was actually solved. If you hit something
   familiar, it is almost certainly in there.
4. **Workflows → Executions** in n8n, for the exact node and error.

---

*Built 2026-09-30. Model: TagaSenti v4 (Apache-2.0, DOI 10.57967/hf/9620).*
