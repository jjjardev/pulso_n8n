#!/usr/bin/env python3
"""
STEP 1 — Local ONNX verification + throughput benchmark.

PURPOSE
  This is the single most important verification step in the whole build. It
  answers three questions BEFORE we build any Docker service or n8n node:

    Q1. Does tagasenti_int8.onnx load and produce sane predictions on Tagalog?
        (A model that loads but inverts its labels would silently poison every
         report the client ever sees, so this is checked FIRST.)

    Q2. Is the label order in the doc's LABELS array correct?
        We compare against the model's own config.json id2label.

    Q3. How fast is inference on THIS machine's CPU?
        This decides the review cap in n8n Node 3 and the HTTP timeout in
        Node 4. Guessing here is what causes flaky timeouts in production.

USAGE
    python3 01_local_onnx_verification.py

EXIT CODES
    0 = all spot-checks passed (pipeline is safe to continue)
    1 = a spot-check failed (STOP and investigate before going further)
"""

import glob
import json
import os
import sys
import time

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

# ----------------------------------------------------------------------------
# PATHS - these were discovered by inspecting the real filesystem, not guessed.
# See journal entry 00 for the `find` commands that produced these locations.
# ----------------------------------------------------------------------------
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The model now lives in tagasenti/ (fetched by tagasenti/fetch_model.sh).
# It used to sit at the repo root, which meant a second 537 MB copy in every
# clone-adjacent directory. Check both so an old checkout still verifies.
ONNX_CANDIDATES = [
    os.path.join(PROJECT_DIR, "tagasenti", "tagasenti_int8.onnx"),
    os.path.join(PROJECT_DIR, "tagasenti_int8.onnx"),
]
ONNX_GLOB = next((p for p in ONNX_CANDIDATES if os.path.exists(p)),
                 ONNX_CANDIDATES[0])
HF_CACHE_SNAPSHOT = os.path.expanduser(
    "~/.cache/huggingface/hub/models--jjjardev--tagasenti_model/snapshots"
)

# The label array from the pipeline doc (v1.2, section 2). We VERIFY this
# rather than trust it - see Q2 below.
DOC_LABELS = ["Negative", "Neutral", "Positive"]

# Spot-checks taken verbatim from the user's own working Colab script
# (quanize_tagasenti/tagasenti_onnx_colab/02_use_tagasenti_int8_colab.py).
# The 4th entry is a deliberately ADVERSARIAL sarcasm example that the model
# is documented to get WRONG (it says the model classifies it as Negative).
# We keep it so behaviour is recorded, but we do not fail the build on it.
SPOT_CHECKS = [
    ("Ang ganda ng quality ng tela, worth it ang price!", "Positive"),
    ("Wala pa ring update ang order ko hanggang ngayon.", "Negative"),
    ("Sa aking palagay, hindi naman ito gaanong importante.", "Neutral"),
    ("Ang bait mo naman, pinagbigyan mo ako sa wakas.", "Negative"),  # sarcasm
]
SPOT_CHECKS_MUST_PASS = slice(0, 3)   # first 3 are the real pass/fail gate
SPOT_CHECKS_ADVERSARIAL = slice(3, 4)  # 4th is recorded, never fatal

# Max sequence length. The doc uses 128. XLM-R's positional embedding table
# supports 514, so 128 is a safe truncation. We match the doc exactly so that
# local results and container results are comparable.
MAX_LENGTH = 128


def find_onnx():
    """Resolve the ONNX path, with a glob fallback and a clear error."""
    if os.path.exists(ONNX_GLOB):
        return ONNX_GLOB
    hits = glob.glob(os.path.expanduser("~/Desktop/**/tagasenti_int8.onnx"),
                     recursive=True)
    if hits:
        return hits[0]
    sys.exit("FATAL: tagasenti_int8.onnx not found under ~/Desktop.")


