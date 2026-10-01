<!--
================================================================================
PHASE 1 ARTIFACT — SUPERSEDED IN PART. READ THIS FIRST.
================================================================================

This is the ORIGINAL SPECIFICATION, kept verbatim on purpose. Everything below
it is unchanged, including the parts that turned out to be wrong. It is not
maintained documentation and should not be followed as a build guide.

It is here because the gap between this document and what actually shipped is
the most instructive thing in the repository. Nothing below was written knowing
what the implementation would turn out to require.

WHAT CHANGED BETWEEN SPEC AND SHIP
----------------------------------
  1. Model version
     Spec says:  TagaSenti v4
     Shipped:     v6 (the release published to Hugging Face)
     Why:         v4's peak checkpoint was never saved - `save_total_limit=2`
                  pruned it during training. v6 is the reproducible, published
                  release, so it is what the INT8 export contains.

  2. Delivery mechanism  (the largest change)
     Spec says:  PDF delivered to Telegram, 16 references throughout
     Shipped:    PDF written to a local directory; the webhook returns JSON
     Why:        Telegram puts a third-party service in the middle of a
                  pipeline whose entire appeal is that it runs fully local,
                  caps documents at 50 MB, and introduces rate-limit risk on
                  every send. A JSON sidecar and the Telegram path were both
                  built, then removed.

  3. Form Trigger -> self-hosted upload page
     Spec assumes: n8n's Form Trigger
     Shipped:       a small nginx-served page that proxies to the webhook
     Why:           Form Trigger could not accept the file upload. The
                    replacement is same-origin by construction, which removes
                    the CORS failure that a cross-origin POST would hit.

  4. Node count: spec says 7, shipped pipeline has 9.

WHAT DID NOT CHANGE
-------------------
  The core thesis held: CSV in, ternary Tagalog/Taglish sentiment out, one-page
  A4 PDF, rule-based insights, no LLM anywhere in the loop. The scoring, the
  statistics, the report design, and the insight engine are as specified.

WHERE TO GO INSTEAD
-------------------
  README.md          what it does now, and the honest accuracy numbers
  ORCHESTRATION.md   why each decision was made; the three abandoned designs
  OPERATIONS.md      how to run it
  agent_journal/     the build record, in build order

  Section 1 below (model setup) is still substantially accurate. Section 2's
  tokenizer list is wrong: it names four files, two of which do not exist
  upstream and are not needed by the fast tokenizer. That mistake cost a wasted
  vendoring attempt and is logged in agent_journal/JOURNAL.md section 10.

  Step 5's Telegram credential is obsolete. The shipped pipeline uses ZERO
  credentials.
================================================================================
-->

# Review Pulso — Complete Build Documentation (v1.2 — Visual Upgrade + Telegram)
**TagaSenti × n8n: review-upload → Filipino sentiment PDF report**
Version 1.2 — 2026-09-30. Assumes: TagaSenti v4 exported to `tagasenti_int8.onnx`.
Changes from v1.1: full Telegram setup guide; redesigned report (gauge, donut with
center score, stacked bar, insight engine, comment cards). No LLM — still 7 nodes.

---

## 0. What this builds

Client uploads a CSV of reviews via web form → TagaSenti ONNX scores every review →
statistics + rule-based insights → polished one-page PDF → delivered to Telegram.

```
[Form upload CSV] → [Parse CSV] → [Clean rows] → [TagaSenti /predict_batch]
   → [Stats + insights + HTML] → [Gotenberg PDF] → [Telegram document + alert]
```

## 1. The stack

| Service    | Image / build         | Internal URL           | Role                     |
|------------|-----------------------|------------------------|--------------------------|
| n8n        | n8nio/n8n             | http://n8n:5678        | orchestration            |
| tagasenti  | build ./tagasenti     | http://tagasenti:8000  | ONNX inference           |
| gotenberg  | gotenberg/gotenberg:8 | http://gotenberg:3000  | HTML → PDF               |

```yaml
services:
  n8n:
    image: n8nio/n8n
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      - TZ=Asia/Manila
      - N8N_ENCRYPTION_KEY=<your-long-random-key>
    volumes:
      - n8n_data:/home/node/.n8n

  tagasenti:
    build: ./tagasenti
    restart: unless-stopped

  gotenberg:
    image: gotenberg/gotenberg:8
    restart: unless-stopped

volumes:
  n8n_data:
```

