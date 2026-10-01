#!/usr/bin/env python3
"""
STEP 16 - Score the test CSV against ground truth.

WHAT THIS DOES
Runs the local TagaSenti INT8 model over `test-100-reviews.csv` and compares
its predictions to `test-100-ground-truth.csv`, reporting accuracy, macro F1
and a confusion matrix.

WHY THIS EXISTS
The pipeline doc's section 6 validation gate says: take ~200 REAL reviews,
label them yourself, compare labels, require >= 75% macro F1, and only then
demo to a client. This script is the scoring half of that gate.

It runs the model LOCALLY rather than going through n8n, for two reasons:

  1. It is ~100x faster, so it can be run whenever the model is touched.
  2. It scores the MODEL, in isolation. If a number is wrong you want to know
     whether the model is wrong or the pipeline is wrong, and this separates
     the two.

THE DOMAIN CAVEAT - read before trusting any number here
TagaSenti is a MULTI-DOMAIN dataset. A random sample of it is dominated by
news and political commentary, because that is what dominates the corpus. A
random 14-row sample contains roughly 2-3 business reviews and 8+ news items.

Review Pulso is for BUSINESS reviews (Google Maps, Shopee). So:

  - These numbers describe the model on multi-domain Filipino text.
  - They are NOT a prediction of accuracy on a client's Google Maps reviews.

Expect the business-review subset to score differently, and quite possibly
better, because "mahal ang food pero maganda ang service" is the kind of
mixed-sentiment business text the model saw a lot of. The honest reading of the
result below is: "the model works, and here is its accuracy on this domain" -
not "the model is X% accurate for your client".

A true section-6 gate needs ~200 REAL BUSINESS reviews, hand-labelled by
someone who reads Filipino. That is a human task and cannot be automated from
a corpus like this one.

USAGE
    python3 16_score_predictions.py
"""

import csv
import os
import sys

# Resolve the service directory relative to the repo (see 02 for rationale).
# Override with TAGASENTI_DIR / MODEL_PATH if the runtime lives elsewhere.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_DIR = os.environ.get("TAGASENTI_DIR",
                             os.path.join(REPO_ROOT, "tagasenti"))
os.environ["MODEL_PATH"] = os.environ.get(
    "MODEL_PATH", os.path.join(SERVICE_DIR, "tagasenti_int8.onnx"))
os.environ["TOKENIZER_DIR"] = os.path.join(SERVICE_DIR, "tokenizer")
sys.path.insert(0, SERVICE_DIR)

LABELS = ["Negative", "Neutral", "Positive"]
HERE = os.path.dirname(os.path.abspath(__file__))
CSV_IN = os.path.join(HERE, "data", "test-100-reviews.csv")
TRUTH = os.path.join(HERE, "data", "test-100-ground-truth.csv")

# Words that mark a sentence as business/product feedback rather than news or
# opinion. Used only to REPORT the split - never to select or filter the test
# set, which must stay a random sample to be honest.
BUSINESS_HINTS = {
    "seller", "item", "shipping", "delivery", "order", "price", "quality",
    "service", "staff", "food", "menu", "packaging", "shop", "store",
    "product", "branch", "cashier", "wait", "sulit", "worth", "masarap",
    "mura", "pambago", "bought", "ordered", "recommend", "ulit", "balik",
    "pagkain", "taste", "tasted", "kain", "mamili", "maling", "defective",
    "damaged", "refund", "exchange", "promo", "price", "budget",
}


def is_business(text):
    t = text.lower()
    return any(w in t for w in BUSINESS_HINTS)


def macro_f1(matrix, labels):
    """
    matrix[i][j] = count of true label i predicted as j.
    F1 per class = 2PR / (P+R), then averaged unweighted.
    """
    f1s = []
    for i, lab in enumerate(labels):
        tp = matrix[i][i]
        fp = sum(matrix[r][i] for r in range(len(labels))) - tp
        fn = sum(matrix[i]) - tp
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        f1s.append(f1)
    return f1s, sum(f1s) / len(f1s)


