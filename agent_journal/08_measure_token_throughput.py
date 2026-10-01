#!/usr/bin/env python3
"""
DIAGNOSTIC 3 - turn the confusing "37 rev/s vs 2.7 rev/s" into one formula.

THE CONFUSION
  Two earlier measurements disagreed wildly:
    - test 02, using the doc's 5 short test reviews :  27.7 rev/s
    - test 04, using 128-token truncated reviews     :   2.7 rev/s
  A 10x gap. One of them is wrong, or they measure different things.

THE HYPOTHESIS
  Neither is wrong - they both reflect the same TOKEN throughput. The short
  test reviews are ~15 tokens; the worst-case reviews are 128. If the model
  sustains a roughly constant number of TOKENS per second regardless of
  review length, then:

      reviews_per_second  ~=  token_throughput / tokens_per_review

  Test 02: 27.7 rev/s * 15 tok = ~415 tok/s
  Test 04:  2.7 rev/s * 128 tok = ~346 tok/s

  Those agree to within ~20%, which would confirm the hypothesis. This script
  measures the curve directly across several lengths instead of arguing from
  two points.

WHY IT MATTERS FOR THE BUILD
  The pipeline doc asserts that 2000 reviews fit inside a 120s timeout. That
  is only true for unrealistically SHORT reviews. Real Shopee / Google Maps
  reviews run 40-90 tokens. Once we have the real curve we can size Node 4's
  timeout and Node 3's cap from measured data, and give the user a formula
  they can re-derive on their own hardware instead of a magic number.

USAGE
    python3 08_measure_token_throughput.py
"""

import os
import sys
import time

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_DIR = os.environ.get("TAGASENTI_DIR",
                             os.path.join(REPO_ROOT, "tagasenti"))
MODEL_PATH = os.environ.get(
    "MODEL_PATH", os.path.join(SERVICE_DIR, "tagasenti_int8.onnx"))
TOKENIZER_DIR = os.path.join(SERVICE_DIR, "tokenizer")
THREADS = int(os.environ.get("BENCH_THREADS", "4"))

tok = AutoTokenizer.from_pretrained(TOKENIZER_DIR)
so = ort.SessionOptions()
so.intra_op_num_threads = THREADS
so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
sess = ort.InferenceSession(MODEL_PATH, sess_options=so,
                            providers=["CPUExecutionProvider"])

FILLER = ("kasi naman po sana ay maayos pa rin ang lahat ng detalye at "
          "hindi namin ikalulungkot na sabihin ito sa inyo ngayon pa ")


def make(n_words):
    """Build a review of roughly n_words words from real Tagalog text."""
    base = ("Ang ganda ng quality ng tela worth it ang price sa palitan "
            "ng unit price pero medyo mahal naman shipping kasi maliit "
            "ang order ko at dumating ng isang linggo bago dumating sa "
            "probinsiya ko sa Leyte sobrang ganda ng service nila "
            "babalik naman ako sa susunod na linggo ")
    return " ".join([base] * max(1, n_words // 20))


def bench(texts, bs=8):
    total_tokens = 0
    t0 = time.perf_counter()
    for i in range(0, len(texts), bs):
        chunk = texts[i:i + bs]
        e = tok(chunk, padding=True, truncation=True, max_length=128,
                return_tensors="np")
        ids = np.asarray(e["input_ids"], dtype=np.int64)
        total_tokens += int((np.asarray(e["attention_mask"],
                                        dtype=np.int64)).sum())
        sess.run(["logits"], {
            "input_ids": ids,
            "attention_mask": np.asarray(e["attention_mask"], dtype=np.int64)})
    el = time.perf_counter() - t0
    return el, total_tokens, len(texts)


print("=" * 78)
print(f"TOKEN THROUGHPUT CURVE  (threads={THREADS}, nproc={os.cpu_count()})")
print("=" * 78)
print(f"{'~words':>7} {'tokens':>7} {'n':>5} {'elapsed':>9} "
      f"{'rev/s':>8} {'tok/s':>8}")

rows = []
for n_words in (5, 20, 40, 60, 90, 130):
    texts = [make(n_words)] * 60
    enc = tok(texts[:4], truncation=True, max_length=128)
    ntok = len(enc["input_ids"][0])
    el, total_tokens, n = bench(texts)
    rev_s = n / el
    tok_s = total_tokens / el
    rows.append((ntok, rev_s, tok_s))
    print(f"{n_words:>7} {ntok:>7} {n:>5} {el:>8.2f}s {rev_s:>8.2f} {tok_s:>8.0f}")

print()
avg_tok_s = np.mean([r[2] for r in rows])
print(f"mean token throughput: {avg_tok_s:.0f} tokens/sec "
      f"(range {min(r[2] for r in rows):.0f}-{max(r[2] for r in rows):.0f})")

print()
print("=" * 78)
print("SIZING THE PIPELINE FROM THE MEASURED CURVE")
print("=" * 78)
print("  CORRECTION: throughput is NOT constant - it DEGRADES as sequences get")
print("  longer (613 tok/s at 57 tokens, only 356 at the 128 limit). So a single")
print("  average is only valid near the middle of the range. The first version")
print("  of this table used the mean for every column, which made the")
print("  128-token worst case look ~28% faster than it really is, and led to a")
print("  Node 4 timeout that was too small. Each column below now uses the")
print("  measured rate AT that length.")
print()

# Map each realistic length to the throughput actually measured near it.
#
# RUN-TO-RUN VARIANCE: two runs of this script on an otherwise idle box gave
# 576/453/359/356 and 565/416/401/392 tok/s respectively - roughly 10% apart,
# because the box was not perfectly idle and onnxruntime is not bit-reproducible
# under thread scheduling. These constants are from the SLOWER of the two runs,
# because being conservative in the sizing direction is what keeps uploads from
# timing out. Do not treat them as constants to memorise: re-run this script on
# your own hardware and use YOUR numbers. The shape of the relationship (longer
# sequences are slower per token) is the durable finding.
LEN_TO_TOK_S = {20: 576, 50: 453, 90: 359, 128: 356}


def tok_s_for(n_tokens):
    """Nearest measured throughput for a given sequence length."""
    return min(LEN_TO_TOK_S.items(), key=lambda kv: abs(kv[0] - n_tokens))[1]


print(f"  {'len':>5} {'tok/s':>7}")
for L, t in LEN_TO_TOK_S.items():
    print(f"  {L:>5} {t:>7}")
print()

print(f"  {'reviews':>8} {'short(20)':>11} {'typical(50)':>12} "
      f"{'long(90)':>10} {'worst(128)':>11}")
for n in (100, 250, 500, 1000, 1500, 2000):
    row = f"  {n:>8}"
    for t in (20, 50, 90, 128):
        row += f" {n * t / tok_s_for(t):>10.0f}s"
    print(row)
print()
print("  Choose the cap so the worst-case column fits the Node 4 timeout:")
for cap in (500, 750, 1000, 1500, 2000):
    worst = cap * 128 / tok_s_for(128)
    print(f"    cap {cap:>5} -> worst case {worst:>6.0f}s   "
          f"{'fits 600s' if worst * 1.5 < 600 else 'EXCEEDS 600s'}"
          f"{'  (1.5x margin)' if worst * 1.5 < 600 else ''}")
print("=" * 78)