```bash
cd ~/n8n && docker compose up -d --build
```

## 2. The tagasenti service

### ~/n8n/tagasenti/main.py

```python
from fastapi import FastAPI
from pydantic import BaseModel
import numpy as np, onnxruntime as ort
from transformers import AutoTokenizer

LABELS = ["Negative", "Neutral", "Positive"]
tok = AutoTokenizer.from_pretrained("./tokenizer")
sess = ort.InferenceSession("tagasenti_int8.onnx", providers=["CPUExecutionProvider"])
app = FastAPI()

class In(BaseModel):
    text: str

class Batch(BaseModel):
    texts: list[str]

def run_one(text: str):
    x = tok(text, return_tensors="np", truncation=True, max_length=128)
    logits = sess.run(["logits"], dict(x))[0][0]
    p = np.exp(logits - logits.max()); p /= p.sum()
    return {"label": LABELS[int(logits.argmax())],
            "scores": {l: round(float(v), 4) for l, v in zip(LABELS, p)}}

@app.post("/predict")
def predict(body: In):
    return run_one(body.text)

@app.post("/predict_batch")
def predict_batch(body: Batch):
    return {"results": [run_one(t) for t in body.texts]}
```

### ~/n8n/tagasenti/Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn onnxruntime transformers
COPY main.py tagasenti_int8.onnx ./
COPY tokenizer/ ./tokenizer/
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Vendor the XLM-R tokenizer files in `~/n8n/tagasenti/tokenizer/`
(tokenizer.json, sentencepiece.bpe.model, special_tokens_map.json, config.json).

Test:
```bash
curl -X POST http://localhost:8000/predict -H "Content-Type: application/json" \
  -d '{"text": "Ang ganda ng quality, sulit na sulit!"}'
```

## 3. Telegram setup (first time — ~10 minutes)

1. In Telegram, open **@BotFather** → send `/newbot` → name it (e.g., `Review Pulso Bot`)
   → choose username ending in `bot` (e.g., `reviewpulso_bot`). BotFather replies with
   your **HTTP API token** (looks like `7123456789:AAHf...`). Save it — shown once.
