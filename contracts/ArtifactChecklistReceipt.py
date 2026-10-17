# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

"""
ArtifactChecklistReceipt -- a standalone GenLayer Intelligent Contract that
seals a structured checklist together with a pinned artifact, and issues a
receipt only when every validator independently re-fetches and re-hashes that
artifact, independently re-fetches every declared evidence source, and then
re-runs the same deterministic checks over the freshly fetched bytes.

-----------------------------------------------------------------------------
WHAT THIS CONTRACT DOES
-----------------------------------------------------------------------------
The lifecycle has three deterministic-then-non-deterministic steps:

  1. `open_checklist(checklist_json)` stores a checklist. A checklist is a
     small, structured object -- never prose:

         {
           "schema": "acr.checklist.v1",
           "title": "release-2026-01 provenance",      # 1..80 characters
           "evidence": [                                # 0..2 sources
             {"url": "https://...", "needle": "build-ok"}
           ],
           "checks": [                                   # 1..8 checks
             {"id": "c1", "kind": "json_path_eq",
              "path": "build.status", "expect": "success"},
             {"id": "c2", "kind": "json_number_gte",
              "path": "build.passed", "min": 10}
           ]
         }

     `open_checklist` performs no fetch. It only validates the caller's JSON
     against the fixed schema and persists the normalized result.

  2. `submit_artifact(checklist_id, artifact_url, artifact_sha256)` binds the
     checklist to one pinned artifact: an https URL plus the expected sha256 of
     its bytes. Exactly one submission is allowed per checklist.

  3. `seal(checklist_id)` is the one non-deterministic operation. The leader
     AND every validator, independently, re-fetch the pinned artifact and every
     evidence source from scratch, re-hash the artifact bytes, and re-evaluate
     every check against the freshly fetched artifact. Only a payload that every
     validator reproduces byte-for-byte is accepted.

     The resulting receipt records the fold -- "met", "unmet", or "unresolved"
     -- together with the recomputed artifact digest and the per-check results.

-----------------------------------------------------------------------------
WHY VALIDATORS RE-FETCH AND RE-HASH (the non-determinism boundary)
-----------------------------------------------------------------------------
The only externally-influenced operation in this contract is `seal`, and the
only non-deterministic call inside it is the single
`gl.vm.run_nondet(leader_fn, validator_fn)` call. `leader_fn` and `validator_fn`
are the ONLY places that touch `gl.nondet.web.get`. They never read or write
`self.*`; every value they need is passed in as a plain local captured by the
closure, so the closures stay serializable and the consensus-critical section
has no storage access at all.

The receipt is written only in the deterministic epilogue, after consensus. The
artifact digest recorded on-chain is the one every validator recomputed, so a
caller can never substitute earlier bytes: changing the artifact after the fact
changes its digest and flips the fold to "unresolved" (or "unmet").

-----------------------------------------------------------------------------
THE FOLD
-----------------------------------------------------------------------------
  "met"        -- the artifact was fetched, its digest equalled the pinned
                  digest, every evidence source was fetched successfully, and
                  every check passed.
  "unmet"      -- the artifact and every evidence source were obtained, but at
                  least one check failed.
  "unresolved" -- a required fetch (the artifact, or any evidence source)
                  failed, exceeded its size bound, or hashed to something other
                  than the pinned digest. No check result can promote this to
                  "met".

`unresolved` is a non-outcome, but sealing a checklist is once-only: the receipt
is written for every fold, and after it exists no second `seal` for that
checklist is accepted. This keeps every checklist's outcome immutable.

-----------------------------------------------------------------------------
WHAT THIS CONTRACT DELIBERATELY DOES NOT DO
-----------------------------------------------------------------------------
It holds no funds, transfers nothing, and computes no score. It interprets no
free-form text: every check reads a structured JSON path, and evidence needles
are plain substring tests. A receipt is a record, nothing more.
"""

from genlayer import *
import genlayer as gl
from genlayer.storage import TreeMap
import json
import typing
import hashlib


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHECKLIST_SCHEMA = "acr.checklist.v1"
RECEIPT_SCHEMA = "acr.receipt.v1"

MIN_CHECKS = 1
MAX_CHECKS = 8
MAX_CHECK_ID_CHARS = 32
MAX_PATH_CHARS = 128
MAX_EXPECT_CHARS = 64

