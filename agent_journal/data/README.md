# data/ — test data

Three CSVs used to exercise and validate the pipeline. None of them is real
customer data.

| File | What it is |
|---|---|
| `business-reviews-100.csv` | 100 hand-written Filipino business reviews (restaurants, sellers, services). This is the closest thing to what a real client uploads, and the best file for a demo. |
| `test-100-reviews.csv` | 100 reviews sampled from the TagaSenti corpus, balanced 40 negative / 20 neutral / 40 positive. |
| `test-100-ground-truth.csv` | The same 100 rows plus their true labels, for scoring model accuracy. |

## A caveat worth reading before you trust `test-100-reviews.csv`

TagaSenti is a **multi-domain** corpus. A random sample of it is dominated by
news and political commentary — in a random 14-row draw, only 2–3 were business
reviews. So accuracy measured against `test-100-ground-truth.csv` describes the
model on *general Filipino text*, **not** on the Google Maps and Shopee feedback
this tool is actually for.

`business-reviews-100.csv` is the better demonstration file, but it has no
ground-truth labels, so it cannot be used to measure accuracy.

## Validating for real

Both are working files. What is missing is the thing that actually matters:
**a labelled set of real business reviews.** See the "Things that are not
finished yet" section of `../../README.md`.
