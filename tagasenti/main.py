"""
tagasenti - TagaSenti v4 INT8 ONNX inference service for the Review Pulso
n8n pipeline.

WHAT THIS IS
  A FastAPI microservice that scores Filipino/Taglish reviews as
  Negative / Neutral / Positive, using the locally-vendored INT8-quantised
  TagaSenti model (xlm-roberta-large, Apache-2.0,
  DOI 10.57967/hf/9620).

ENDPOINTS
  GET  /health        - liveness + model fingerprint (used by compose healthcheck)
  POST /predict       - {"text": "..."}            -> single result
  POST /predict_batch - {"texts": ["...", "..."]}  -> {"results": [...]}

WHY THIS FILE DIFFERS FROM THE PIPELINE DOC (v1.2, section 2)
  The doc's version scores one review at a time in a plain Python loop with no
  padding. That is correct but it is the reason a 2000-review upload would sit
  on the n8n HTTP node for most of a minute and flirt with its timeout. We
  batch and pad instead, which measured ~37 reviews/sec on the build machine
  (see agent_journal/01_local_onnx_verification.output.log). Predictions are
  mathematically identical to the loop; only the wall-clock changes.

  A measured batch_size of 8 was fastest on the build machine. Larger batches
  were SLOWER (37.3 -> 32.1 rev/s at batch 64), which is expected for a
  24-layer, 1024-hidden transformer on CPU where large padded batches thrash
  cache. BATCH_SIZE is therefore 8 by default, not 32.

  We also pass ONLY input_ids + attention_mask explicitly rather than splatting
  the tokenizer's full output dict. The graph declares exactly those two
  inputs and no token_type_ids; being explicit avoids a version-dependent
  "unexpected input" failure.

VERIFIED LABEL ORDER
  config.json id2label = {0: Negative, 1: Neutral, 2: Positive}. The LABELS
  array below is indexed by logits.argmax() and was asserted against that
  config at build time. If you ever swap the model, re-verify this - a wrong
  order silently inverts every report.

ATTRIBUTION (do not remove)
  The Review Pulso PDF footer credits TagaSenti. Apache-2.0 requires the
  attribution and licence notice be preserved in redistribution.
"""

import logging
import os
import time

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI
from pydantic import BaseModel, Field
from transformers import AutoTokenizer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODEL_PATH = os.environ.get("MODEL_PATH", "tagasenti_int8.onnx")
TOKENIZER_DIR = os.environ.get("TOKENIZER_DIR", "./tokenizer")

# Measured optimal on the build machine. See module docstring.
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "8"))

# Truncation length. The doc uses 128; XLM-R supports up to 514 positional
# embeddings so 128 is safe and keeps inference fast.
MAX_LENGTH = int(os.environ.get("MAX_LENGTH", "128"))

# Hard ceiling so a single request cannot pin the CPU forever. Chosen as
# 2000 because 2000 / 37.3 rev/s = ~54s, which sits inside n8n Node 4's
# 120s timeout with 2x headroom. n8n Node 3 also caps at 2000; this is the
# second line of defence in case that cap is ever removed.
MAX_BATCH_REVIEWS = int(os.environ.get("MAX_BATCH_REVIEWS", "2000"))

# Cap intra-op threads. Measured on the 6-core build machine at 128-token
# worst case (agent_journal/04_benchmark_threads_and_size_cap.output.log):
#   threads=2 -> 2.4 rev/s      threads=6 -> 2.6 rev/s
#   threads=4 -> 2.7 rev/s      threads=8 -> 1.5 rev/s
#   threads=12 -> 1.0 rev/s     (oversubscription is much worse than default)
# onnxruntime's default is one thread per logical core, which on this box
# means 6 and is measurably WORSE than 4. Do not raise this without
# re-running that benchmark - it makes things slower, not faster.
ORT_INTRA_OP_THREADS = int(os.environ.get("ORT_INTRA_OP_THREADS", "4"))

LABELS = ["Negative", "Neutral", "Positive"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
)
log = logging.getLogger("tagasenti")

# ---------------------------------------------------------------------------
# Model load (at import time so the container fails fast and loudly on a
# missing/corrupt model rather than 500-ing on the first request)
# ---------------------------------------------------------------------------
log.info("loading tokenizer from %s", TOKENIZER_DIR)
tok = AutoTokenizer.from_pretrained(TOKENIZER_DIR)

