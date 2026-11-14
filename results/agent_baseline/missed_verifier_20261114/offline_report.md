# find_missed sources and controlled verifier results

Model: qwen3.7-flash. Only SECOND/LEVIR development data. WHU historical old-GT figures are excluded.

All five cached sources are retained. Ranking is source round-robin, descending area within a source, then original index; exact duplicates retain provenance. GT is read only for scoring. S3/S4 thresholds were historically chosen on these development labels, so source gains are descriptive, not held-out generalization.

## Full source complementarity

| Group | Images | Source | Added TP | Added FP | Unique TP vs all others | Unique FP | Contributing images | Missing-pixel recall |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| second_appeared | 60 | S1_02 | 274929 | 397282 | 62177 | 277985 | 44 | 41.896 |
| second_appeared | 60 | S2 | 191517 | 501898 | 17574 | 381015 | 25 | 29.185 |
| second_appeared | 60 | S3 | 53833 | 16451 | 0 | 0 | 0 | 8.204 |
| second_appeared | 60 | S4 | 193329 | 167899 | 50438 | 136617 | 40 | 29.461 |
| second_appeared | 60 | S5 | 76371 | 274438 | 10602 | 152923 | 30 | 11.638 |
| second_disappeared | 60 | S1_02 | 51835 | 343806 | 16788 | 235440 | 32 | 35.781 |
| second_disappeared | 60 | S2 | 35884 | 469214 | 3371 | 315276 | 15 | 24.770 |
| second_disappeared | 60 | S3 | 11533 | 10710 | 181 | 5797 | 1 | 7.961 |
| second_disappeared | 60 | S4 | 26146 | 35890 | 4384 | 28325 | 16 | 18.048 |
| second_disappeared | 60 | S5 | 26639 | 181740 | 397 | 85671 | 12 | 18.388 |
| levir_any | 200 | S1_02 | 700003 | 2756781 | 97684 | 1811017 | 158 | 43.318 |
| levir_any | 200 | S2 | 516510 | 876102 | 37039 | 340198 | 162 | 31.963 |
| levir_any | 200 | S3 | 0 | 0 | 0 | 0 | 0 | 0.000 |
| levir_any | 200 | S4 | 0 | 0 | 0 | 0 | 0 | 0.000 |
| levir_any | 200 | S5 | 769475 | 23771649 | 285376 | 22985250 | 162 | 47.618 |

Unique contribution is relative to the union of all other sources; zero unique pixels does not imply a source is uninformative. Pair overlaps and concentration by image are in [full source evidence](pilot/full_sources_summary.json).

## Same-candidate verifier comparison

The 30-image subset is fixed lexicographic 10/group. Top-5/10 are prefixes of Top-20. Every arm sees the same candidate masks and crop geometry. SoM adds a support-anchored mark; structured adds the same explicit state questions for every candidate. This is addition to a fixed historical initial answer, not an Agent result.

