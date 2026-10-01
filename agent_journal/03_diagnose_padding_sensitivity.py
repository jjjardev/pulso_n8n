#!/usr/bin/env python3
"""
DIAGNOSTIC - why do batched and one-at-a-time predictions disagree?

FINDING FROM 02_test_tagasenti_service.py
  Batching 5 reviews together produced a DIFFERENT label than scoring them one
  at a time, with a max score drift of 0.2678. That is far too large to be
  harmless. Since n8n Node 5 zips results[i] with reviews[i] and the PDF
  prints a confidence bar per review, a padding-sensitive model would show
  clients confidence numbers that do not reproduce.

HYPOTHESES TO TEST (not guess - measure)
  H1. PADDING. Sequences in a batch are padded to the batch's longest member.
      If the INT8 graph mishandles pad positions, real tokens' outputs shift.
  H2. THREAD NON-DETERMINISM. onnxruntime CPU inference with multiple threads
      can produce slightly different floats run-to-run. If the gaps are tiny
      and random, this is it. If they are large and systematic, it's H1.
  H3. IT'S JUST AMBIGUOUS TEXT. "Okay lang naman, hindi maganda hindi rin
      pangit." is genuinely mixed, so a near-50/50 model may sit on a knife
      edge and fall either way.

  H3 predicts: drift correlates with how close the decision was to the argmax
  boundary. H1 predicts: drift appears even for confidently-classified text.
  We test both.

USAGE
    python3 03_diagnose_padding_sensitivity.py
"""

import os
import sys

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_DIR = os.environ.get("TAGASENTI_DIR",
                             os.path.join(REPO_ROOT, "tagasenti"))
os.environ["MODEL_PATH"] = os.environ.get(
    "MODEL_PATH", os.path.join(SERVICE_DIR, "tagasenti_int8.onnx"))
os.environ["TOKENIZER_DIR"] = os.path.join(SERVICE_DIR, "tokenizer")
sys.path.insert(0, SERVICE_DIR)

import main  # noqa: E402

LABELS = main.LABELS


def probs_of(sess, tok, text, pad_with=None, threads=None):
    """
    Return the probability vector for `text`.

    pad_with: a list of other texts to include in the same batch, forcing
              padding. We then read off the row for `text`.
    threads:   optionally re-open the session with a different thread count.
    """
    if threads is not None:
        so = __import__("onnxruntime").SessionOptions()
        so.intra_op_num_threads = threads
        s = __import__("onnxruntime").InferenceSession(
            main.MODEL_PATH, sess_options=so,
            providers=["CPUExecutionProvider"])
    else:
        s = sess

    if pad_with is None:
        chunk = [text]
    else:
        # Longest text first, then the target last, so the target is the most
        # heavily padded row in its batch - the harshest possible test.
        chunk = pad_with + [text]

    enc = tok(chunk, padding=True, truncation=True, max_length=main.MAX_LENGTH,
              return_tensors="np")
    logits = s.run(["logits"], {
        "input_ids": enc["input_ids"].astype(np.int64),
        "attention_mask": enc["attention_mask"].astype(np.int64),
    })[0]
    e = np.exp(logits - logits.max(axis=-1, keepdims=True))
    p = e / e.sum(axis=-1, keepdims=True)
    return p[-1]  # target is always last


sess = main.sess
tok = main.tok

print("=" * 78)
print("DIAGNOSTIC: batch padding vs thread non-determinism")
print("=" * 78)

# ---------------------------------------------------------------------------
print("\nD1. H2 - is inference even deterministic across repeat runs?")
print("    (same input, same batch, run 5 times)")
text = "Ang bilis ng delivery at ang sarap ng food!"
base = probs_of(sess, tok, text)
deltas = []
for _ in range(5):
    deltas.append(float(np.abs(probs_of(sess, tok, text) - base).max()))
print(f"    max drift across 5 identical runs: {max(deltas):.10f}")
print(f"    -> {'DETERMINISTIC' if max(deltas) < 1e-6 else 'NON-DETERMINISTIC'}")
print("    (if deterministic, H2 is ruled out and any drift is from padding)")

# ---------------------------------------------------------------------------
print("\nD2. H1 - does padding change a CONFIDENTLY classified review?")
print("    (padding with a deliberately long text)")
long_text = ("This is a very long filler sentence used only to force the "
              "tokenizer to pad every other row in the batch to a much larger "
              "sequence length than the target review needs, so that we can "
              "observe whether the int8 graph is sensitive to the padded "
              "positions of the attention mask. " * 2)

confident = [
    ("Ang ganda ng quality ng tela, worth it ang price!", "Positive"),
    ("Wala pa ring update ang order ko hanggang ngayon.", "Negative"),
    ("Sulit na sulit, babalik ulit ako dito pramis", "Positive"),
]
worst_padded = 0.0
for txt, expected in confident:
    solo = probs_of(sess, tok, txt)
    padded = probs_of(sess, tok, txt, pad_with=[long_text])
    drift = float(np.abs(solo - padded).max())
    worst_padded = max(worst_padded, drift)
    print(f"    solo  {LABELS[int(solo.argmax())]:<9} "
          f"max_prob={solo.max():.4f}  {txt[:40]}")
    print(f"    padded{LABELS[int(padded.argmax())]:<9} "
          f"max_prob={padded.max():.4f}   drift={drift:.4f}  "
          f"{'SAME' if solo.argmax() == padded.argmax() else '*** FLIPPED ***'}")
print(f"\n    worst drift on CONFIDENT text: {worst_padded:.4f}")
if worst_padded < 0.01:
    print("    -> H1 RULED OUT. Confident reviews are padding-stable.")
    print("       So the T4 mismatch must be H3 (ambiguous text near the")
    print("       decision boundary), not a batching bug.")
else:
    print("    -> H1 CONFIRMED. The int8 graph IS padding-sensitive.")

# ---------------------------------------------------------------------------
print("\nD3. H3 - how close to the boundary is the text that flipped?")
texts = [
    "Ang bilis ng delivery at ang sarap ng food!",
    "Medyo matagal ang paghihintay at malamig na ang ulam.",
    "Okay lang naman, hindi maganda hindi rin pangit.",
    "Grabe ang panget ng packaging, basag na basag.",
    "Sulit na sulit, babalik ulit ako dito pramis",
]
print(f"    {'review':<48} {'gap(top1-top2)':>14}  stable?")
for txt in texts:
    solo = probs_of(sess, tok, txt)
    batched = probs_of(sess, tok, txt, pad_with=[t for t in texts if t != txt])
    order = np.argsort(solo)[::-1]
    gap = float(solo[order[0]] - solo[order[1]])
    same = solo.argmax() == batched.argmax()
    print(f"    {txt[:46]:<48} {gap:>14.4f}  {'yes' if same else 'NO'}")
print("\n    A tiny gap means the model is genuinely undecided, so any")
print("    numerical perturbation flips it. That is a property of the TEXT,")
print("    not a bug in our batching.")

# ---------------------------------------------------------------------------
print("\nD4. Does thread count change predictions?")
print("    (batching + int8 on CPU: more threads = faster but is it stable?)")
ref = probs_of(sess, tok, texts[0], pad_with=texts[1:])
for th in (1, 2, 4, 8):
    p = probs_of(sess, tok, texts[0], pad_with=texts[1:], threads=th)
    print(f"    threads={th}: max_prob={p.max():.4f} "
          f"drift_vs_4={float(np.abs(p - ref).max()):.6f}")

print("\n" + "=" * 78)
print("See agent_journal/JOURNAL.md entry 03 for the decision taken.")
print("=" * 78)
