# Confidence is gated by rules, not self-reported by the model

Confidence decides whether a finding becomes an automatic repair or a flag for human review, so it governs the one action synclint takes that is hard to undo. The obvious implementation is to ask the model for a score between 0 and 1.

We don't, because a model's self-reported confidence is poorly calibrated and gives us no way to explain or tune the boundary. Instead, deterministic rules decide what is *eligible* for automatic repair — only narrow, well-understood diff shapes such as a renamed parameter or a changed default value qualify — and the model scores only within that gate. Anything outside it becomes a flag regardless of how certain the model claims to be.

This means the auto-repair path can be described exactly, and its calibration measured against the fixture corpus. Widening it is a deliberate act of adding a rule, not a threshold nudge.
