#!/usr/bin/env python3
"""
STEP 15 - Build a realistic 100-sentence test CSV for the Review Pulso form.

WHY REAL DATA AND NOT INVENTED SENTENCES
The obvious thing is to write 100 plausible Filipino reviews by hand. That
would be a poor test for three reasons:

  1. I'd unconsciously write sentences the model has certainly seen, phrased
     the way a language model would phrase them.
  2. The pipeline's real input is scraped Google Maps / Shopee reviews, which
     are short, typo-ridden, code-switched, and full of informal abbreviations
     that a non-speaker would not reproduce.
  3. The source doc's own validation gate (section 6) asks for ~200 REAL
     labelled reviews scored against hand labels. A hand-written CSV cannot
     satisfy that, because the writer's label IS the expected answer.

So this samples from the real TagaSenti dataset, which is already on this
machine:

    ~/Desktop/TagaSenti/tagasenti_dataset.csv   35,686 rows, `sentence,label`
    label 0 = Negative, 1 = Neutral, 2 = Positive

Two artefacts are produced:

  test-100-reviews.csv        100 real reviews, balanced 40/20/40, a single
                              `review` column - exactly what the form wants.

  test-100-ground-truth.csv   the same 100 rows PLUS the true label and the
                              original row id, so predictions can be scored
                              afterwards.

The ground-truth file is the valuable one. Without it the upload is just a
smoke test; with it, the upload becomes a real accuracy measurement - which is
the validation gate the pipeline is supposed to pass before a client sees it.

USAGE
    python3 15_make_test_csv.py
"""

import csv
import os
import random
import re
import sys

DATASET = os.path.expanduser("~/Desktop/TagaSenti/tagasenti_dataset.csv")
# HERE is the agent_journal directory, so output lands in data/ no matter what
# directory the script is invoked from. It must be defined before it is used -
# the original version used bare filenames joined against a local `here`
# defined inside main(), which broke as soon as the files moved.
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(HERE, "data", "test-100-reviews.csv")
OUT_TRUTH = os.path.join(HERE, "data", "test-100-ground-truth.csv")

# Balance: 40 negative / 20 neutral / 40 positive. Deliberately NOT uniform,
# because a real client batch is rarely balanced, and an unbalanced sample
# stresses the percentage math more than a 33/33/33 split would.
TARGET = {0: 40, 1: 20, 2: 40}
LABEL_NAME = {0: "Negative", 1: "Neutral", 2: "Positive"}

# The pipeline's minimum review length (n8n Node 3 drops anything under 3
# chars) and the model's 128-token truncation. Filter to a realistic band so
# the sample resembles genuine short reviews rather than one-line fragments or
# essays.
MIN_CHARS = 15
MAX_CHARS = 400


def clean(s):
    """Normalise whitespace, drop newlines (which would break a naive CSV)."""
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s


def main():
    if not os.path.exists(DATASET):
        sys.exit(f"FATAL: dataset not found at {DATASET}")

    with open(DATASET, newline="", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.DictReader(fh))
    print(f"read {len(rows):,} rows from the real TagaSenti dataset")

    # Bucket by label, filtering to the realistic length band.
    buckets = {0: [], 1: [], 2: []}
    for i, r in enumerate(rows):
        try:
            lab = int(r["label"])
        except (KeyError, ValueError, TypeError):
            continue
        if lab not in buckets:
            continue
        text = clean(r.get("sentence", ""))
        if not (MIN_CHARS <= len(text) <= MAX_CHARS):
            continue
        buckets[lab].append((i + 2, text))   # +2: 1-based, and row 1 is the header
    for k, v in buckets.items():
        print(f"  {LABEL_NAME[k]:<9} usable: {len(v):,}")

    # seed=20260930 so this CSV is byte-reproducible. A test fixture that
    # changes between runs is not a fixture.
    rng = random.Random(20260930)

    picked = []
    for lab, want in TARGET.items():
        pool = buckets[lab]
        if len(pool) < want:
            sys.exit(f"FATAL: only {len(pool)} usable {LABEL_NAME[lab]} rows, "
                     f"need {want}")
        picked.extend(rng.sample(pool, want))

    # Interleave rather than grouping by label. A CSV where rows 1-40 are all
    # negative would let a report look correct by accident if something sorted
    # or bucketed them.
    rng.shuffle(picked)


    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        w.writerow(["review"])
        for _, text in picked:
            w.writerow([text])

    with open(OUT_TRUTH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        w.writerow(["row_in_test_csv", "true_label", "true_label_id",
                    "original_dataset_line", "review"])
        for n, (src_line, text) in enumerate(picked, start=2):
            lab = next(k for k, v in buckets.items()
                       if any(t == text and s == src_line for s, t in v))
            w.writerow([n, LABEL_NAME[lab], lab, src_line, text])

    lens = [len(t) for _, t in picked]
    print()
    print(f"wrote {OUT_CSV}          ({len(picked)} reviews)")
    print(f"wrote {OUT_TRUTH}  ({len(picked)} rows with the true labels)")
    print()
    print(f"  mix          : 40 Negative / 20 Neutral / 40 Positive")
    print(f"  length       : min {min(lens)} chars, median "
          f"{sorted(lens)[len(lens)//2]}, max {max(lens)} chars")
    print(f"  expected net : {(40 - 40) / 100 * 100:+.0f}  (balanced, so 0)")
    print()
    print("Upload", OUT_CSV, "through the form.")
    print("Then score it against", OUT_TRUTH, "- see 16_score_predictions.py")
    print("(that needs the model's own predictions; see the note in the doc).")


if __name__ == "__main__":
    main()