MIN_EVIDENCE = 0
MAX_EVIDENCE = 2
MAX_NEEDLE_CHARS = 256
MAX_EVIDENCE_BYTES = 12_000

# The artifact is re-fetched and re-hashed by every validator, so its size is
# bounded to keep that consensus work bounded. An artifact larger than this is
# treated as a failed required fetch ("unresolved"), never partially read.
MAX_ARTIFACT_BYTES = 5_000_000

MAX_TITLE_CHARS = 80

ALLOWED_CHECK_KINDS = ("json_path_eq", "json_number_gte")
ALLOWED_FOLDS = ("met", "unmet", "unresolved")

_HEX = "0123456789abcdef"
_DIGITS = "0123456789"
_ID_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
_PATH_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


class ArtifactChecklistReceipt(gl.contract.Contract):
    checklists: TreeMap[str, str]
    submissions: TreeMap[str, str]
    receipts: TreeMap[str, str]
    sealed: TreeMap[str, str]
    checklist_counter: u256
    receipt_counter: u256

    def __init__(self):
        # Only the two scalar counters are assigned here. The generic storage
        # classes (TreeMap) are storage-backed descriptors the framework
        # allocates from their bare annotations; constructing one directly is
        # forbidden. Their slots therefore read as empty from the first block
        # onward without an explicit assignment.
        self.checklist_counter = u256(0)
        self.receipt_counter = u256(0)

    # -----------------------------------------------------------------
    # Public write: open a checklist
    # -----------------------------------------------------------------
    @gl.public.write
    def open_checklist(self, checklist_json: str) -> str:
        """Validate and persist a checklist, returning its checklist_id.

        Fully deterministic: no fetch, no non-deterministic call. Only the
        caller-supplied JSON is validated against the fixed schema and the
        normalized result stored.
        """
        normalized = _validate_checklist(checklist_json)

        checklist_id = "cl-" + str(int(self.checklist_counter))
        self.checklist_counter = u256(int(self.checklist_counter) + 1)
        self.checklists[checklist_id] = json.dumps(normalized, sort_keys=True)
        return checklist_id

    # -----------------------------------------------------------------
    # Public write: pin the artifact for a checklist
    # -----------------------------------------------------------------
    @gl.public.write
    def submit_artifact(
        self, checklist_id: str, artifact_url: str, artifact_sha256: str
    ) -> str:
        """Bind one pinned artifact (URL + expected sha256) to a checklist.

        Exactly one submission is allowed per checklist. The binding is stored
        as plain JSON; nothing is fetched here.
        """
        if self.checklists.get(checklist_id) is None:
            raise gl.vm.UserError("unknown checklist_id: " + str(checklist_id))

        if self.submissions.get(checklist_id) is not None:
            raise gl.vm.UserError(
                "an artifact is already submitted for checklist " + str(checklist_id)
            )

        url = _validate_https_url(artifact_url, "artifact_url")
        digest = _validate_sha256(artifact_sha256, "artifact_sha256")

        record = {
            "schema": "acr.submission.v1",
            "checklist_id": checklist_id,
            "artifact_url": url,
            "artifact_sha256": digest,
            "submitted_by": str(gl.message.sender_address),
        }
        self.submissions[checklist_id] = json.dumps(record, sort_keys=True)
        return checklist_id

    # -----------------------------------------------------------------
    # Public write: seal the checklist against its pinned artifact
    # -----------------------------------------------------------------
    @gl.public.write
    def seal(self, checklist_id: str) -> str:
        """Re-fetch the pinned artifact and evidence, and issue a receipt.

        The deterministic prologue loads the checklist and submission and
        enforces the seal-once rule. The non-deterministic middle contains
        exactly ONE `run_nondet` call. The deterministic epilogue writes
        storage. No storage access happens inside `leader_fn`/`validator_fn`,
        and neither closure holds a reference to `self`.
        """
        # ---- Deterministic prologue: load and validate the checklist ----
        raw_checklist = self.checklists.get(checklist_id)
        if raw_checklist is None:
            raise gl.vm.UserError("unknown checklist_id: " + str(checklist_id))
        try:
            checklist = json.loads(raw_checklist)
        except (ValueError, TypeError):
            raise gl.vm.UserError("stored checklist is not valid JSON")

        # ---- Deterministic prologue: load the pinned artifact ----
        raw_submission = self.submissions.get(checklist_id)
        if raw_submission is None:
            raise gl.vm.UserError(
                "no artifact submitted for checklist " + str(checklist_id)
            )
        try:
            submission = json.loads(raw_submission)
        except (ValueError, TypeError):
            raise gl.vm.UserError("stored submission is not valid JSON")

        # ---- Deterministic prologue: seal-once, regardless of fold ----
        if self.sealed.get(checklist_id) is not None:
            raise gl.vm.UserError("checklist already sealed: " + str(checklist_id))

        # Copy every binding value into plain locals. The closures below close
        # ONLY over these locals (never over `self`), which keeps the
        # non-deterministic section free of storage access and keeps the
        # closures cloudpickle-serializable for the validator sandbox.
        sealed_title = str(checklist.get("title", ""))
        sealed_evidence = [
            {"url": str(item.get("url", "")), "needle": str(item.get("needle", ""))}
            for item in checklist.get("evidence", [])
        ]
        sealed_checks = [
            {
                "id": str(item.get("id", "")),
                "kind": str(item.get("kind", "")),
                "path": str(item.get("path", "")),
                "expect": str(item.get("expect", "")),
                "min": item.get("min"),
            }
            for item in checklist.get("checks", [])
        ]
        artifact_url = str(submission.get("artifact_url", ""))
        expected_sha256 = str(submission.get("artifact_sha256", ""))

        # -----------------------------------------------------------------
        # NON-DETERMINISTIC SECTION. Exactly one `run_nondet` call. Every
        # gl.nondet.web.get lives inside these two closures. Neither closure
        # reads or writes self.*.
        # -----------------------------------------------------------------
        def leader_fn() -> str:
            """Freshly fetch the artifact and evidence and derive the payload."""
            # --- Required fetch 1: the pinned artifact ---
            artifact_ok = False
            artifact_sha256 = ""
            artifact_doc = None
            try:
                artifact_resp = gl.nondet.web.get(
                    artifact_url,
                    headers={"User-Agent": "artifact-checklist-receipt"},
                )
                if (
                    200 <= artifact_resp.status < 300
                    and artifact_resp.body is not None
                ):
                    artifact_bytes = bytes(artifact_resp.body)
                    if len(artifact_bytes) <= MAX_ARTIFACT_BYTES:
                        artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
                        if artifact_sha256 == expected_sha256:
                            artifact_ok = True
                            try:
                                artifact_doc = json.loads(
                                    artifact_bytes.decode("utf-8")
                                )
                            except (ValueError, TypeError, UnicodeDecodeError):
                                artifact_doc = None
            except Exception:
                # A failed required fetch yields artifact_ok=False, which forces
                # the fold to "unresolved" below -- never "met".
                artifact_ok = False
                artifact_sha256 = ""
                artifact_doc = None

            # --- Required fetches 2..n: the declared evidence sources ---
            evidence_ok = True
            for item in sealed_evidence:
                source_ok = False
                try:
                    evidence_resp = gl.nondet.web.get(
                        item["url"],
                        headers={"User-Agent": "artifact-checklist-receipt"},
                    )
                    if (
                        200 <= evidence_resp.status < 300
                        and evidence_resp.body is not None
                    ):
                        evidence_bytes = bytes(evidence_resp.body)
                        if len(evidence_bytes) <= MAX_EVIDENCE_BYTES:
                            needle = item["needle"]
                            if needle == "":
                                source_ok = True
                            else:
                                try:
                                    source_ok = needle in evidence_bytes.decode("utf-8")
                                except (UnicodeDecodeError, ValueError):
                                    source_ok = False
                except Exception:
                    source_ok = False
                if not source_ok:
                    evidence_ok = False
                    break

            # --- Deterministic checks over the freshly fetched artifact ---
            check_results = []
            for check in sealed_checks:
                passed = False
                if artifact_ok and artifact_doc is not None:
                    found, value = _walk_path(artifact_doc, check["path"])
                    if found:
                        if check["kind"] == "json_path_eq":
                            passed = (
                                isinstance(value, str) and value == check["expect"]
                            )
                        elif check["kind"] == "json_number_gte":
                            passed = _is_number(value) and value >= check["min"]
                check_results.append({"id": check["id"], "pass": passed})

            # --- The fold is a pure function of the two *_ok flags + checks ---
            if artifact_ok and evidence_ok:
                fold = (
                    "met"
                    if all(entry["pass"] for entry in check_results)
                    else "unmet"
                )
            else:
                fold = "unresolved"

            payload = {
                "artifact_ok": artifact_ok,
                "evidence_ok": evidence_ok,
                "artifact_sha256": artifact_sha256,
                "checks": check_results,
                "fold": fold,
            }
            return json.dumps(payload, sort_keys=True)

        def validator_fn(leaders_result) -> bool:
            """Independently reproduce the leader's full payload and compare.

            Returns True only if the leader returned a *string* payload AND this
            validator's own fresh fetch + re-hash + re-evaluation produced a
            byte-for-byte identical normalized payload. Any exception is treated
            as disagreement.
            """
            if not isinstance(leaders_result, gl.vm.Return):
                return False
            try:
                claimed = leaders_result.calldata
            except Exception:
                return False
            if not isinstance(claimed, str):
                return False
            try:
                independently_derived = leader_fn()
            except Exception:
                return False
            return independently_derived == claimed

        payload_json = gl.vm.run_nondet(leader_fn, validator_fn)

        # -----------------------------------------------------------------
        # DETERMINISTIC EPILOGUE. Reached only after consensus. Plain state
        # mutation, identical on every node.
        # -----------------------------------------------------------------
        if not isinstance(payload_json, str):
            raise gl.vm.UserError("consensus returned a non-string payload")
        try:
            payload = json.loads(payload_json)
        except (ValueError, TypeError):
            raise gl.vm.UserError("consensus payload was not valid JSON")
        if not isinstance(payload, dict):
            raise gl.vm.UserError("consensus payload was not a JSON object")

        fold = payload.get("fold")
        if fold not in ALLOWED_FOLDS:
            raise gl.vm.UserError("consensus returned an unknown fold")

        receipt_id = "receipt-" + str(int(self.receipt_counter))
        self.receipt_counter = u256(int(self.receipt_counter) + 1)

        record = {
            "schema": RECEIPT_SCHEMA,
            "receipt_id": receipt_id,
            "checklist_id": checklist_id,
            "title": sealed_title,
            "fold": fold,
            # "met" is the ONLY fold that is a positive outcome. "unmet" is a
            # decision and "unresolved" is a non-outcome; callers must read the
            # fold and must never treat either as "met".
            "artifact_ok": _as_bool(payload.get("artifact_ok")),
            "evidence_ok": _as_bool(payload.get("evidence_ok")),
            "artifact_url": artifact_url,
            "artifact_sha256": _as_str(payload.get("artifact_sha256")),
            "expected_artifact_sha256": expected_sha256,
            "checks": _as_check_results(payload.get("checks")),
            "submitted_by": str(gl.message.sender_address),
        }

        self.receipts[receipt_id] = json.dumps(record, sort_keys=True)
        self.sealed[checklist_id] = receipt_id
        return receipt_id

    # -----------------------------------------------------------------
    # Public views
    # -----------------------------------------------------------------
    @gl.public.view
    def get_checklist(self, checklist_id: str) -> str:
        """Return the normalized checklist JSON, or raise if unknown."""
        raw = self.checklists.get(checklist_id)
        if raw is None:
            raise gl.vm.UserError("no checklist found for id: " + str(checklist_id))
        return raw

    @gl.public.view
    def get_submission(self, checklist_id: str) -> str:
        """Return the submitted artifact binding JSON, or raise if absent."""
        raw = self.submissions.get(checklist_id)
        if raw is None:
            raise gl.vm.UserError(
                "no artifact submitted for checklist " + str(checklist_id)
            )
        return raw

    @gl.public.view
    def get_receipt(self, receipt_id: str) -> str:
        """Return the full receipt JSON, or raise if unknown."""
        raw = self.receipts.get(receipt_id)
        if raw is None:
            raise gl.vm.UserError("no receipt found for id: " + str(receipt_id))
        return raw

    @gl.public.view
    def get_checklist_count(self) -> u256:
        return self.checklist_counter

    @gl.public.view
    def get_receipt_count(self) -> u256:
        return self.receipt_counter