def find_tokenizer_dir():
    """
    Resolve the tokenizer directory.

    IMPORTANT FINDING (journal entry 00): the pipeline doc section 2 instructs
    you to vendor FOUR tokenizer files, including `sentencepiece.bpe.model`
    and `special_tokens_map.json`. NEITHER OF THOSE EXIST for this model.
    TagaSenti ships a FAST tokenizer, which is fully self-contained in
    `tokenizer.json`. Only three files are actually required. If you go
    looking for the .bpe.model file you will waste an hour.
    """
    snaps = glob.glob(os.path.join(HF_CACHE_SNAPSHOT, "*"))
    if not snaps:
        sys.exit("FATAL: no HuggingFace snapshot for tagasenti_model found. "
                 "Run: huggingface-cli download jjjardev/tagasenti_model")
    snap = snaps[0]
    required = ["tokenizer.json", "tokenizer_config.json"]
    for fn in required:
        if not os.path.exists(os.path.join(snap, fn)):
            sys.exit(f"FATAL: {fn} missing from {snap}")
    return snap


def check_label_order(snapshot_dir):
    """
    Q2: Confirm the doc's LABELS array matches the model's id2label.

    WHY THIS MATTERS: main.py does `LABELS[int(logits.argmax())]`. If the
    model's real id2label were e.g. [Positive, Neutral, Negative], every
    positive review in every client report would be labelled Negative. That
    is a silent, total failure of the product - no error, no exception, just
    confidently wrong reports. So we assert it here, loudly.
    """
    cfg_path = os.path.join(snapshot_dir, "config.json")
    id2label = None
    if os.path.exists(cfg_path):
        with open(cfg_path) as fh:
            cfg = json.load(fh)
        id2label = {int(k): v for k, v in cfg["id2label"].items()}

    derived = [id2label[i] for i in sorted(id2label)] if id2label else None
    print("Q2. LABEL ORDER CHECK")
    print(f"    config.json id2label : {id2label}")
    print(f"    derived order        : {derived}")
    print(f"    doc's LABELS array   : {DOC_LABELS}")
    ok = derived == DOC_LABELS
    print(f"    verdict              : {'MATCH - doc is correct' if ok else 'MISMATCH - FIX main.py'}")
    print()
    return ok


def predict_batched(sess, tok, texts, batch_size=16):
    """
    Batched inference, with padding.

    WHY THIS DIFFERS FROM THE DOC: the doc's main.py calls run_one() in a
    plain loop with no padding. That is correct but slow, and it is the reason
    a 2000-review upload would blow past the doc's 120s HTTP timeout. We
    batch and pad instead. Batching is strictly faster; padding is handled
    correctly by the attention_mask, so results are identical to the loop.
    """
    all_labels, all_probs = [], []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        enc = tok(chunk, padding=True, truncation=True,
                  max_length=MAX_LENGTH, return_tensors="np")
        # NOTE: we pass ONLY the two inputs the graph declares. See journal
        # entry 00 - the graph has exactly input_ids + attention_mask and NO
        # token_type_ids. Passing the full tokenizer dict (as the doc's main.py
        # does via dict(x)) risks an unexpected-input error on some
        # transformers versions.
        ort_inputs = {
            "input_ids": enc["input_ids"].astype(np.int64),
            "attention_mask": enc["attention_mask"].astype(np.int64),
        }
        logits = sess.run(["logits"], ort_inputs)[0]
        e = np.exp(logits - logits.max(axis=-1, keepdims=True))
        probs = e / e.sum(axis=-1, keepdims=True)
        all_labels.extend([DOC_LABELS[p] for p in probs.argmax(axis=-1)])
        all_probs.extend(probs.tolist())
    return all_labels, all_probs


