# Design

This document explains *why* `ArtifactChecklistReceipt` is built the way it is: the non-determinism boundaries, the storage layout, the threat model, and the limitations that are honestly part of the design rather than oversights.

## 1. The core problem

A checklist receipt is a trust claim: "this exact artifact, fetched right now, satisfies every one of these structured checks, and the evidence sources that vouch for it are live." Verifying that claim *on-chain* means fetching a real-world artifact and doing real work over it inside a blockchain contract.

A traditional oracle cannot do this without reintroducing trust: a single reporter fetches, checks, and signs a value, and the chain accepts it. GenLayer can, because the chain itself runs the non-deterministic read *independently on every validator* and only writes state when the validators agree. This contract is a minimal, self-contained realization of exactly that.

## 2. Independent re-execution, not leader-trust

The single most important design decision is that **the validator does not trust the leader's result; it recomputes it.**

`seal` wraps one `gl.vm.run_nondet(leader_fn, validator_fn)` call. `leader_fn` does the full job: fetch the pinned artifact, re-hash its bytes, fetch every evidence source, evaluate every check over the freshly decoded artifact JSON, compute `artifact_ok`, `evidence_ok`, the per-check results, and the fold, then return `json.dumps(payload, sort_keys=True)`. `validator_fn(leaders_result)` ignores the leader's *content* — it does not parse it, does not check it "looks right" — and instead calls `leader_fn()` itself, producing a second, independent payload from its own fetches and its own hashing, then returns `own_payload == leaders_payload` as exact string equality.

The consequence is structural, not incidental: a leader cannot fabricate an outcome, because a fabricated payload only matches if the validator's *own real fetches and hashing* happened to produce the identical bytes. A leader that returns a plausible-looking but wrong string is simply disagreed with. `tests/direct/test_artifact_checklist_receipt.py::test_validator_rejects_dishonest_leader` asserts precisely this, and `::test_validator_agrees_with_honest_leader` asserts the affirmative case.

## 3. Why the compared payload is fully deterministic

Everything that crosses the equivalence-principle boundary is derived from the fetch *without any model*:

- `artifact_ok`: a boolean over a fetch status, a byte-length bound, and a SHA-256 comparison.
- `evidence_ok`: a boolean over each evidence fetch's status, byte-length bound, and substring test.
- `artifact_sha256`: a SHA-256 hex digest (or `""` when no bytes were obtained).
- `checks`: an ordered list of `{id, pass}` — string ids from the sealed checklist and booleans from `json_path_eq` / `json_number_gte`.
- `fold`: one of three string constants.

There is no free-form generation, no summarization, no classification, no confidence score, and no percentage anywhere. `json_number_gte` compares JSON numbers directly (ints and floats), and `json_path_eq` requires the extracted value to be a *string*, so there is no float-formatting or type-coercion drift. Two honest nodes that observe the same artifact and evidence therefore produce byte-identical payloads with certainty, not with high probability — which is what makes strict equality a sound consensus rule rather than a flaky one.

## 4. Non-determinism boundaries

`self.*` storage is touched **only** outside the non-deterministic section:

1. **Deterministic prologue** (before `run_nondet`): load the checklist, load the submission, and enforce the seal-once rule.
2. **Non-deterministic middle** (inside `run_nondet`): `leader_fn`/`validator_fn` fetch over the web and evaluate checks. Neither closure references `self`; every value they need was copied into plain locals first (`sealed_title`, `sealed_evidence`, `sealed_checks`, `artifact_url`, `expected_sha256`).
3. **Deterministic epilogue** (after `run_nondet` returns): sanitize the consensus payload, write the receipt, and record the seal-once pointer.

This ordering is what prevents the classic bug of reading mutable storage inside a block that runs multiple times (once per validator) — the value read could differ between executions, making the payloads diverge for reasons unrelated to the evidence.

## 5. Storage design

| Field | Type | Purpose |
|---|---|---|
| `checklists` | `TreeMap[str, str]` | `checklist_id` → normalized, canonical (`sort_keys=True`) checklist JSON |
| `submissions` | `TreeMap[str, str]` | `checklist_id` → artifact binding JSON (one per checklist) |
| `receipts` | `TreeMap[str, str]` | `receipt_id` → receipt JSON |
| `sealed` | `TreeMap[str, str]` | `checklist_id` → `receipt_id`; the seal-once index |
| `checklist_counter` | `u256` | checklists opened; next id is `cl-<counter>` |
| `receipt_counter` | `u256` | receipts issued; next id is `receipt-<counter>` |

Everything stored is a JSON string, so the storage encoder never has to handle a nested dataclass or a list-valued field.