# ---------------------------------------------------------------------------
# Module-level deterministic helpers
#
# Pure functions -- no fetch, no model, no storage. Used both in the
# deterministic validation path and inside the leader/validator closures.
# Because they are module-level (not methods and not closures over `self`), the
# closures that call them stay free of any contract-instance reference.
# ---------------------------------------------------------------------------


def _validate_checklist(checklist_json: typing.Any) -> dict:
    """Validate `checklist_json` against acr.checklist.v1 and normalize it.

    Raises gl.vm.UserError on any violation. Unknown fields are rejected so a
    stored checklist is exactly the schema it claims to be.
    """
    if not isinstance(checklist_json, str):
        raise gl.vm.UserError("checklist_json must be a string")
    try:
        doc = json.loads(checklist_json)
    except (ValueError, TypeError):
        raise gl.vm.UserError("checklist_json is not valid JSON")
    if not isinstance(doc, dict):
        raise gl.vm.UserError("checklist must be a JSON object")

    _reject_unknown_fields(doc, ("schema", "title", "evidence", "checks"), "checklist")

    if doc.get("schema") != CHECKLIST_SCHEMA:
        raise gl.vm.UserError("checklist.schema must be " + CHECKLIST_SCHEMA)

    title = doc.get("title")
    if (
        not isinstance(title, str)
        or not title
        or len(title) > MAX_TITLE_CHARS
        or title != title.strip()
        or any(ch.isspace() and ch not in " " for ch in title)
    ):
        raise gl.vm.UserError(
            "checklist.title must be 1-" + str(MAX_TITLE_CHARS) + " characters"
        )

    raw_evidence = doc.get("evidence")
    if not isinstance(raw_evidence, list):
        raise gl.vm.UserError("checklist.evidence must be a list")
    if not (MIN_EVIDENCE <= len(raw_evidence) <= MAX_EVIDENCE):
        raise gl.vm.UserError(
            "checklist.evidence must contain "
            + str(MIN_EVIDENCE)
            + "-"
            + str(MAX_EVIDENCE)
            + " entries"
        )
    evidence = []
    for item in raw_evidence:
        if not isinstance(item, dict):
            raise gl.vm.UserError("each evidence entry must be a JSON object")
        _reject_unknown_fields(item, ("url", "needle"), "evidence entry")
        url = _validate_https_url(item.get("url"), "evidence.url")
        needle = item.get("needle", "")
        if not isinstance(needle, str) or len(needle) > MAX_NEEDLE_CHARS:
            raise gl.vm.UserError(
                "evidence.needle must be a string of at most "
                + str(MAX_NEEDLE_CHARS)
                + " characters"
            )
        evidence.append({"url": url, "needle": needle})

    raw_checks = doc.get("checks")
    if not isinstance(raw_checks, list):
        raise gl.vm.UserError("checklist.checks must be a list")
    if not (MIN_CHECKS <= len(raw_checks) <= MAX_CHECKS):
        raise gl.vm.UserError(
            "checklist.checks must contain "
            + str(MIN_CHECKS)
            + "-"
            + str(MAX_CHECKS)
            + " entries"
        )
    checks = []
    seen_ids = set()
    for item in raw_checks:
        if not isinstance(item, dict):
            raise gl.vm.UserError("each check must be a JSON object")
        kind = item.get("kind")
        if kind not in ALLOWED_CHECK_KINDS:
            raise gl.vm.UserError(
                "check.kind must be one of " + ", ".join(ALLOWED_CHECK_KINDS)
            )
        if kind == "json_path_eq":
            _reject_unknown_fields(item, ("id", "kind", "path", "expect"), "check")
        else:
            _reject_unknown_fields(item, ("id", "kind", "path", "min"), "check")

        check_id = _validate_check_id(item.get("id"))
        if check_id in seen_ids:
            raise gl.vm.UserError("check.id values must be unique")
        seen_ids.add(check_id)

        path = item.get("path")
        if (
            not isinstance(path, str)
            or not path
            or len(path) > MAX_PATH_CHARS
            or any(ch not in _PATH_CHARS for ch in path)
        ):
            raise gl.vm.UserError(
                "check.path must be 1-"
                + str(MAX_PATH_CHARS)
                + " characters of [A-Za-z0-9_.-]"
            )

        if kind == "json_path_eq":
            expect = item.get("expect")
            if not isinstance(expect, str) or len(expect) > MAX_EXPECT_CHARS:
                raise gl.vm.UserError(
                    "check.expect must be a string of at most "
                    + str(MAX_EXPECT_CHARS)
                    + " characters"
                )
            checks.append(
                {"id": check_id, "kind": kind, "path": path, "expect": expect}
            )
        else:
            minimum = item.get("min")
            if not _is_number(minimum):
                raise gl.vm.UserError("check.min must be a number")
            checks.append({"id": check_id, "kind": kind, "path": path, "min": minimum})

    return {
        "schema": CHECKLIST_SCHEMA,
        "title": title,
        "evidence": evidence,
        "checks": checks,
    }