def main():
    for p in (CSV_IN, TRUTH):
        if not os.path.exists(p):
            sys.exit(f"FATAL: {p} missing - run 15_make_test_csv.py")

    tests = [r["review"] for r in csv.DictReader(open(CSV_IN, encoding="utf-8"))]
    truth = {int(r["row_in_test_csv"]): r["true_label"]
             for r in csv.DictReader(open(TRUTH, encoding="utf-8"))}
    print(f"loaded {len(tests)} test reviews, {len(truth)} truth labels")
    if len(tests) != len(truth):
        sys.exit("FATAL: test CSV and ground truth have different row counts")

    import main as svc  # the tagasenti service module
    print("loading model ...", flush=True)
    preds = svc.run_many(tests)

    matrix = [[0] * 3 for _ in range(3)]
    per_class_fp = {l: [] for l in LABELS}
    business = 0
    correct_business = 0

    for i, (text, p) in enumerate(zip(tests, preds)):
        row = i + 2                       # +2: row 1 is the header
        true_label = truth[row]
        ti = LABELS.index(true_label)
        pi = LABELS.index(p["label"])
        matrix[ti][pi] += 1
        if ti != pi:
            per_class_fp[true_label].append(
                (text, p["label"], p["scores"][p["label"]]))
        if is_business(text):
            business += 1
            if ti == pi:
                correct_business += 1

    n = len(preds)
    total_correct = sum(matrix[i][i] for i in range(3))

    print()
    print("=" * 78)
    print("TagaSenti INT8 - 100 real Filipino reviews, scored against ground truth")
    print("=" * 78)

    print(f"\nOVERALL")
    print(f"  accuracy     : {total_correct}/{n} = {total_correct / n * 100:.1f}%")
    f1s, mf1 = macro_f1(matrix, LABELS)
    print(f"  macro F1     : {mf1 * 100:.1f}%   (doc's gate is >= 75%)")
    gate = "PASS" if mf1 >= 0.75 else "BELOW GATE"
    print(f"  vs gate      : {gate}")

    print(f"\nPER CLASS")
    print(f"  {'label':<10} {'prec':>7} {'rec':>7} {'F1':>7} {'support':>8}")
    for i, lab in enumerate(LABELS):
        tp = matrix[i][i]
        fp = sum(matrix[r][i] for r in range(3)) - tp
        fn = sum(matrix[i]) - tp
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        print(f"  {lab:<10} {prec:>7.3f} {rec:>7.3f} {f1s[i]:>7.3f} {sum(matrix[i]):>8}")

    print(f"\nCONFUSION MATRIX  (rows = truth, cols = predicted)")
    print(f"  {'':<10}" + "".join(f"{l[:4]:>9}" for l in LABELS) + f"{'total':>9}")
    for i, lab in enumerate(LABELS):
        row = "".join(f"{matrix[i][j]:>9}" for j in range(3))
        print(f"  {lab:<10}{row}{sum(matrix[i]):>9}")

    print(f"\nDOMAIN SPLIT (reported, not used to select the sample)")
    print(f"  looks like business/product feedback : {business}/{n} rows")
    if business:
        print(f"  accuracy on that subset              : "
              f"{correct_business / business * 100:.1f}%")
    print(f"  the rest is news / political commentary, which TagaSenti was also")
    print(f"  trained on. See the docstring: these numbers are NOT a prediction")
    print(f"  of accuracy on a client's Google Maps reviews.")

    print(f"\nWORST MISTAKES (true label -> predicted, with confidence)")
    misses = []
    for lab in LABELS:
        for text, pred, score in per_class_fp[lab]:
            misses.append((score, lab, pred, text))
    misses.sort(reverse=True)
    for score, tl, pl, text in misses[:8]:
        print(f"  {tl:>8} -> {pl:<8} ({score:.0%} conf)  {text[:70]}")

    print("=" * 78)


if __name__ == "__main__":
    main()
