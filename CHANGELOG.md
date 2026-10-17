# Changelog

All notable changes to this project are documented in this file.

## [1.0.0] - 2026-10-17

Initial release.

- `open_checklist(checklist_json) -> str` — deterministic validation and persistence of an `acr.checklist.v1` checklist (title, up to 2 evidence sources with optional needles, 1–8 structured checks).
- `submit_artifact(checklist_id, artifact_url, artifact_sha256) -> str` — pins one artifact (https URL + expected SHA-256) to a checklist; exactly one submission per checklist.
- `seal(checklist_id) -> str` — independent re-fetch and re-hash of the pinned artifact and independent re-fetch of every evidence source by every validator, with a fully discrete consensus payload and a `met`/`unmet`/`unresolved` fold.
- `get_checklist`, `get_submission`, `get_receipt`, `get_checklist_count`, `get_receipt_count` view methods.
- Two check kinds: `json_path_eq` (string path equality) and `json_number_gte` (numeric lower bound), both over the freshly decoded artifact JSON.
- Seal-once rule: a checklist is sealed exactly once, regardless of fold, so its recorded outcome is immutable.
- Artifact size bound (5,000,000 bytes) and per-evidence size bound (12,000 bytes); over-bound or failed required fetches yield `unresolved`.
- 56 direct-mode tests; `genvm-lint check` and `genvm-lint validate` both clean.
- CI workflow running lint, validation, and tests on every push.

### Fixed during development (before the 1.0.0 tag)

- Replaced direct `TreeMap()` construction in `__init__` with bare-annotation storage fields plus scalar-counter initialization, matching the pinned SDK's storage descriptor model.
- Made the dishonest-leader validator test alter the artifact digest (a payload an honest leader cannot produce) so it asserts genuine disagreement rather than re-deriving the honest payload.