2. Get your **chat ID**: send any message to your new bot, then open in a browser:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   → find `"chat":{"id":123456789` → that number is your chat ID (negative if it's a group).
3. In n8n → **Credentials → Add credential → Telegram API** → paste the access token.
   (Telegram credentials in n8n take only the token; chat ID goes per-node.)
4. Test: Telegram node → operation `getChat` → Chat ID = your ID → should return your
   user object. If 401, token wrong; if 400, chat ID wrong.

Notes:
- For a **group** channel later: add the bot to the group, send a message, re-run
  getUpdates, use the negative group chat ID.
- Bots can't message users who never messaged the bot first — that's why step 2 requires
  you to send a message first.

## 4. The workflow — node by node

### Node 1 — Form Trigger

- **Form Title:** `Review Pulso — Filipino Sentiment Report`
- **Form Path:** `review-pulso`
- **Authentication:** `Basic Auth`
- **Form Elements:**
  1. File — Field Label: `reviews_file` — accept `.csv`
  2. Text — Field Label: `business_name` — required
  3. Textarea — Field Label: `context` — optional

> Gotcha: the file arrives in a binary field named `reviews_file` (the Field Label),
> not `data`. If CSV upload is rejected on your n8n version, use Node 2b.

### Node 2 — Extract from File
- Operation: Extract From File — Input Binary Field: `reviews_file` — Format: CSV

### Node 2b — (Fallback) Code CSV parser
```javascript
const key = 'reviews_file';
const raw = Buffer.from(items[0].binary[key].data, 'base64').toString('utf-8');
const lines = raw.replace(/^﻿/, '').split(/\r?\n/).filter(l => l.trim());
const headers = lines[0].split(',').map(h => h.trim().toLowerCase());
const textIdx = headers.findIndex(h => ['review','text','comment','content','feedback'].includes(h));
if (textIdx === -1) throw new Error('No review/text column found. Columns: ' + headers.join(', '));
return lines.slice(1).map(l => {
  const cols = l.split(',');
  return { json: { review: cols[textIdx]?.replace(/^"|"$/g, '').trim() || '' } };
});
```

### Node 3 — Code: clean + batch into one item
```javascript
const seen = new Set();
const reviews = [];
for (const item of items) {
  const text = (item.json.review || '').toString().trim();
  if (!text || text.length < 3) continue;
  const norm = text.toLowerCase().replace(/\s+/g, ' ');
  if (seen.has(norm)) continue;
  seen.add(norm);
  reviews.push(text);
}
if (reviews.length === 0) throw new Error('No usable reviews after cleaning.');
if (reviews.length > 2000) throw new Error(`Too many reviews (${reviews.length}); cap at 2000.`);
return [{ json: {
  reviews,
  business_name: $('Node 1').first().json.business_name,
  context: $('Node 1').first().json.context || ''
}}];
```

### Node 4 — HTTP Request → TagaSenti
- POST `http://tagasenti:8000/predict_batch` — Body (JSON):
```json
{ "texts": {{ JSON.stringify($json.reviews) }} }
```
- Timeout 120000 ms; Retry ON ×3

### Node 5 — Code: stats + insight engine + HTML (complete)

```javascript
// ============ 1. AGGREGATE ============
const reviews = $('Node 3').first().json.reviews;
const meta    = $('Node 3').first().json;
const res     = $input.first().json.results;
const n       = res.length;

const counts = { Negative: 0, Neutral: 0, Positive: 0 };
const rows = [];
res.forEach((r, i) => {
  counts[r.label]++;
  rows.push({ text: reviews[i], label: r.label, score: r.scores[r.label] });
});

const pct = k => +((counts[k] / n) * 100).toFixed(1);
const net = +(((counts.Positive - counts.Negative) / n) * 100).toFixed(1);
const conf = l => rows.filter(r => r.label === l)
  .reduce((a, r) => a + r.score, 0) / Math.max(1, counts[l]);

const top = (label, k = 5) => rows.filter(r => r.label === label)
  .sort((a, b) => b.score - a.score).slice(0, k);

const p = { negative: pct('Negative'), neutral: pct('Neutral'), positive: pct('Positive') };

// ============ 2. INSIGHT ENGINE (rule-based, no LLM) ============
const verdict = net >= 40 ? 'Excellent — customers love you'
              : net >= 15 ? 'Good — generally favorable'
              : net >= 0  ? 'Mixed — room to improve'
              : net >= -15 ? 'At risk — negatives gaining'
              : 'Critical — act this week';

const insights = [];
insights.push(`<b>${verdict}.</b> Net sentiment score of <b>${net}</b> across ${n} reviews.`);
if (counts.Positive > 0)
  insights.push(`${p.positive}% of reviewers expressed positive sentiment${counts.Negative === 0 ? ' — with zero negative reviews.' : '.'}`);
if (counts.Negative > 0) {
  const highConfNeg = rows.filter(r => r.label === 'Negative' && r.score >= 0.85).length;
  if (highConfNeg > 0)
    insights.push(`⚠️ <b>${highConfNeg} negative review${highConfNeg > 1 ? 's' : ''} with ≥85% model confidence</b> — treat as confirmed complaints and respond first.`);
  if (conf('Negative') < 0.70)
    insights.push(`Negative-review confidence is low (${conf('Negative').toFixed(2)}) — ambiguous or mixed-language text; sample-check before acting.`);
}
if (p.neutral > 45)
  insights.push(`High neutral share (${p.neutral}%) often means short/low-effort reviews — consider prompting customers for more detailed feedback.`);
if (counts.Positive > 0 && counts.Negative > 0) {
  const ratio = (counts.Positive / counts.Negative).toFixed(1);
  insights.push(`Positivity ratio is <b>${ratio}:1</b> (positive to negative).`);
}

const recs = [];
if (counts.Negative > 0) recs.push(`Address the leading complaint directly — "${top('Negative',1)[0].text.slice(0,90)}" appears to be the strongest negative signal.`);
if (counts.Positive > 0) recs.push(`Amplify what works — customers explicitly praised "${top('Positive',1)[0].text.slice(0,90)}". Feature it in marketing.`);
recs.push(`Reply to every high-confidence negative review within 24h; public, empathetic replies recover visible sentiment faster than silent fixes.`);
recs.push(`Re-run this report monthly and track the net score trend — direction matters more than any single reading.`);

// ============ 3. HTML ============
const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;');
const gaugePos = ((net + 100) / 2);            // 0..100 for marker
const donut = `conic-gradient(#e05252 0 ${p.negative}%, #b7bec4 ${p.negative}% ${p.negative+p.neutral}%, #2fae6e ${p.negative+p.neutral}% 100%)`;
const badge = l => ({Negative:'#e05252', Neutral:'#8d979e', Positive:'#2fae6e'}[l]);
const card = r => `
  <div class="ccard">
    <span class="cbadge" style="background:${badge(r.label)}">${r.label}</span>
    <span class="ctext">"${esc(r.text)}"</span>
    <div class="cbar"><div style="width:${(r.score*100).toFixed(0)}%;background:${badge(r.label)}"></div></div>
    <span class="cconf">${(r.score*100).toFixed(0)}% confidence</span>
  </div>`;
const insightLi = insights.map(i => `<li>${i}</li>`).join('');
const recLi = recs.map((r,i) => `<li><b>${i+1}.</b> ${esc(r)}</li>`).join('');

const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@page { size: A4; margin: 0; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', Helvetica, Arial, sans-serif; color: #2b2f33; font-size: 10.5pt; }
.page { width: 210mm; min-height: 297mm; padding: 14mm 14mm 12mm; }
.header { background: linear-gradient(135deg,#1d3557 0%,#2f6b8f 100%); color: #fff;
  border-radius: 4mm; padding: 6mm 7mm; margin-bottom: 5mm; }
.header h1 { font-size: 19pt; font-weight: 700; letter-spacing: .2px; }
.header .sub { opacity: .85; font-size: 9.5pt; margin-top: 1.5mm; }
.verdict { display:inline-block; margin-top: 2.5mm; background: rgba(255,255,255,.15);
  border: 1px solid rgba(255,255,255,.35); border-radius: 10mm; padding: 1.2mm 4mm; font-size: 9.5pt; font-weight:600; }
.kpis { display: flex; gap: 3.5mm; margin: 5mm 0; }
.kpi { flex: 1; background: #fff; border: 1px solid #e3e7ea; border-top: 3px solid #ccc;
  border-radius: 2.5mm; padding: 3.5mm 2mm; text-align: center;
  box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.kpi .v { font-size: 17pt; font-weight: 800; }
.kpi .l { font-size: 8.5pt; color: #6a737b; text-transform: uppercase; letter-spacing: .5px; }
.kpi.pos { border-top-color:#2fae6e; } .kpi.pos .v { color:#2fae6e; }
.kpi.neu { border-top-color:#8d979e; } .kpi.neu .v { color:#8d979e; }
.kpi.neg { border-top-color:#e05252; } .kpi.neg .v { color:#e05252; }
.kpi.net { border-top-color:#1d3557; } .kpi.net .v { color:#1d3557; }
.grid { display: flex; gap: 5mm; margin-bottom: 5mm; }
.col { flex: 1; }
.panel { background:#fff; border:1px solid #e3e7ea; border-radius:3mm; padding: 4mm; }
.panel h2 { font-size: 11pt; color:#1d3557; margin-bottom: 3mm; }
.donut-wrap { display:flex; align-items:center; gap:5mm; }
.donut { width: 40mm; height: 40mm; border-radius: 50%; position: relative; flex-shrink:0; }
.donut .hole { position:absolute; inset: 11mm; background:#fff; border-radius:50%;
  display:flex; flex-direction:column; align-items:center; justify-content:center; }
.donut .hole .big { font-size: 14pt; font-weight: 800; color:#1d3557; }
.donut .hole .small { font-size: 7.5pt; color:#8d979e; }
.legend div { margin-bottom: 1.8mm; font-size: 9pt; }
.dot { display:inline-block; width: 3mm; height: 3mm; border-radius: 50%; margin-right: 2mm; vertical-align: -0.3mm; }
.stack { height: 7mm; border-radius: 2mm; overflow: hidden; display: flex; margin: 2mm 0 1.5mm; }
.stack div { height: 100%; }
.stacklabels { display:flex; justify-content: space-between; font-size: 8pt; color:#6a737b; }
.gauge { position: relative; height: 6mm; border-radius: 3mm; margin-top: 2mm;
  background: linear-gradient(90deg,#e05252 0%,#d9b23a 45%,#d9b23a 55%,#2fae6e 100%); }
.gauge .marker { position: absolute; top: -1.5mm; width: 1.6mm; height: 9mm; background:#1d3557;
  border-radius: 1mm; left: calc(${gaugePos}% - 0.8mm); }
.gauge-labels { display:flex; justify-content:space-between; font-size:7.5pt; color:#8d979e; margin-top:1mm; }
ul.insights { list-style: none; }
ul.insights li { padding: 2mm 0 2mm 6mm; position: relative; border-bottom: 1px dashed #eceff1; font-size: 9.5pt; line-height: 1.45; }
ul.insights li:before { content: "▸"; position: absolute; left: 0; color: #2f6b8f; }
ul.insights li:last-child { border-bottom: none; }
ol.recs { padding-left: 5mm; }
ol.recs li { margin-bottom: 2mm; font-size: 9.5pt; line-height: 1.45; }
.ccard { border: 1px solid #e9edf0; border-left: 3px solid #ccc; border-radius: 2mm;
  padding: 2.5mm 3mm; margin-bottom: 2.5mm; background: #fbfcfd; }
.cbadge { color:#fff; font-size: 7.5pt; font-weight:700; border-radius: 8mm; padding: .8mm 2.6mm; }
.ctext { display:block; font-size: 9pt; margin: 1.5mm 0; font-style: italic; color:#39424a; }
.cbar { height: 1.6mm; background:#eef1f3; border-radius: 1mm; overflow: hidden; }
.cbar div { height: 100%; border-radius: 1mm; }
.cconf { font-size: 7.5pt; color:#9aa4ac; display:block; margin-top: 1mm; }
h3.sec { font-size: 11pt; color:#1d3557; margin: 4mm 0 2.5mm; border-left: 3px solid #2f6b8f; padding-left: 2.5mm; }
.footer { margin-top: 5mm; padding-top: 2.5mm; border-top: 1px solid #e6eaed;
  font-size: 7.5pt; color: #9aa4ac; display:flex; justify-content: space-between; }
</style></head><body><div class="page">

<div class="header">
  <h1>Review Pulso — Sentiment Report</h1>
  <div class="sub"><b>${esc(meta.business_name)}</b>${meta.context ? ' · ' + esc(meta.context) : ''} · ${n} reviews analyzed · ${new Date().toLocaleDateString('en-PH',{timeZone:'Asia/Manila',day:'numeric',month:'long',year:'numeric'})}</div>
  <div class="verdict">${verdict}</div>
</div>

<div class="kpis">
  <div class="kpi pos"><div class="v">${p.positive}%</div><div class="l">Positive</div></div>
  <div class="kpi neu"><div class="v">${p.neutral}%</div><div class="l">Neutral</div></div>
  <div class="kpi neg"><div class="v">${p.negative}%</div><div class="l">Negative</div></div>
  <div class="kpi net"><div class="v">${net}</div><div class="l">Net score</div></div>
</div>

<div class="grid">
  <div class="col panel">
    <h2>Sentiment mix</h2>
    <div class="donut-wrap">
      <div class="donut" style="background:${donut}">
        <div class="hole"><div class="big">${net}</div><div class="small">NET SCORE</div></div>
      </div>
      <div class="legend">
        <div><span class="dot" style="background:#2fae6e"></span>Positive ${p.positive}%</div>
        <div><span class="dot" style="background:#b7bec4"></span>Neutral ${p.neutral}%</div>
        <div><span class="dot" style="background:#e05252"></span>Negative ${p.negative}%</div>
      </div>
    </div>
    <div class="stack">
      <div style="width:${p.positive}%;background:#2fae6e"></div>
      <div style="width:${p.neutral}%;background:#b7bec4"></div>
      <div style="width:${p.negative}%;background:#e05252"></div>
    </div>
    <div class="stacklabels"><span>▲ positive</span><span>negative ▼</span></div>
  </div>
  <div class="col panel">
    <h2>Net sentiment gauge</h2>
    <div class="gauge"><div class="marker"></div></div>
    <div class="gauge-labels"><span>-100 (all negative)</span><span>0</span><span>+100 (all positive)</span></div>
    <h2 style="margin-top:4mm">Key insights</h2>
    <ul class="insights">${insightLi}</ul>
  </div>
</div>

<h3 class="sec">⚠️ Needs attention — top negative reviews</h3>
${top('Negative').map(card).join('') || '<p style="color:#8d979e">No negative reviews in this batch. 🎉</p>'}

<h3 class="sec">💚 What's working — top positive reviews</h3>
${top('Positive').map(card).join('') || '<p style="color:#8d979e">No positive reviews in this batch.</p>'}

<h3 class="sec">Recommended actions</h3>
<ol class="recs">${recLi}</ol>

<div class="footer">
  <span>Powered by <b>TagaSenti</b> · Apache 2.0 · DOI 10.57967/hf/9620</span>
  <span>Generated by Review Pulso v1.2</span>
</div>

</div></body></html>`;

return [{ json: { stats: { total: n, pct: p, net_score: net, counts }, insights, verdict },
          binary: { data: await this.helpers.prepareBinaryData(
            Buffer.from(html, 'utf-8'), 'index.html', 'text/html') } }];
```

### Node 6 — HTTP Request → Gotenberg
- POST `http://gotenberg:3000/forms/chromium/convert/html`
- Headers: `Gotenberg-Output-Filename` = `review-pulso-report.pdf`
- Body Content Type: `Form-Data`
- Body Parameters (two):
  1. **Parameter Type: Form Binary Data** — Name: `files` — Input Data Field Name: `data`
     (Gotenberg's form field is `files`; the binary's own fileName `index.html` travels with it)
  2. **Parameter Type: Form** — Name: `printBackground` — Value: `true`
     (⚠️ REQUIRED — the donut, gauge and header gradient are CSS *backgrounds*; Chromium's
     print path drops backgrounds unless printBackground=true, and your charts come out white)
- Response Format: File — Output Binary Field: `data` — Timeout 60000 ms, Retry ×2

### Node 7 — Telegram
- **Send Document** — Credential: your Telegram bot — **Chat ID:** your ID —
  **Binary Field:** `data` — Caption:
  `📊 ${$json.verdict}\n${$('Node 1').first().json.business_name}: ${$json.stats.total} reviews · Pos ${$json.stats.pct.positive}% / Neu ${$json.stats.pct.neutral}% / Neg ${$json.stats.pct.negative}% · Net ${$json.stats.net_score}`
- **Send Message** (ops log) — Chat ID: your ID — Text:
  `New Review Pulso report for ${$('Node 1').first().json.business_name} (${$json.stats.total} reviews). Net ${$json.stats.net_score}.`

## 5. Workflow settings
- Error workflow (Telegram: `❌ Review Pulso failed: {{ $json.error.message }}`)
- Timeout: 300000 ms — Save + Activate

## 6. Validation gate
~200 REAL reviews → label yourself → compare labels → ≥75% macro F1 → demos.

## 7. Testing checklist
- [ ] curl tagasenti → Tagalog positive → Positive
- [ ] Telegram getChat test returns your user object
- [ ] 5-row test CSV → Node 5 emits binary index.html
- [ ] Node 6 → PDF binary; Node 7 → document + caption in Telegram
- [ ] Donut shows three colors; gauge marker sits at correct position; cards render
- [ ] Re-run same CSV → byte-identical PDF

Test CSV:
```csv
review
"Ang bilis ng delivery at ang sarap ng food!"
"Medyo matagal ang paghihintay at malamig na ang ulam."
"Okay lang naman, hindi maganda hindi rin pangit."
"Grabe ang panget ng packaging, basag na basag."
"Sulit na sulit, babalik ulit ako dito pramis"
```

## 8. Troubleshooting
| Symptom | Fix |
|---|---|
| Telegram 401 | Token wrong — re-copy from BotFather |
| Telegram 400 / chat not found | Chat ID wrong — redo getUpdates step |
| No binary field `data` after form | Use `reviews_file` as Input Binary Field |
| Gotenberg 400 multipart | Body not form-data | Body Content Type = Form-Data with a Form Binary Data parameter |
| Gotenberg "at least one HTML file" | Binary filename must be `index.html` |
| Gauge marker missing/offset | Ensure `gaugePos` computed from net in -100..100 |
| Charts/gauge white or missing | `printBackground` not sent | Add form field `printBackground=true` in Node 6 (Chromium drops CSS backgrounds in print otherwise) |

## 9. Security
- Form: Basic Auth. Ports 8000/3000 stay unmapped. Keep TagaSenti footer attribution.