| Group | Images | k | Arm | F1 | Precision | Recall | Candidate missing-pixel recall |
|---|---:|---:|---|---:|---:|---:|---:|
| second_appeared | 10 | — | initial | 70.096 | 88.014 | 58.239 | — |
| second_appeared | 10 | 5 | four | 71.523 | 73.706 | 69.465 | 46.866 |
| second_appeared | 10 | 5 | som | 73.165 | 75.213 | 71.226 | 46.866 |
| second_appeared | 10 | 5 | structured | 71.373 | 72.935 | 69.877 | 46.866 |
| second_appeared | 10 | 10 | four | 71.526 | 71.209 | 71.845 | 52.581 |
| second_appeared | 10 | 10 | som | 73.933 | 74.282 | 73.587 | 52.581 |
| second_appeared | 10 | 10 | structured | 71.597 | 70.623 | 72.598 | 52.581 |
| second_appeared | 10 | 20 | four | 71.680 | 69.949 | 73.498 | 59.127 |
| second_appeared | 10 | 20 | som | 73.852 | 72.801 | 74.934 | 59.127 |
| second_appeared | 10 | 20 | structured | 71.701 | 68.953 | 74.678 | 59.127 |
| second_disappeared | 10 | — | initial | 24.230 | 38.081 | 17.768 | — |
| second_disappeared | 10 | 5 | four | 26.356 | 20.003 | 38.626 | 58.952 |
| second_disappeared | 10 | 5 | som | 23.675 | 35.467 | 17.768 | 58.952 |
| second_disappeared | 10 | 5 | structured | 54.637 | 47.685 | 63.963 | 58.952 |
| second_disappeared | 10 | 10 | four | 41.181 | 29.543 | 67.948 | 62.245 |
| second_disappeared | 10 | 10 | som | 38.204 | 48.114 | 31.679 | 62.245 |
| second_disappeared | 10 | 10 | structured | 56.890 | 48.793 | 68.208 | 62.245 |
| second_disappeared | 10 | 20 | four | 42.267 | 30.067 | 71.131 | 65.962 |
| second_disappeared | 10 | 20 | som | 44.432 | 51.643 | 38.989 | 65.962 |
| second_disappeared | 10 | 20 | structured | 56.608 | 46.951 | 71.265 | 65.962 |
| levir_any | 10 | — | initial | 87.625 | 82.230 | 93.779 | — |
| levir_any | 10 | 5 | four | 79.389 | 68.590 | 94.224 | 8.301 |
| levir_any | 10 | 5 | som | 78.093 | 66.661 | 94.258 | 8.301 |
| levir_any | 10 | 5 | structured | 73.851 | 60.693 | 94.294 | 8.301 |
| levir_any | 10 | 10 | four | 75.104 | 62.341 | 94.440 | 14.419 |
| levir_any | 10 | 10 | som | 73.220 | 59.773 | 94.474 | 14.419 |
| levir_any | 10 | 10 | structured | 67.912 | 52.971 | 94.591 | 14.419 |
| levir_any | 10 | 20 | four | 70.035 | 55.601 | 94.592 | 18.394 |
| levir_any | 10 | 20 | som | 68.627 | 53.834 | 94.630 | 18.394 |
| levir_any | 10 | 20 | structured | 63.043 | 47.234 | 94.756 | 18.394 |

## Per-image verifier diagnostics

False accept/reject below uses candidate TP>FP as a pixel-benefit proxy, not an adjudicated semantic label. Refer to original images/responses before assigning a model error. Counts use Top-20 once, avoiding repeated counting at Top-5/10.

