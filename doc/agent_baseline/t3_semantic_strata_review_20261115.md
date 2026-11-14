# Qualification of the B3 stopping rationale

The completed `semantic_strata_tune/summary.json` changes the interpretation of the earlier aggregate comparison. It does not change any original predictions, labels, or scores.

| Fixed tune stratum | N | B0 pair agreement | B1 | B2 | B3 |
|---|---:|---:|---:|---:|---:|
| All | 30 | 8 | 4 | 6 | 6 |
| Remaining GT positive | 11 | 1 | 2 | 4 | 2 |
| Remaining GT >=16, sensitivity only | 8 | 0 | 0 | 2 | 1 |
| Remaining GT empty | 19 | 7 | 2 | 2 | 4 |

**The statement “B3 6/30 < B0 8/30, therefore semantic parsing worsened on useful retrieval tasks” is unsupported and should be corrected.** B3 gains one correct pair on positives and one in the >=16 diagnostic, while losing three net correct pairs on empty tasks. B2 has the largest observed positive-stratum agreement, despite lower overall agreement. The fixed cohort composition masks these distinctions. All four variants use the same tasks; no sample selection changed.

Agreement is not improved at every endpoint either: on positives, before agreement for B0/B1/B2/B3 is 7/8/9/8 of 11, and after agreement is 4/3/4/3. Pair outcomes reflect which endpoints coincide correctly, not uniform improvement in every marginal measure. Correct label pairs are not independently established support-grounding accuracy. B2 additionally sees both dates and changes view/support instructions; its gain cannot be attributed to spotlight alone.

## Which positive cases improve?

- B0: only train_1136_5to1, with 3 remaining GT pixels.
- B1: train_1136_5to1 and train_2571_4to5, with 3 and 6 remaining pixels. Neither passes the >=16 sensitivity threshold.
- B2: those two cases plus train_1745_5to2 (12451 remaining pixels) and train_1808_2to1 (7641). Thus it has two genuinely non-tiny label-pair improvements under this diagnostic.
- B3: train_1808_2to1 and train_915_3to4 (1 remaining pixel); B0's train_1136 pair is lost. The B3 non-tiny improvement is real as label agreement, but it is one case.

## Why better labels still do not establish retrieval benefit

The cache-only historical GT-text diagnostic covers only 4 of 40315 positive tune GT pixels under the existing strict source-loss/target-gain proposal rule. This is evidence against treating a parser-only fix as sufficient for this particular pipeline; it is not a universal upper bound on every possible proposal method or on incidental overlap from wrong-class queries.

The corrected cases make that limitation concrete. In train_1745, the historical correct source query covers 10358 GT pixels but the target-at-T2 query covers zero, leaving zero intersection. In train_1808, correct endpoint queries intersect on 3479 GT pixels, but strict source-loss exclusion reduces the supported truth to 4. The other positive cases listed above have zero strict GT coverage in the historical oracle cache. These facts are recorded in `oracle_cache/summary.json`, not inferred from new model calls.

At review time, this night's `retrieval/` directory contains only B0, and `report_final_v2` does not provide a B2/B3 downstream retrieval run. Therefore it is incorrect to report B2 or B3 as having experimentally failed downstream based on B0 R2/R3 alone. Historical oracle decomposition supports a mechanism-level concern for the same cached text-proposal construction; an actual predicted-B2 downstream result must be separately identified before attributing measured performance to that arm.

## Revised conclusion and next-step scope

Preserve “B3 was not promoted” as the actual execution history, but replace the aggregate-only rationale with: **B3 shows a small positive-stratum label-agreement signal, without demonstrated support-grounding reliability or retrieval benefit; the unchanged text-proposal mechanism has a severe independent coverage constraint. No automatic promotion follows from this retrospective subgroup audit.** Do not claim all parsing variants worsened.

For a future, separately planned study, prioritize checking support grounding on the fixed positive cohort and separating endpoint proposal failure from strict exclusion failure before spending on another broad parsing or verification run. Keep label agreement, visual grounding, candidate coverage, and achieved retrieval metrics separate. The >=16 cohort remains sensitivity-only and must not replace the full benchmark. Do not use the exposed confirmation cohort to pick a new winner. This review requests no new experiment and changes no main README.