def main():
    print("=" * 78)
    print("TagaSenti INT8 ONNX - Local Verification & Benchmark")
    print("=" * 78)
    print(f"python      : {sys.version.split()[0]}")
    print(f"onnxruntime : {ort.__version__}")
    print(f"numpy       : {np.__version__}")
    print()

    onnx_path = find_onnx()
    size_mb = os.path.getsize(onnx_path) / 1e6
    print(f"Q1. MODEL LOAD")
    print(f"    path  : {onnx_path}")
    print(f"    size  : {size_mb:.1f} MB")

    t0 = time.perf_counter()
    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    load_s = time.perf_counter() - t0
    print(f"    load  : {load_s:.2f}s  (this is container start-up cost too)")
    print(f"    inputs : {[(i.name, i.type) for i in sess.get_inputs()]}")
    print(f"    outputs: {[(o.name, o.type) for o in sess.get_outputs()]}")
    print()

    tok_dir = find_tokenizer_dir()
    tok = AutoTokenizer.from_pretrained(tok_dir)
    print(f"    tokenizer: {tok_dir}")
    print(f"    tokenizer class: {type(tok).__name__}  (fast tokenizer = "
          f"self-contained in tokenizer.json)")
    print()

    labels_ok = check_label_order(tok_dir)

    # ---- Q1: spot-checks -------------------------------------------------
    print("Q1. SPOT-CHECKS (Tagalog / Taglish)")
    texts = [t for t, _ in SPOT_CHECKS]
    expected = [e for _, e in SPOT_CHECKS]
    got_labels, got_probs = predict_batched(sess, tok, texts)
    failures = []
    for i, (txt, exp, got, probs) in enumerate(
            zip(texts, expected, got_labels, got_probs)):
        conf = max(probs)
        agree = exp == got
        tag = "OK  " if agree else "DIFF"
        if not agree:
            failures.append((i, txt, exp, got))
        print(f"    [{tag}] expected={exp:<8} got={got:<8} "
              f"conf={conf:.1%}  {txt[:52]}")
    mandatory = list(range(*SPOT_CHECKS_MUST_PASS.indices(
        len(SPOT_CHECKS))))[0:3]
    hard_fail = [f for f in failures if f[0] in mandatory]
    print()
    print(f"    {len(mandatory) - len(hard_fail)}/{len(mandatory)} mandatory "
          f"spot-checks passed.")
    if failures:
        print("    Differences (recorded, not necessarily fatal):")
        for i, txt, exp, got in failures:
            print(f"      - \"{txt[:48]}\" expected {exp}, got {got}")
    print()

    # ---- Q3: benchmark ---------------------------------------------------
    print("Q3. THROUGHPUT BENCHMARK")
    print("    (using real Tagalog review text from the pipeline doc's test CSV)")
    bench_corpus = [
        "Ang bilis ng delivery at ang sarap ng food!",
        "Medyo matagal ang paghihintay at malamig na ang ulam.",
        "Okay lang naman, hindi maganda hindi rin pangit.",
        "Grabe ang panget ng packaging, basag na basag.",
        "Sulit na sulit, babalik ulit ako dito pramis",
    ]
    # Repeat the 5 real reviews up to 500 rows to get a stable measurement.
    n_bench = 500
    big = [bench_corpus[i % len(bench_corpus)] for i in range(n_bench)]

    results = {}
    for bs in (8, 16, 32, 64):
        t0 = time.perf_counter()
        predict_batched(sess, tok, big, batch_size=bs)
        el = time.perf_counter() - t0
        rate = n_bench / el
        results[bs] = rate
        print(f"    batch_size={bs:<3} {n_bench} reviews in {el:6.2f}s "
              f"= {rate:7.1f} reviews/sec")

    best_bs = max(results, key=results.get)
    best_rate = results[best_bs]
    print()
    print(f"    BEST: batch_size={best_bs} at {best_rate:.1f} reviews/sec")
    print()
    print("    PROJECTION for the n8n pipeline (see sizing table below):")
    print("    n      batch  est_inference  +2x headroom  doc timeout 120s?")
    for n in (100, 300, 500, 1000, 2000):
        est = n / best_rate
        verdict = "YES" if est * 2 < 120 else ("TIGHT" if est < 120 else "NO")
        print(f"    {n:<6} {best_bs:<6} {est:7.1f}s        "
              f"{est * 2:7.1f}s          {verdict}")
    print()
    print("    RECOMMENDATION: set n8n Node 3 review cap so that")
    print(f"      cap / {best_rate:.1f} * 2  <  120s   ->  cap < "
          f"{int(120 * best_rate / 2)}")
    print()

    # ---- verdict ---------------------------------------------------------
    print("=" * 78)
    all_ok = labels_ok and not hard_fail
    if all_ok:
        print("RESULT: PASS - the model is behaving correctly. Safe to build "
              "the service around it.")
    else:
        print("RESULT: FAIL - do not continue to Docker/n8n. Investigate:")
        if not labels_ok:
            print("  - LABELS array in main.py must be reordered to match "
                  "config.json id2label")
        for f in hard_fail:
            print(f"  - spot-check {f[0]} expected {f[2]}, got {f[3]}")
    print("=" * 78)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