def _reject_unknown_fields(doc: dict, allowed: tuple, label: str) -> None:
    """Raise if `doc` contains any key outside `allowed`."""
    for key in doc:
        if key not in allowed:
            raise gl.vm.UserError("unexpected field in " + label + ": " + str(key))


def _validate_check_id(value: typing.Any) -> str:
    """Validate a check id: 1-32 chars of [A-Za-z0-9._-], starting alphanumeric."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_CHECK_ID_CHARS
        or value[0] not in (_ID_CHARS[:62])
        or any(ch not in _ID_CHARS for ch in value)
    ):
        raise gl.vm.UserError(
            "check.id must be 1-"
            + str(MAX_CHECK_ID_CHARS)
            + " characters of [A-Za-z0-9._-], starting alphanumeric"
        )
    return value


def _validate_https_url(value: typing.Any, label: str) -> str:
    """Validate an https URL: scheme, a host, and no embedded credentials.

    Deliberately check-based rather than using urllib: only the authority
    component is inspected for a userinfo (`@`) and for emptiness, so schemes,
    paths, and queries keep full expressiveness while credential embedding and
    hostless URLs are rejected.
    """
    if not isinstance(value, str) or not value.startswith("https://"):
        raise gl.vm.UserError(label + " must start with https://")
    if value != value.strip() or any(ch.isspace() for ch in value):
        raise gl.vm.UserError(label + " must not contain whitespace")
    rest = value[len("https://") :]
    if not rest:
        raise gl.vm.UserError(label + " must have a host")
    end = len(rest)
    for sep in ("/", "?", "#"):
        idx = rest.find(sep)
        if idx != -1 and idx < end:
            end = idx
    authority = rest[:end]
    if not authority:
        raise gl.vm.UserError(label + " must have a host")
    if "@" in authority:
        raise gl.vm.UserError(label + " must not contain credentials")
    return value


def _validate_sha256(value: typing.Any, label: str) -> str:
    """Validate a string as exactly 64 lowercase hexadecimal characters."""
    if not isinstance(value, str) or len(value) != 64:
        raise gl.vm.UserError(label + " must be exactly 64 lowercase hex characters")
    for ch in value:
        if ch not in _HEX:
            raise gl.vm.UserError(
                label + " must be exactly 64 lowercase hex characters"
            )
    return value


def _walk_path(doc: typing.Any, path: str) -> tuple:
    """Walk a dot-separated path into a decoded JSON document.

    Returns `(True, value)` when the path resolves and `(False, None)`
    otherwise. A purely numeric segment indexes a list; on a dict, the segment
    is used as a string key. Returning a tuple (rather than a sentinel value)
    keeps the result picklable and JSON-safe on every path.
    """
    current = doc
    for segment in path.split("."):
        if segment == "":
            return (False, None)
        if isinstance(current, list):
            if any(ch not in _DIGITS for ch in segment):
                return (False, None)
            index = int(segment)
            if index >= len(current):
                return (False, None)
            current = current[index]
        elif isinstance(current, dict):
            if segment not in current:
                return (False, None)
            current = current[segment]
        else:
            return (False, None)
    return (True, current)


def _is_number(value: typing.Any) -> bool:
    """True for a JSON number, and False for a bool (bool is an int subclass)."""
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or isinstance(value, float)


def _as_bool(value: typing.Any) -> bool:
    return value if isinstance(value, bool) else False


def _as_str(value: typing.Any) -> str:
    return value if isinstance(value, str) else ""


def _as_check_results(value: typing.Any) -> list:
    """Sanitize the consensus `checks` list into a list of {id, pass} objects."""
    if not isinstance(value, list):
        return []
    results = []
    for item in value:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        results.append({"id": item_id, "pass": _as_bool(item.get("pass"))})
    return results
