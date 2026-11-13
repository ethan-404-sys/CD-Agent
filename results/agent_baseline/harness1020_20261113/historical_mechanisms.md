# Frozen development pilot: historical mechanism evidence

These six archived sessions explain selection; they are not the newly paired baseline and do not establish the result of this repair. All six historically terminated with submit. No bad_id occurred in these six; stale-ID repair remains covered by targeted regression unless a real paired run reproduces it.

| Task / image | Historical configuration | Verifiable steps and attribution |
|---|---|---|
| T1.1xC0 / val_31 | json_qwen37flash_g3_s3 | 11–15 create candidates with set_current=false; 21 ask without current → bad_args; 22 to_mask(mask#9) → bad_handle; 24 repeats missing-current ask. 25 report adopts mask#9, 26 removes valid components, 28 submits. Final 544 FP on empty GT; not an empty submitted answer. State/type repair targets the interaction errors, not the verifier's false positive. |
| T1.1xC0 / val_22 | json_qwen37flash_g3_s3 | 7/10 create mask#5/#6 with set_current=false; 11 ask without current fails; 12 to_mask(mask#6) type error; 13 explicit ask(regions with mask#6) already recovers legally. Historical final F1 .792883. New set_answer cannot claim credit for the historical successful submission. |
| T1.1xC0 / val_44 | json_qwen37flash_g3_s3 | 1/2 separate T1/T2 segment_text; 3 to_mask adopts mask#2; 4 ask; 5/6 report/submit. No tool errors, F1 .923889. Batch-tool and progressive-disclosure simple protection case. |
| T5.1 / val_32 | json_qwen37flash_g3_s1 | Initial mask#0 empty on empty GT, F1 1. 11 select(mask#1) type error; 12 legal component removal adopts mask#2, introduces 4976 FP (F1 0). 14 verifier rejects ID1; 16 planner explicitly disagrees and submits. Primarily visual/planning error; set_answer makes restoring mask#0 possible but cannot make the planner choose it. |
| T5.1 / val_47 | json_qwen37flash_g3_s2 | 18 to_mask(mask#5) type error. 20 union adds FP (1400→19719), TP unchanged at669, F1 .376583→.061174. 24/26 deletion restores .376583. 27 verifier requests restoring original IDs7/9/11; 28 legal add_region adds548 FP, final F1 .326262. Final deterioration is verifier-driven candidate selection, not proven stale-ID corruption. |
| T5.1 / val_36 | json_qwen37flash_g3_s3 | Initial F1 .937541. 7 verifier rejects ID21; 8 view; 9 legal deletion removes485 TP and19 FP, final F1 .930033; 10 submit. No tool errors. Simple protection case with an existing visual/verifier limitation. |

Original sessions: `/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/<configuration>/<task>/val/<image>/`; check `input.json`, `steps.jsonl`, and `final.json`. Frozen selection: `results/agent_baseline/answer_state_20261113/selection.json`. New baseline uses a common seed offset; do not conflate historical runs/seeds with the new paired arms.
