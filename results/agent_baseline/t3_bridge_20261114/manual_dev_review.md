# Development input review (incremental, 2026-11-14)

These are individual visual audits of the exact transmitted inputs, not an independent annotation study. No task label, model answer, historical score, or display mechanism was modified.

## train_1496_2to1: support versus holes

- Task label: non-vegetated ground → low vegetation (surface group).
- Independent outputs: non-vegetated ground → building. T2 rationale describes dense white roofs in parallel rows.
- Actual API input: `dev60/tasks/train_1496_2to1/interpretation/t2/00.png`; full response and usage in the same directory. T1 input is the adjacent `t1/00.png`.
- Original exemplar: `/guisongxia01/pangchao/wangyihan/wyh/exp6_staging/agent_baseline_run/results/agent_baseline/data/t3_tasks_dev_0of1/train_1496_2to1_exemplar.png`.
- Visual audit: the white exemplar support is the connected space between rows of buildings; the roofs are black holes in that support. The contour-only input draws boundaries around those holes. The response describes the roofs rather than the inter-building support. This is evidence of region grounding / foreground-hole ambiguity; it is not sufficient evidence that the VLM cannot recognize low vegetation. The exact botanical state of all inter-building pixels remains unadjudicated.
- Candidate-stage interpretation: after excluding the exemplar, this task has only 4 positive pixels; its per-task F1 is not a robust measure of general semantic retrieval quality.
- Next hypothesis: separately disclose exemplar support to the single-date parser and compare on the same fixed tasks, while retaining the current contour-only control. This is a proposed follow-up, not a completed experiment or a change to the shared renderer.

## train_1177_2to3: off-target reasoning

- Actual paired inputs reviewed: `dev60/tasks/train_1177_2to3/interpretation/t1/00.png` and `t2/00.png`.
- The red marked region lies at the upper edge of the rendered crop; much of the image is neighborhood context. T1 rationale talks about a yard between roofs; T2 calls the region an angular light building. The response is not a reliable description of the thin marked support.
- Task label is ground → tree; prediction is ground → building. This is a provisional grounding failure, not a definitive claim that the ground-truth boundary is exact. Original label/registration adjudication has not been performed.
- Candidate coverage independently remains limited: 1,252 / 1,686 positive pixels covered by the full cached proposal pool; 3 / 4 positive components align under the existing criterion. Correct exemplar interpretation alone cannot fix that missing proposal coverage.

All requests in these audits explicitly used Bailian `qwen3.7-flash`. The current fixed dev60 experiment remains unchanged.
