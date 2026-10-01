#!/usr/bin/env python3
"""
DIAGNOSTIC 2 - pick ORT_INTRA_OP_THREADS and the review cap.

WHY
  Test 02 T8 measured 2000 reviews in 72.1s at intra_op_num_threads=4, which
  is uncomfortably close to n8n Node 4's 120s timeout - only 1.6x headroom.
  Diagnostic 03 D4 proved that thread count does NOT change predictions
  (drift 0.000000 at 1/2/4/8 threads), so we are free to use whatever thread
  count is fastest. This measures speed per thread count and derives a cap
  that leaves a safe margin instead of a lucky one.

SAFETY MARGIN
  We do not size for the measured time. Client uploads vary: a CSV with
  128-token reviews is worst case, a machine that is also running Gotenberg
  is slower than this idle benchmark, and a cold page cache makes the first
  batch slow. We size for 2x the measured time, then round DOWN to a round
  number that a human can remember and a client can be told.

USAGE
    python3 04_benchmark_threads_and_size_cap.py
"""

import os
import statistics
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

tok = AutoTokenizer.from_pretrained(TOKENIZER_DIR)

# WORST-CASE corpus: reviews deliberately padded out past the 128-token
# truncation. Using the doc's 5 short test rows here would flatter the numbers
# badly - real Google Maps / Shopee reviews are much longer, and a truncated
# 128-token row is the most expensive row the model can be asked to compute.
CLAUSE = ("kasi naman po sana ay maayos pa rin ang lahat ng detalye at "
          "hindi namin ikalulungkot na sabihin ito sa inyo ngayon pa ")


def _long(a, b, c):
    """Build a review long enough to be truncated at 128 tokens."""
    return f"{a} {CLAUSE * 6}{b} {CLAUSE * 6}{c}"


worst_case = [
    _long("Ang bilis ng delivery at ang sarap ng food",
          "pero medyo mahal ang shipping fee",
          "babalik naman ako sa susunod na linggo"),
    _long("Grabe ang panget ng packaging, basag na basag",
          "may butas pa sa gilid ng kaha",
          "nagbibigay pa ng babala sa staff pero hanggang ngayon"),
    _long("Medyo matagal ang paghihintay para sa isang bowl",
          "perookay lang naman, masarap yung lasa",
          "friendly at marunong yung tindera sa suggestions"),
] * 60
N = len(worst_case)

# Confirm we really are at worst case, so the sizing means something.
enc = tok(worst_case[:8], truncation=True, max_length=128)
lens = [len(x) for x in enc["input_ids"]]
print("=" * 78)
print("THREAD BENCHMARK + CAP SIZING")
print("=" * 78)
print(f"corpus size      : {N} reviews")
print(f"token lengths    : min={min(lens)} max={max(lens)} (cap=128)")
if max(lens) < 128:
    print(f"  WARNING: corpus maxes at {max(lens)} tokens, NOT the 128 cap.")
    print("  The sizing below would be optimistic. Lengthen the sample texts.")
else:
    print("  Confirmed: every row hits the 128-token cap. This is worst case.")
print()

results = {}
for th in (2, 4, 6, 8, 12, 16):
    so = ort.SessionOptions()
    so.intra_op_num_threads = th
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    s = ort.InferenceSession(MODEL_PATH, sess_options=so,
                             providers=["CPUExecutionProvider"])
    # warm up once, so we time steady state not lazy init
    s.run(["logits"], {
        "input_ids": np.asarray(enc["input_ids"], dtype=np.int64),
        "attention_mask": np.asarray(enc["attention_mask"], dtype=np.int64)})

    t0 = time.perf_counter()
    for i in range(0, N, 8):
        chunk = worst_case[i:i + 8]
        e = tok(chunk, padding=True, truncation=True, max_length=128,
                return_tensors="np")
        s.run(["logits"], {
            "input_ids": np.asarray(e["input_ids"], dtype=np.int64),
            "attention_mask": np.asarray(e["attention_mask"], dtype=np.int64)})
    el = time.perf_counter() - t0
    results[th] = N / el
    print(f"  threads={th:<3} {N} reviews in {el:6.2f}s = {N / el:6.1f} rev/s")
    del s

best_th = max(results, key=results.get)
best_rate = results[best_th]
print(f"\n  FASTEST: threads={best_th} at {best_rate:.1f} rev/s")
print(f"  (n8n Node 4 timeout is 120s)")

# ---------------------------------------------------------------------------
print("\nCAP SIZING (worst case, 2x safety margin):")
print(f"  {'cap':>6} {'est @fastest':>14} {'+2x margin':>12}   fits 120s?")
chosen = None
for cap in (200, 300, 500, 750, 1000, 1500, 2000):
    est = cap / best_rate
    fits = est * 2 < 120
    print(f"  {cap:>6} {est:>11.1f}s {est * 2:>11.1f}s   "
          f"{'YES' if fits else 'no'}")
    if fits and chosen is None:
        chosen = cap

print(f"\n  RECOMMENDED MAX_BATCH_REVIEWS: {chosen}")
print(f"    -> estimated {chosen / best_rate:.0f}s, with 2x margin "
      f"{chosen / best_rate * 2:.0f}s, inside 120s.")
print(f"  RECOMMENDED ORT_INTRA_OP_THREADS: {best_th}")
print()
print("  NOTE on CPU count:")
print(f"    nproc = {os.cpu_count()}")
print("    Do not set intra_op beyond the physical core count; oversubscribing")
print("    makes it slower AND starves the Gotenberg container that shares")
print("    the host. If nproc is large, cap at ~half of it.")
print("=" * 78)