Generic storage classes are allocated by the SDK from their bare annotations; only the scalar counters are assigned in `__init__` (see §10).

## 6. Threat model

| Threat | How it is closed or bounded |
|---|---|
| **Fabricated outcome accepted because a validator only checked the shape of the leader's claim** | The validator re-runs `leader_fn` itself and compares full payloads byte-for-byte. It never trusts the leader's content. |
| **A required fetch silently fails and the fold still reads as a success** | Any failed artifact or evidence fetch sets `artifact_ok`/`evidence_ok` to `False`, which forces the fold to `unresolved`. `met` is structurally impossible without a matching digest and every evidence source. |
| **Artifact swapped after the checklist was written** | The pinned `artifact_sha256` is compared against the digest every validator recomputes. Different bytes produce a different digest → `unresolved`. |
| **Prose in the artifact or evidence steers the outcome** | No fetched content is ever interpreted as language. Checks read structured JSON paths only; evidence needles are substring tests. |
| **Unbounded consensus work from a huge artifact or evidence source** | The artifact is bounded to 5,000,000 bytes and each evidence source to 12,000 bytes; over the bound is treated as a failed required fetch. |
| **Caller-supplied JSON that is not the schema it claims** | Unknown fields are rejected at every level (checklist, check, evidence entry), and every field is type- and length-checked. |
| **Unbounded or structurally dangerous URLs** | URLs must be `https://` with a present host and no embedded credentials or whitespace. |
| **Re-sealing a checklist to try to change its recorded outcome** | The seal-once index makes a second `seal` for a checklist revert, for **every** fold (including `unresolved`). |
| **Serialization drift between nodes** | Every stored and compared value is a JSON string produced with `sort_keys=True`; the payload contains only strings, booleans, JSON numbers, and lists of flat objects. |

## 7. The seal-once rule

A checklist is a durable claim, so its outcome is immutable: `seal(checklist_id)` writes the receipt and the `sealed[checklist_id]` pointer atomically in the deterministic epilogue, and any later `seal` for the same checklist reverts with `checklist already sealed`.

This differs from a "retryable unresolved" design on purpose. An `unresolved` round here means a required source could not be obtained at seal time; rather than silently re-reading a source later (which would let an observer pick *when* the claim is judged), the contract records the non-outcome as the checklist's final answer. `met` is the only positive fold, and callers must gate on it.

## 8. Size and shape bounds

| Bound | Value | Why |
|---|---|---|
| checks per checklist | 8 | bounds the deterministic work per validator |
| check id length | 32 | keeps ids small and printable |
| path length | 128 | bounds JSON walk cost |
| `expect` length | 64 | `json_path_eq` compares short strings |
| evidence sources | 2 | bounds the number of required fetches |
| evidence needle length | 256 | bounded substring test |
| evidence body | 12,000 bytes | bounds each required fetch |
| artifact body | 5,000,000 bytes | bounds the single largest required fetch and the re-hash |
| title length | 80 | display-only field |

## 9. Limitations (honest)

- The contract verifies only what the checks express. It is not a general-purpose JSON-schema validator; a caller encodes intent as `json_path_eq` and `json_number_gte` checks.
- `json_number_gte` uses Python's numeric comparison over JSON numbers, so very large integers beyond double precision are an unusual case a caller should avoid by using string equality instead.
- Evidence needles are exact substring matches; there is no case-folding or normalization.
- The artifact digest is the only integrity binding. If a source is mutable and the caller does not pin a digest, the checklist has nothing to anchor to — so pinning is required, not optional.
- There is no ERC-20/721 interaction, no funds, no access control, and no upgrade path: by design.

## 10. SDK layout notes

The contract header is the canonical two-line GenLayer header:

```
# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }
```

Under the pinned runner (`genvm-manager v0.6.0-rc8`, which ships that exact `5jycge4q…` bundle), the Python SDK is the `genlayer` package whose top level exposes submodules lazily. `from genlayer import *` no longer exports a `gl` alias — that alias belonged to the older flat `genlayer.gl` layout. The idiomatic, working pattern under this runner is:

```python
from genlayer import *
import genlayer as gl
from genlayer.storage import TreeMap
```

So the contract declares its base as `gl.contract.Contract`, uses `gl.public.write` / `gl.public.view`, `gl.vm.run_nondet`, `gl.vm.Return`, `gl.vm.UserError`, `gl.nondet.web.get`, and `gl.message.sender_address`. Storage generics (`TreeMap`, `u256`) come from the star import and `genlayer.storage`; `u256` is available from the star import. This is the same import pattern the sibling reference contracts use, and it is verified by `genvm-lint check`/`validate` and the direct test suite.
