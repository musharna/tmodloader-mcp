# Lessons

- 2026-11-07: a single-slot safety copy (`auto-before-restore`) was re-taken on every restore, so the retry of a failed restore, or a second restore, saved a half-restored or already-saved state over the only original. Class: a backup taken unconditionally before a retryable operation. Caught now by `tests/test_saves.py` (retry after an injected second-copy OSError; restore A then B), which fail on the old code; not lintable, so no hook.
