# Spotlight support audit — incremental evidence

These two historically documented examples were reviewed with their identities known. This is a mechanism audit, **not a blind accuracy assessment** and not a confirmation-set estimate. Existing labels and task definitions remain unchanged.

## train_1496_2to1, T2

Actual sent image: `B1/tasks/train_1496_2to1/t2/00.png`; request, response and usage are adjacent. The displayed support is the network of ground between buildings, while roof holes and the external neighborhood are visibly darkened. The VLM nevertheless describes bright rectangular rooftops and calls the region building, explicitly reversing the visual support interpretation. The support transform has the intended polarity; this example does not show that the transform solved grounding. The old contour-only prediction also called T2 building.

## train_1177_2to3, T1

Actual sent image: `B1/tasks/train_1177_2to3/t1/00.png`. The unchanged support is a thin bright strip at the upper edge. The response instead describes the large rectangular roof at lower left and buildings at upper right, which are darkened non-support. This is concrete off-target reasoning, not merely disagreement with a category label. The input rendering itself preserves the intended support.

## Next falsifiable hypothesis

A relative-brightness cue may fail to bind attention to a small or perforated support; mean close-view support across the fixed60 is only 2.74%. An explicit binary support panel or a tightly localized support chip would test a different representation mechanism. This is a proposed next comparison, not an implemented replacement. The frozen B1/B2 experiments continue unchanged. Selection for any follow-up must use the tuning partition, with confirmation reported separately; existing development-image exposure remains disclosed.

B1 changes both support rendering and the necessary support-description instruction. B2 additionally exposes unmodified images of both dates. Neither comparison should be described as a perfectly isolated brightness-only intervention.
