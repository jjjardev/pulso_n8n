# evidence/ — the proof behind JOURNAL.md

Every number in `../JOURNAL.md` has a file in here behind it. If you do not
believe a claim, the raw output that produced it is in this directory.

| File | Backs up |
|---|---|
| `*.output.log` | The measured result of each script: throughput, accuracy, page counts, spot-check labels. |
| `sample-report.html` | The rendered report before PDF conversion. |
| `smoke-test-report.pdf` | A real report Gotenberg produced, from the smoke test. |
| `14_visual_check.png`, `14_visual_check_full.png` | The report, rendered and inspected by eye. This is how the `1. 1.` double-numbering bug and the donut caption overflow were found — both passed every string assertion. |
| `18_upload_page.png` | The drag-and-drop upload page as it appears in a browser. |

## Why keep these

They are 1.1 MB. They cost nothing to keep, and they are the difference between
"JOURNAL.md says 89.9% macro F1" and "JOURNAL.md says 89.9% macro F1, here is
the output, check the arithmetic."

Several claims in the journal were also *refuted* by these files — the "37
reviews per second" figure that turned out to be measured on unrealistically
short test strings, for one. A record that only contains the numbers I was
right about is not a record.

## They are regenerable

Every file here can be rebuilt by rerunning its script, with one exception
worth noting: `01`, `03`, `04`, `08` and `16` are slow (minutes) and
`16_score_predictions.py` depends on `../data/`. Nothing here is a source of
truth. The source of truth is `../JOURNAL.md` plus the scripts.
