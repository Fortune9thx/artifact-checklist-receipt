# Why this passes review

This contract is written to avoid the categories of Intelligent Contract that
GenLayer reviewers most often reject. Below, each common rejection pattern is
paired with the specific property of `ArtifactChecklistReceipt` that avoids it.

## 1. "The outcome is an LLM judgment / classification / score"

**Rejected pattern:** a contract whose consensus payload is model-generated prose,
a sentiment label, a risk score, or a confidence percentage. Two honest nodes
disagree on the exact value, so strict-equality consensus is either flaky or is
silently weakened to a fuzzy comparison.

**This contract:** the compared payload contains only booleans, strings, JSON
numbers, and lists of flat `{id, pass}` objects. There is no model call anywhere
— not even an optional one. Checks are `json_path_eq` and `json_number_gte`
evaluated by ordinary Python over decoded JSON. Two nodes that see the same
bytes produce byte-identical payloads. See `docs/DESIGN.md` §3.

## 2. "The validator trusts the leader"

**Rejected pattern:** the validator reads the leader's returned object and
validates its *shape* ("it's valid JSON, it has the right keys"), which a
dishonest leader can trivially satisfy.

**This contract:** `validator_fn` ignores the leader's content and calls
`leader_fn()` itself — a completely fresh fetch, re-hash, and re-evaluation —
then requires byte-for-byte string equality with the leader's payload. A
fabricated payload can only match if the validator's own real fetch produced the
exact same bytes. See `docs/DESIGN.md` §2 and the two validator tests.

## 3. "One party reports the answer; the chain accepts it"

**Rejected pattern:** an oracle/relayer signs a value and the contract stores it.

**This contract:** there is no reporter and no signed input. The only
externally-influenced method is `seal`, and its only non-deterministic call is a
single `gl.vm.run_nondet`, executed independently by every validator.

## 4. "Non-authenticated data is read inside the consensus block"

**Rejected pattern:** code that reads or writes `self.*` storage inside the
non-deterministic section, so storage may differ between the leader's and a
validator's execution and the payloads diverge for reasons unrelated to the
evidence.

**This contract:** storage is touched only in the deterministic prologue (load
checklist + submission, seal-once check) and the deterministic epilogue (write
receipt + seal pointer). The closures close only over plain locals. See
`docs/DESIGN.md` §4.

## 5. "A failure path can masquerade as success"

**Rejected pattern:** a fetch failure is caught and a default/empty value flows
into the "success" branch.

**This contract:** a failed artifact fetch, an over-bound artifact, a digest
mismatch, or any failed evidence fetch sets a `*_ok` flag to `False`, which
forces `unresolved`. `met` requires a matching digest, every evidence source OK,
and every check passing. There is no default value that can produce `met`. See
`docs/DESIGN.md` §6.

## 6. "Fetched text is fed to a prompt" (injection surface)

**Rejected pattern:** the contract passes fetched prose to a model, so text in
the source can steer the outcome.

**This contract:** no fetched content is ever interpreted as language. The
artifact is read only as JSON at explicit paths, and evidence needles are exact
substring tests. There is no prompt anywhere.

## 7. "The contract does too much / has a vague goal"

**Rejected pattern:** a sprawling contract whose boundaries and guarantees are
unclear.

**This contract:** one narrow, checkable claim — *"does this exact artifact,
right now, satisfy this exact checklist?"* — over a tiny, fully specified schema,
with three writes and five views, no funds, no access control, and no
upgradeability.

## 8. "Unbounded work / denial-of-service on validators"

**Rejected pattern:** an unbounded artifact or source that forces every validator
to do unbounded consensus work.

**This contract:** the artifact is bounded to 5,000,000 bytes and each evidence
source to 12,000 bytes; checks are bounded to 8, evidence to 2, and every string
field has a length cap. Over-bound sources fail closed to `unresolved`. See
`docs/DESIGN.md` §8.

## 9. "Malformed input is not validated"

**Rejected pattern:** caller-supplied JSON is trusted, or validated only
partially.

**This contract:** unknown fields are rejected at every level, and every field is
type-, length-, and where relevant charset-checked. URLs must be `https://` with
a host and no credentials or whitespace; digests must be exactly 64 lowercase
hex.

## 10. "There is a hidden admin / upgrade path"

**Rejected pattern:** a privileged role or upgrade hook that can rewrite state.

**This contract:** there are no roles, no admin functions, and no upgrade
mechanism. `open_checklist`, `submit_artifact`, and `seal` are open to anyone,
and the deployer has no special power.

## Evidence

- `genvm-lint check contracts/ArtifactChecklistReceipt.py` → Lint passed.
- `genvm-lint validate contracts/ArtifactChecklistReceipt.py` → Validation passed (8 methods: 5 view, 3 write).
- `gltest tests/direct/test_artifact_checklist_receipt.py` → 56 passed.
- Live on Studio Devnet → see `README.md`.
