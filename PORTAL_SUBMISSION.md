# Portal Submission

## Project name

ArtifactChecklistReceipt

## Category

Intelligent Contract primitive / on-chain artifact verification

## One-line pitch

A GenLayer Intelligent Contract that pins an artifact by SHA-256 against a structured checklist and issues a receipt only when every validator independently re-fetches and re-hashes the artifact, re-fetches every evidence source, and re-runs every deterministic check.

## Description / Notes

ArtifactChecklistReceipt seals a structured checklist — a title, up to two evidence sources with optional needles, and one to eight checks (`json_path_eq` string equality or `json_number_gte` numeric lower bound over the artifact's JSON) — together with a pinned artifact (an https URL plus its expected SHA-256). To seal, the leader and every validator each fetch the artifact bytes from scratch, re-hash them, fetch every evidence source, and re-run every check over the freshly decoded artifact JSON. A validator never trusts the leader's content: it re-runs the leader function itself and compares the full normalized payload byte-for-byte. There is no model, no confidence score, no percentage, and no partial credit anywhere in the compared payload. The fold is `met` only when the digest matches, every evidence source is obtained, and every check passes; it is `unmet` when the sources are obtained but a check fails, and `unresolved` when any required fetch fails or the digest differs. A checklist is sealed exactly once — the receipt is written for every fold — so its outcome is immutable. No prose is ever interpreted: checks read structured JSON paths and evidence needles are substring tests.

## Deployed contract

- Network: GenLayer Studio Devnet (Studionet)
- Address: `0x12ccdC7A77eBBc5b907293E606EE317B69bcD2c7`
- Deploy transaction: `0x618a8bb0e5a46263f6e550c3f64b35252fba3abce02f170c87be1478d54442c7`
- Final status: `ACCEPTED`

### Live smoke test (end-to-end, real committed sources)

- Checklist opened: `cl-0` — tx `0x5edde9c7d205e6f11f5f849b7e392410cdf4250de89a74f461fd28489fae11d2` (finalized)
- Artifact pinned: tx `0x57e7739ab987bbedb5740bbb7d7245e9fbecab1f7f24b63bdb414714841ae741` (finalized), digest `e92b23b34509b58a99c593aa6a3ecaf0d307f52f88286e4213779c657dd1bcb9`
- Sealed: `receipt-0` — tx `0xe8a4038bb0c869bcf3c052e6ae5e26a392fb9f48cd6d0d7af11ce1e086e62819` (finalized)
- Result: fold `met`; `artifact_ok` and `evidence_ok` both `true`, and all three checks (`status == "ok"`, `score >= 1`, `name == "artifact-checklist-receipt"`) passed; the recomputed `artifact_sha256` equals the pinned digest `e92b23b3…bcb9`.

## Repository

https://github.com/Fortune9thx/artifact-checklist-receipt

## Key features

- **Independent re-execution, not leader-trust** — the validator recomputes the entire payload itself, including the fetch, the re-hash, and every check.
- **Fully deterministic consensus payload** — booleans, strings, JSON numbers, one SHA-256 digest, a list of `{id, pass}` objects, and one fold string. No floats-by-formatting, no model.
- **Fail-closed required fetches** — any fetch failure, over-bound source, or digest mismatch yields `unresolved`; `met` is structurally impossible without a matching digest and every evidence source.
- **Injection-proof** — no fetched content is ever interpreted as language; checks read structured JSON paths only and evidence needles are substring tests.
- **Immutable outcomes** — a checklist is sealed exactly once, for every fold.
- **No privileged roles, no funds, no scores.**

## Evidence of quality

- `genvm-lint check contracts/ArtifactChecklistReceipt.py` → Lint passed (3 checks).
- `genvm-lint validate contracts/ArtifactChecklistReceipt.py` → Validation passed (8 methods: 5 view, 3 write).
- `gltest tests/direct/test_artifact_checklist_receipt.py` → 56 passed.
- Live on Studio Devnet → see `README.md`.
- Design rationale, non-determinism boundaries, and threat model in `docs/DESIGN.md`.
- Rejection-pattern mapping in `docs/WHY_THIS_PASSES_REVIEW.md`.

## Links

- Repository: https://github.com/Fortune9thx/artifact-checklist-receipt
- Contract header pins `py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng` (SDK v0.3.0).
