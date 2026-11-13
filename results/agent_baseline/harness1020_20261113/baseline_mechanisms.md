# Actual frozen baseline: six complete sessions

Source: `.runtime/harness1020-20261113/online/baseline/<task>/val/<image>/steps.jsonl`. These are the new paired baseline, not the historical selection sessions. All six submitted normally; no invalid retries. Historical missing-current, mask/instance conversion, and stale-ID failures did not recur.

| Case | Key steps | Observed mechanism |
|---|---|---|
| T1 val31 | 16 adopts current; 17 ask; 18 deletion | Verifier confidently accepts IDs9/5; final545 FP on empty GT, F1 0. Interface recovery cannot be credited for this baseline; visual false positives remain. |
| T1 val22 | 10 combines demolition; 15 verifier; 16 deletion | Combination adds2499 FP, F1 87.23→83.31; verifier-driven deletion loses683 TP and removes532 FP, final82.66. No interface error. |
| T1 val44 | 3 invalid view; 4 to_mask; 5 valid view | Only baseline tool error: requesting component labels on inst#1. It already recovers next step; final92.386. |
| T5 val32 | View-only inspection, then submit | Correctly preserves initial empty mask; F1 100. Historical false-positive addition does not recur. New variants must protect this successful answer. |
| T5 val47 | 16 reaches43.22; 19 verifier; 24 deletion; 25 restoration | Deletion loses552 TP and removes1831 FP, F1 falls40.96; restoring834 FP ends32.626. Legal edits under verifier guidance, not ID corruption. |
| T5 val36 | 7 verifier; 8 deletion | Removes993 TP and95 FP, F1 93.75→92.283. Simple-case decline predates the new repair. |

New runs must distinguish candidate/visual changes from interface recovery, report token overhead, and preserve this baseline when comparing later stages. Fewer errors alone does not establish a causal repair of a failure absent from this baseline.