| Image/group | Arm | Answers/expected | Unknown | Truncated | Proxy false accept | Proxy false reject | Evidence |
|---|---|---:|---:|---:|---:|---:|---|
| second_appeared/train_1065 | four | 20/20 | 0 | 0 | 3 | 1 | [case](pilot/scores/second_appeared/train_1065.json) |
| second_appeared/train_1065 | som | 20/20 | 0 | 0 | 3 | 1 | [case](pilot/scores/second_appeared/train_1065.json) |
| second_appeared/train_1065 | structured | 20/20 | 0 | 0 | 5 | 0 | [case](pilot/scores/second_appeared/train_1065.json) |
| second_appeared/train_1129 | four | 20/20 | 0 | 0 | 10 | 1 | [case](pilot/scores/second_appeared/train_1129.json) |
| second_appeared/train_1129 | som | 20/20 | 0 | 0 | 9 | 4 | [case](pilot/scores/second_appeared/train_1129.json) |
| second_appeared/train_1129 | structured | 20/20 | 0 | 0 | 11 | 0 | [case](pilot/scores/second_appeared/train_1129.json) |
| second_appeared/train_1136 | four | 20/20 | 0 | 0 | 4 | 0 | [case](pilot/scores/second_appeared/train_1136.json) |
| second_appeared/train_1136 | som | 20/20 | 0 | 0 | 3 | 0 | [case](pilot/scores/second_appeared/train_1136.json) |
| second_appeared/train_1136 | structured | 20/20 | 0 | 0 | 4 | 0 | [case](pilot/scores/second_appeared/train_1136.json) |
| second_appeared/train_1166 | four | 7/7 | 0 | 0 | 0 | 6 | [case](pilot/scores/second_appeared/train_1166.json) |
| second_appeared/train_1166 | som | 7/7 | 0 | 0 | 0 | 6 | [case](pilot/scores/second_appeared/train_1166.json) |
| second_appeared/train_1166 | structured | 7/7 | 0 | 0 | 0 | 5 | [case](pilot/scores/second_appeared/train_1166.json) |
| second_appeared/train_1177 | four | 20/20 | 0 | 0 | 2 | 3 | [case](pilot/scores/second_appeared/train_1177.json) |
| second_appeared/train_1177 | som | 20/20 | 0 | 0 | 0 | 2 | [case](pilot/scores/second_appeared/train_1177.json) |
| second_appeared/train_1177 | structured | 20/20 | 1 | 1 | 2 | 0 | [case](pilot/scores/second_appeared/train_1177.json) |
| second_appeared/train_119 | four | 9/9 | 0 | 0 | 2 | 2 | [case](pilot/scores/second_appeared/train_119.json) |
| second_appeared/train_119 | som | 9/9 | 0 | 0 | 0 | 2 | [case](pilot/scores/second_appeared/train_119.json) |
| second_appeared/train_119 | structured | 9/9 | 0 | 0 | 2 | 1 | [case](pilot/scores/second_appeared/train_119.json) |
| second_appeared/train_127 | four | 20/20 | 0 | 0 | 2 | 1 | [case](pilot/scores/second_appeared/train_127.json) |
| second_appeared/train_127 | som | 20/20 | 0 | 0 | 0 | 2 | [case](pilot/scores/second_appeared/train_127.json) |
| second_appeared/train_127 | structured | 20/20 | 0 | 0 | 2 | 3 | [case](pilot/scores/second_appeared/train_127.json) |
| second_appeared/train_1322 | four | 20/20 | 0 | 0 | 15 | 0 | [case](pilot/scores/second_appeared/train_1322.json) |
| second_appeared/train_1322 | som | 20/20 | 0 | 0 | 14 | 0 | [case](pilot/scores/second_appeared/train_1322.json) |
| second_appeared/train_1322 | structured | 20/20 | 0 | 0 | 15 | 0 | [case](pilot/scores/second_appeared/train_1322.json) |
| second_appeared/train_1344 | four | 20/20 | 0 | 0 | 0 | 0 | [case](pilot/scores/second_appeared/train_1344.json) |
| second_appeared/train_1344 | som | 20/20 | 0 | 0 | 0 | 2 | [case](pilot/scores/second_appeared/train_1344.json) |
| second_appeared/train_1344 | structured | 20/20 | 1 | 1 | 0 | 0 | [case](pilot/scores/second_appeared/train_1344.json) |
| second_appeared/train_1370 | four | 20/20 | 0 | 0 | 0 | 6 | [case](pilot/scores/second_appeared/train_1370.json) |
| second_appeared/train_1370 | som | 20/20 | 0 | 0 | 0 | 4 | [case](pilot/scores/second_appeared/train_1370.json) |
| second_appeared/train_1370 | structured | 20/20 | 0 | 0 | 1 | 5 | [case](pilot/scores/second_appeared/train_1370.json) |
| second_disappeared/train_1065 | four | 8/8 | 0 | 0 | 4 | 0 | [case](pilot/scores/second_disappeared/train_1065.json) |
| second_disappeared/train_1065 | som | 8/8 | 0 | 0 | 3 | 0 | [case](pilot/scores/second_disappeared/train_1065.json) |
| second_disappeared/train_1065 | structured | 8/8 | 0 | 0 | 7 | 0 | [case](pilot/scores/second_disappeared/train_1065.json) |
| second_disappeared/train_1129 | four | 6/6 | 0 | 0 | 1 | 0 | [case](pilot/scores/second_disappeared/train_1129.json) |
| second_disappeared/train_1129 | som | 6/6 | 0 | 0 | 1 | 0 | [case](pilot/scores/second_disappeared/train_1129.json) |
| second_disappeared/train_1129 | structured | 6/6 | 0 | 0 | 2 | 0 | [case](pilot/scores/second_disappeared/train_1129.json) |
| second_disappeared/train_1136 | four | 20/20 | 0 | 0 | 3 | 2 | [case](pilot/scores/second_disappeared/train_1136.json) |
| second_disappeared/train_1136 | som | 20/20 | 0 | 0 | 3 | 4 | [case](pilot/scores/second_disappeared/train_1136.json) |
| second_disappeared/train_1136 | structured | 20/20 | 1 | 1 | 5 | 1 | [case](pilot/scores/second_disappeared/train_1136.json) |
| second_disappeared/train_1166 | four | 5/5 | 0 | 0 | 1 | 0 | [case](pilot/scores/second_disappeared/train_1166.json) |
| second_disappeared/train_1166 | som | 5/5 | 0 | 0 | 1 | 0 | [case](pilot/scores/second_disappeared/train_1166.json) |
| second_disappeared/train_1166 | structured | 5/5 | 0 | 0 | 1 | 0 | [case](pilot/scores/second_disappeared/train_1166.json) |
| second_disappeared/train_1177 | four | 11/11 | 0 | 0 | 0 | 0 | [case](pilot/scores/second_disappeared/train_1177.json) |
| second_disappeared/train_1177 | som | 11/11 | 0 | 0 | 0 | 0 | [case](pilot/scores/second_disappeared/train_1177.json) |
| second_disappeared/train_1177 | structured | 11/11 | 1 | 1 | 0 | 0 | [case](pilot/scores/second_disappeared/train_1177.json) |
| second_disappeared/train_119 | four | 20/20 | 0 | 0 | 4 | 9 | [case](pilot/scores/second_disappeared/train_119.json) |
| second_disappeared/train_119 | som | 20/20 | 0 | 0 | 4 | 8 | [case](pilot/scores/second_disappeared/train_119.json) |
| second_disappeared/train_119 | structured | 20/20 | 0 | 0 | 5 | 0 | [case](pilot/scores/second_disappeared/train_119.json) |
| second_disappeared/train_127 | four | 20/20 | 0 | 0 | 2 | 0 | [case](pilot/scores/second_disappeared/train_127.json) |
| second_disappeared/train_127 | som | 20/20 | 0 | 0 | 1 | 0 | [case](pilot/scores/second_disappeared/train_127.json) |
| second_disappeared/train_127 | structured | 20/20 | 0 | 0 | 1 | 0 | [case](pilot/scores/second_disappeared/train_127.json) |
| second_disappeared/train_1322 | four | 20/20 | 0 | 0 | 18 | 0 | [case](pilot/scores/second_disappeared/train_1322.json) |
| second_disappeared/train_1322 | som | 20/20 | 0 | 0 | 13 | 0 | [case](pilot/scores/second_disappeared/train_1322.json) |
| second_disappeared/train_1322 | structured | 20/20 | 0 | 0 | 18 | 0 | [case](pilot/scores/second_disappeared/train_1322.json) |
| second_disappeared/train_1344 | four | 20/20 | 0 | 0 | 0 | 0 | [case](pilot/scores/second_disappeared/train_1344.json) |
| second_disappeared/train_1344 | som | 20/20 | 0 | 0 | 0 | 0 | [case](pilot/scores/second_disappeared/train_1344.json) |
| second_disappeared/train_1344 | structured | 20/20 | 0 | 0 | 3 | 0 | [case](pilot/scores/second_disappeared/train_1344.json) |
| second_disappeared/train_1370 | four | 20/20 | 0 | 0 | 1 | 0 | [case](pilot/scores/second_disappeared/train_1370.json) |
| second_disappeared/train_1370 | som | 20/20 | 0 | 0 | 0 | 0 | [case](pilot/scores/second_disappeared/train_1370.json) |
| second_disappeared/train_1370 | structured | 20/20 | 0 | 0 | 1 | 0 | [case](pilot/scores/second_disappeared/train_1370.json) |
| levir_any/train_106 | four | 20/20 | 0 | 0 | 8 | 0 | [case](pilot/scores/levir_any/train_106.json) |
| levir_any/train_106 | som | 20/20 | 0 | 0 | 5 | 0 | [case](pilot/scores/levir_any/train_106.json) |
| levir_any/train_106 | structured | 20/20 | 0 | 0 | 10 | 0 | [case](pilot/scores/levir_any/train_106.json) |
| levir_any/train_109 | four | 20/20 | 0 | 0 | 3 | 0 | [case](pilot/scores/levir_any/train_109.json) |
| levir_any/train_109 | som | 20/20 | 0 | 0 | 4 | 0 | [case](pilot/scores/levir_any/train_109.json) |
| levir_any/train_109 | structured | 20/20 | 0 | 0 | 12 | 0 | [case](pilot/scores/levir_any/train_109.json) |
| levir_any/train_11 | four | 20/20 | 0 | 0 | 9 | 1 | [case](pilot/scores/levir_any/train_11.json) |
| levir_any/train_11 | som | 20/20 | 0 | 0 | 8 | 1 | [case](pilot/scores/levir_any/train_11.json) |
| levir_any/train_11 | structured | 20/20 | 0 | 0 | 15 | 0 | [case](pilot/scores/levir_any/train_11.json) |
| levir_any/train_116 | four | 20/20 | 0 | 0 | 14 | 0 | [case](pilot/scores/levir_any/train_116.json) |
| levir_any/train_116 | som | 20/20 | 0 | 0 | 16 | 0 | [case](pilot/scores/levir_any/train_116.json) |
| levir_any/train_116 | structured | 20/20 | 0 | 0 | 16 | 0 | [case](pilot/scores/levir_any/train_116.json) |
| levir_any/train_118 | four | 20/20 | 0 | 0 | 14 | 0 | [case](pilot/scores/levir_any/train_118.json) |
| levir_any/train_118 | som | 20/20 | 0 | 0 | 15 | 0 | [case](pilot/scores/levir_any/train_118.json) |
| levir_any/train_118 | structured | 20/20 | 0 | 0 | 14 | 0 | [case](pilot/scores/levir_any/train_118.json) |
| levir_any/train_119 | four | 20/20 | 0 | 0 | 14 | 0 | [case](pilot/scores/levir_any/train_119.json) |
| levir_any/train_119 | som | 20/20 | 0 | 0 | 13 | 0 | [case](pilot/scores/levir_any/train_119.json) |
| levir_any/train_119 | structured | 20/20 | 3 | 3 | 15 | 0 | [case](pilot/scores/levir_any/train_119.json) |
| levir_any/train_121 | four | 20/20 | 0 | 0 | 14 | 0 | [case](pilot/scores/levir_any/train_121.json) |
| levir_any/train_121 | som | 20/20 | 0 | 0 | 15 | 0 | [case](pilot/scores/levir_any/train_121.json) |
| levir_any/train_121 | structured | 20/20 | 0 | 0 | 14 | 0 | [case](pilot/scores/levir_any/train_121.json) |
| levir_any/train_122 | four | 20/20 | 0 | 0 | 7 | 0 | [case](pilot/scores/levir_any/train_122.json) |
| levir_any/train_122 | som | 20/20 | 0 | 0 | 9 | 0 | [case](pilot/scores/levir_any/train_122.json) |
| levir_any/train_122 | structured | 20/20 | 0 | 0 | 10 | 0 | [case](pilot/scores/levir_any/train_122.json) |
| levir_any/train_123 | four | 20/20 | 0 | 0 | 9 | 0 | [case](pilot/scores/levir_any/train_123.json) |
| levir_any/train_123 | som | 20/20 | 0 | 0 | 9 | 0 | [case](pilot/scores/levir_any/train_123.json) |
| levir_any/train_123 | structured | 20/20 | 0 | 0 | 11 | 0 | [case](pilot/scores/levir_any/train_123.json) |
| levir_any/train_124 | four | 20/20 | 0 | 0 | 16 | 0 | [case](pilot/scores/levir_any/train_124.json) |
| levir_any/train_124 | som | 20/20 | 0 | 0 | 16 | 0 | [case](pilot/scores/levir_any/train_124.json) |
| levir_any/train_124 | structured | 20/20 | 1 | 1 | 16 | 0 | [case](pilot/scores/levir_any/train_124.json) |

See [manual smoke audit](manual_smoke_review.md) for checked localization failures. No offline acceptance directly changes an Agent answer.