log.info("loading ONNX model %s (this takes a few seconds)", MODEL_PATH)
t0 = time.perf_counter()
sess_opts = ort.SessionOptions()
# Set from the benchmark, not the default. See ORT_INTRA_OP_THREADS above.
sess_opts.intra_op_num_threads = ORT_INTRA_OP_THREADS
# Threads are reused across the many small batches we issue, so keep the
# pool warm instead of paying thread-creation cost per batch.
sess_opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

sess = ort.InferenceSession(
    MODEL_PATH,
    sess_options=sess_opts,
    providers=["CPUExecutionProvider"],
)
log.info("ONNX session ready in %.2fs", time.perf_counter() - t0)
log.info("intra_op_num_threads = %d", ORT_INTRA_OP_THREADS)

graph_inputs = [i.name for i in sess.get_inputs()]
graph_outputs = [o.name for o in sess.get_outputs()]
log.info("graph inputs : %s", graph_inputs)
log.info("graph outputs: %s", graph_outputs)

if sorted(graph_inputs) != ["attention_mask", "input_ids"]:
    raise RuntimeError(
        f"Unexpected ONNX graph inputs {graph_inputs}. This service only "
        f"knows how to feed input_ids + attention_mask. The model may be the "
        f"wrong export."
    )
if "logits" not in graph_outputs:
    raise RuntimeError(f"Unexpected ONNX graph outputs {graph_outputs}; "
                       f"expected a 'logits' output.")

app = FastAPI(
    title="tagasenti",
    version="1.2.0",
    description="Filipino/Taglish sentiment analysis (TagaSenti v4, INT8 ONNX)",
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class In(BaseModel):
    text: str = Field(..., description="A single review to score.")


class Batch(BaseModel):
    texts: list[str] = Field(
        ..., description=f"1..{MAX_BATCH_REVIEWS} reviews to score.")


# ---------------------------------------------------------------------------
# Core inference
# ---------------------------------------------------------------------------
def _softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax over the last axis."""
    e = np.exp(logits - logits.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def run_many(texts: list[str], batch_size: int = BATCH_SIZE) -> list[dict]:
    """
    Score a list of texts, preserving input order.

    ORDER IS LOAD-BEARING. The n8n Code node (Node 5) zips results[i] with
    reviews[i] to build the report. If this function reordered or dropped
    anything, every comment card in the PDF would show the wrong review text
    next to the wrong sentiment. So: no sorting, no filtering, no dedup here.
    Cleaning belongs upstream in n8n Node 3, not here.
    """
    if not texts:
        return []

    if len(texts) > MAX_BATCH_REVIEWS:
        raise ValueError(
            f"{len(texts)} reviews exceeds MAX_BATCH_REVIEWS="
            f"{MAX_BATCH_REVIEWS}")

    results: list[dict] = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start:start + batch_size]
        enc = tok(
            chunk,
            padding=True,
            truncation=True,
            max_length=MAX_LENGTH,
            return_tensors="np",
        )
        ort_inputs = {
            "input_ids": enc["input_ids"].astype(np.int64),
            "attention_mask": enc["attention_mask"].astype(np.int64),
        }
        logits = sess.run(["logits"], ort_inputs)[0]
        probs = _softmax(logits)
        for row in probs:
            results.append({
                "label": LABELS[int(row.argmax())],
                "scores": {
                    lab: round(float(val), 4)
                    for lab, val in zip(LABELS, row)
                },
            })
    return results


def run_one(text: str) -> dict:
    return run_many([text])[0]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    """
    Liveness probe.

    Deliberately does NOT run inference. A healthcheck that scored a sentence
    on every poll would burn CPU and, on a shared host, could push a real
    request past its timeout just because the healthcheck fired. It reports
    that the model is loaded and what it looks like, which is all compose
    needs.
    """
    return {
        "status": "ok",
        "model": os.path.basename(MODEL_PATH),
        "labels": LABELS,
        "batch_size": BATCH_SIZE,
        "max_length": MAX_LENGTH,
        "max_batch_reviews": MAX_BATCH_REVIEWS,
    }


@app.post("/predict")
def predict(body: In):
    return run_one(body.text)


@app.post("/predict_batch")
def predict_batch(body: Batch):
    t0 = time.perf_counter()
    results = run_many(body.texts)
    el = time.perf_counter() - t0
    log.info(
        "scored %d reviews in %.2fs (%.1f rev/s)", len(body.texts), el,
        (len(body.texts) / el) if el > 0 else 0)
    return {"results": results}
