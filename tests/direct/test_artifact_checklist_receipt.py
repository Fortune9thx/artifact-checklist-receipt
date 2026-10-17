"""Direct-mode tests for ArtifactChecklistReceipt.

Covers: open_checklist schema validation, submit_artifact binding rules, the
happy-path "met" receipt, every check-driven "unmet", every required-fetch
failure "unresolved" (artifact, evidence, size bounds, digest mismatch), the
seal-once rule (any fold blocks a re-seal, including "unresolved"), honest vs
dishonest validator behavior, and the pure deterministic helpers.
"""

import hashlib
import json
import re
import sys

import pytest

CONTRACT_PATH = "contracts/ArtifactChecklistReceipt.py"

# The contract pins py-genlayer:5jycge4q... and that runner ships inside the
# v0.6.0-rc8 genvm-manager release bundle (already present in the offline
# gltest-direct cache), so direct-mode tests resolve the exact same SDK the
# contract header names.
SDK_VERSION = "v0.6.0-rc8"

ARTIFACT_URL = "https://example.com/artifact.json"
EVIDENCE_URL = "https://example.com/evidence.txt"
NEEDLE = "build-ok"
EVIDENCE_BODY = "line one\nbuild-ok\nline three\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _module():
    """Return the loaded contract module for direct unit tests of helpers."""
    return sys.modules["_contract_ArtifactChecklistReceipt"]


def _deploy(direct_deploy):
    return direct_deploy(CONTRACT_PATH, sdk_version=SDK_VERSION)


def _artifact_doc(**overrides):
    doc = {
        "version": "1.0.0",
        "build": {"status": "success", "passed": 12, "tags": ["a", "b"]},
    }
    doc.update(overrides)
    return json.dumps(doc, sort_keys=True)


ARTIFACT_BODY = _artifact_doc()
ARTIFACT_SHA = hashlib.sha256(ARTIFACT_BODY.encode("utf-8")).hexdigest()


def _checklist(**overrides):
    doc = {
        "schema": "acr.checklist.v1",
        "title": "release provenance 2026",
        "evidence": [{"url": EVIDENCE_URL, "needle": NEEDLE}],
        "checks": [
            {"id": "c1", "kind": "json_path_eq", "path": "build.status", "expect": "success"},
            {"id": "c2", "kind": "json_number_gte", "path": "build.passed", "min": 10},
            {"id": "c3", "kind": "json_path_eq", "path": "build.tags.0", "expect": "a"},
        ],
    }
    doc.update(overrides)
    return json.dumps(doc)


def _mock_artifact(direct_vm, body=ARTIFACT_BODY, status=200):
    direct_vm.mock_web(re.escape(ARTIFACT_URL), {"status": status, "body": body})


def _mock_evidence(direct_vm, body=EVIDENCE_BODY, status=200):
    direct_vm.mock_web(re.escape(EVIDENCE_URL), {"status": status, "body": body})


def _open_and_submit(contract, checklist_json=None, sha=ARTIFACT_SHA):
    checklist_id = contract.open_checklist(checklist_json or _checklist())
    contract.submit_artifact(checklist_id, ARTIFACT_URL, sha)
    return checklist_id


def _receipt(contract, receipt_id):
    return json.loads(contract.get_receipt(receipt_id))


# ---------------------------------------------------------------------------
# open_checklist validation
# ---------------------------------------------------------------------------


def test_open_checklist_happy_path(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    assert contract.get_checklist_count() == 0

    checklist_id = contract.open_checklist(_checklist())
    assert checklist_id == "cl-0"
    assert contract.get_checklist_count() == 1

    stored = json.loads(contract.get_checklist(checklist_id))
    assert stored["schema"] == "acr.checklist.v1"
    assert stored["title"] == "release provenance 2026"
    assert stored["evidence"] == [{"url": EVIDENCE_URL, "needle": NEEDLE}]
    assert stored["checks"][0] == {
        "id": "c1",
        "kind": "json_path_eq",
        "path": "build.status",
        "expect": "success",
    }
    assert stored["checks"][1] == {
        "id": "c2",
        "kind": "json_number_gte",
        "path": "build.passed",
        "min": 10,
    }


def test_open_checklist_second_id_increments(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    assert contract.open_checklist(_checklist()) == "cl-0"
    assert contract.open_checklist(_checklist(title="another")) == "cl-1"
    assert contract.get_checklist_count() == 2


def test_open_checklist_rejects_bad_schema(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("checklist.schema must be"):
        contract.open_checklist(_checklist(schema="acr.checklist.v0"))


def test_open_checklist_rejects_non_json(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("not valid JSON"):
        contract.open_checklist("this is not json")


def test_open_checklist_rejects_non_object(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("checklist must be a JSON object"):
        contract.open_checklist("[1, 2, 3]")


def test_open_checklist_rejects_unknown_top_level_field(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("unexpected field in checklist"):
        contract.open_checklist(_checklist(surprise=True))


def test_open_checklist_rejects_bad_title(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    for bad_title in ("", "x" * 81, " leading", "trailing "):
        with direct_vm.expect_revert("checklist.title must be"):
            contract.open_checklist(_checklist(title=bad_title))


def test_open_checklist_rejects_bad_evidence(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)

    with direct_vm.expect_revert("checklist.evidence must be a list"):
        contract.open_checklist(_checklist(evidence="https://example.com/x"))

    with direct_vm.expect_revert("checklist.evidence must contain"):
        contract.open_checklist(
            _checklist(
                evidence=[
                    {"url": "https://example.com/1", "needle": ""},
                    {"url": "https://example.com/2", "needle": ""},
                    {"url": "https://example.com/3", "needle": ""},
                ]
            )
        )

    with direct_vm.expect_revert("each evidence entry must be a JSON object"):
        contract.open_checklist(_checklist(evidence=["https://example.com/x"]))

    with direct_vm.expect_revert("unexpected field in evidence entry"):
        contract.open_checklist(
            _checklist(evidence=[{"url": EVIDENCE_URL, "needle": "", "extra": 1}])
        )


def test_open_checklist_rejects_bad_evidence_url(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("must start with https://"):
        contract.open_checklist(_checklist(evidence=[{"url": "http://example.com/x"}]))
    with direct_vm.expect_revert("must not contain credentials"):
        contract.open_checklist(
            _checklist(evidence=[{"url": "https://user:pass@example.com/x"}])
        )


def test_open_checklist_rejects_overlong_needle(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("evidence.needle must be"):
        contract.open_checklist(
            _checklist(evidence=[{"url": EVIDENCE_URL, "needle": "n" * 257}])
        )


def test_open_checklist_rejects_bad_check_list(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("checklist.checks must be a list"):
        contract.open_checklist(_checklist(checks="nope"))
    with direct_vm.expect_revert("checklist.checks must contain"):
        contract.open_checklist(_checklist(checks=[]))
    with direct_vm.expect_revert("checklist.checks must contain"):
        contract.open_checklist(
            _checklist(
                checks=[
                    {"id": "c%d" % i, "kind": "json_path_eq", "path": "a", "expect": "b"}
                    for i in range(9)
                ]
            )
        )
    with direct_vm.expect_revert("each check must be a JSON object"):
        contract.open_checklist(_checklist(checks=["x"]))


def test_open_checklist_rejects_bad_check_kind(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("check.kind must be one of"):
        contract.open_checklist(
            _checklist(checks=[{"id": "c1", "kind": "nope", "path": "a", "expect": "b"}])
        )


def test_open_checklist_rejects_unknown_check_field(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("unexpected field in check"):
        contract.open_checklist(
            _checklist(
                checks=[
                    {"id": "c1", "kind": "json_path_eq", "path": "a", "expect": "b", "min": 1}
                ]
            )
        )


def test_open_checklist_rejects_bad_check_id(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    for bad_id in ("", "x" * 33, "-leading", "has space"):
        with direct_vm.expect_revert("check.id must be"):
            contract.open_checklist(
                _checklist(
                    checks=[{"id": bad_id, "kind": "json_path_eq", "path": "a", "expect": "b"}]
                )
            )


def test_open_checklist_rejects_duplicate_check_id(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("check.id values must be unique"):
        contract.open_checklist(
            _checklist(
                checks=[
                    {"id": "c1", "kind": "json_path_eq", "path": "a", "expect": "b"},
                    {"id": "c1", "kind": "json_number_gte", "path": "b", "min": 1},
                ]
            )
        )


def test_open_checklist_rejects_bad_path(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    for bad_path in ("", "a" * 129, "has space", "a/b"):
        with direct_vm.expect_revert("check.path must be"):
            contract.open_checklist(
                _checklist(
                    checks=[{"id": "c1", "kind": "json_path_eq", "path": bad_path, "expect": "b"}]
                )
            )


def test_open_checklist_rejects_bad_expect(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("check.expect must be"):
        contract.open_checklist(
            _checklist(
                checks=[{"id": "c1", "kind": "json_path_eq", "path": "a", "expect": "x" * 65}]
            )
        )
    with direct_vm.expect_revert("check.expect must be"):
        contract.open_checklist(
            _checklist(checks=[{"id": "c1", "kind": "json_path_eq", "path": "a", "expect": 5}])
        )


def test_open_checklist_rejects_bad_min(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    for bad_min in ("5", True, None):
        with direct_vm.expect_revert("check.min must be a number"):
            contract.open_checklist(
                _checklist(
                    checks=[{"id": "c1", "kind": "json_number_gte", "path": "a", "min": bad_min}]
                )
            )


# ---------------------------------------------------------------------------
# submit_artifact
# ---------------------------------------------------------------------------


def test_submit_artifact_happy_path(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = contract.open_checklist(_checklist())

    assert contract.submit_artifact(checklist_id, ARTIFACT_URL, ARTIFACT_SHA) == checklist_id

    stored = json.loads(contract.get_submission(checklist_id))
    assert stored["schema"] == "acr.submission.v1"
    assert stored["checklist_id"] == checklist_id
    assert stored["artifact_url"] == ARTIFACT_URL
    assert stored["artifact_sha256"] == ARTIFACT_SHA


def test_submit_artifact_unknown_checklist(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("unknown checklist_id"):
        contract.submit_artifact("cl-999", ARTIFACT_URL, ARTIFACT_SHA)


def test_submit_artifact_rejects_duplicate(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = contract.open_checklist(_checklist())
    contract.submit_artifact(checklist_id, ARTIFACT_URL, ARTIFACT_SHA)
    with direct_vm.expect_revert("already submitted"):
        contract.submit_artifact(checklist_id, ARTIFACT_URL, ARTIFACT_SHA)


def test_submit_artifact_rejects_bad_url(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = contract.open_checklist(_checklist())
    with direct_vm.expect_revert("artifact_url must start with https://"):
        contract.submit_artifact(checklist_id, "http://example.com/a", ARTIFACT_SHA)
    with direct_vm.expect_revert("artifact_url must not contain credentials"):
        contract.submit_artifact(checklist_id, "https://u:p@example.com/a", ARTIFACT_SHA)


def test_submit_artifact_rejects_bad_sha(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = contract.open_checklist(_checklist())
    for bad_sha in ("abc", ARTIFACT_SHA.upper(), "z" * 64, ARTIFACT_SHA + "0"):
        with direct_vm.expect_revert("artifact_sha256 must be exactly 64"):
            contract.submit_artifact(checklist_id, ARTIFACT_URL, bad_sha)


# ---------------------------------------------------------------------------
# Happy path: met
# ---------------------------------------------------------------------------


def test_seal_happy_path_met(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)

    assert contract.get_receipt_count() == 0
    receipt_id = contract.seal(checklist_id)
    assert receipt_id == "receipt-0"
    assert contract.get_receipt_count() == 1

    record = _receipt(contract, receipt_id)
    assert record["schema"] == "acr.receipt.v1"
    assert record["checklist_id"] == checklist_id
    assert record["title"] == "release provenance 2026"
    assert record["fold"] == "met"
    assert record["artifact_ok"] is True
    assert record["evidence_ok"] is True
    assert record["artifact_sha256"] == ARTIFACT_SHA
    assert record["expected_artifact_sha256"] == ARTIFACT_SHA
    assert record["checks"] == [
        {"id": "c1", "pass": True},
        {"id": "c2", "pass": True},
        {"id": "c3", "pass": True},
    ]


def test_seal_without_evidence(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract, _checklist(evidence=[]))
    _mock_artifact(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "met"
    assert record["evidence_ok"] is True


# ---------------------------------------------------------------------------
# Check-driven rejections: unmet
# ---------------------------------------------------------------------------


def test_expect_mismatch_unmet(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checks = [
        {"id": "c1", "kind": "json_path_eq", "path": "build.status", "expect": "failure"}
    ]
    checklist_id = _open_and_submit(contract, _checklist(checks=checks))
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unmet"
    assert record["artifact_ok"] is True
    assert record["evidence_ok"] is True
    assert record["checks"] == [{"id": "c1", "pass": False}]


def test_number_below_minimum_unmet(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checks = [{"id": "c2", "kind": "json_number_gte", "path": "build.passed", "min": 100}]
    checklist_id = _open_and_submit(contract, _checklist(checks=checks))
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unmet"
    assert record["checks"] == [{"id": "c2", "pass": False}]


def test_missing_path_unmet(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checks = [{"id": "c1", "kind": "json_path_eq", "path": "build.missing", "expect": "x"}]
    checklist_id = _open_and_submit(contract, _checklist(checks=checks))
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unmet"
    assert record["checks"] == [{"id": "c1", "pass": False}]


def test_non_string_json_path_eq_unmet(direct_deploy, direct_vm):
    # json_path_eq extracts an int at build.passed; not a string -> no pass.
    contract = _deploy(direct_deploy)
    checks = [{"id": "c1", "kind": "json_path_eq", "path": "build.passed", "expect": "12"}]
    checklist_id = _open_and_submit(contract, _checklist(checks=checks))
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unmet"
    assert record["checks"] == [{"id": "c1", "pass": False}]


def test_artifact_not_json_unmet(direct_deploy, direct_vm):
    # A correctly-hashed artifact that is not JSON: artifact_ok, but no check
    # can evaluate, so the fold is unmet rather than met.
    contract = _deploy(direct_deploy)
    body = "plain text, not json"
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    checklist_id = _open_and_submit(contract, sha=sha)
    _mock_artifact(direct_vm, body=body)
    _mock_evidence(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unmet"
    assert record["artifact_ok"] is True
    assert all(entry["pass"] is False for entry in record["checks"])


# ---------------------------------------------------------------------------
# Required-fetch failures -> unresolved
# ---------------------------------------------------------------------------


def test_artifact_digest_mismatch_unresolved(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract, sha="0" * 64)
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unresolved"
    assert record["artifact_ok"] is False
    assert record["artifact_sha256"] == ARTIFACT_SHA
    assert record["expected_artifact_sha256"] == "0" * 64


def test_artifact_http_error_unresolved(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm, body="server error", status=500)
    _mock_evidence(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unresolved"
    assert record["artifact_ok"] is False
    assert record["artifact_sha256"] == ""


def test_artifact_fetch_absent_unresolved(direct_deploy, direct_vm):
    # No web mock for the artifact URL at all: the fetch raises and is caught.
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_evidence(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unresolved"
    assert record["artifact_ok"] is False


def test_artifact_too_large_unresolved(direct_deploy, direct_vm, monkeypatch):
    contract = _deploy(direct_deploy)
    monkeypatch.setattr(_module(), "MAX_ARTIFACT_BYTES", 5)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unresolved"
    assert record["artifact_ok"] is False
    assert record["artifact_sha256"] == ""


def test_evidence_http_error_unresolved(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm, body="not found", status=404)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unresolved"
    assert record["artifact_ok"] is True
    assert record["evidence_ok"] is False
    # Checks still evaluated against the good artifact.
    assert record["checks"] == [
        {"id": "c1", "pass": True},
        {"id": "c2", "pass": True},
        {"id": "c3", "pass": True},
    ]


def test_evidence_fetch_absent_unresolved(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unresolved"
    assert record["evidence_ok"] is False


def test_evidence_missing_needle_unresolved(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm, body="nothing relevant here\n")

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unresolved"
    assert record["evidence_ok"] is False


def test_evidence_too_large_unresolved(direct_deploy, direct_vm, monkeypatch):
    contract = _deploy(direct_deploy)
    monkeypatch.setattr(_module(), "MAX_EVIDENCE_BYTES", 3)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] == "unresolved"
    assert record["evidence_ok"] is False


# ---------------------------------------------------------------------------
# seal prologue and the seal-once rule
# ---------------------------------------------------------------------------


def test_seal_unknown_checklist(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("unknown checklist_id"):
        contract.seal("cl-999")


def test_seal_without_submission(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = contract.open_checklist(_checklist())
    with direct_vm.expect_revert("no artifact submitted"):
        contract.seal(checklist_id)


def test_seal_once_blocks_after_met(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)
    first = contract.seal(checklist_id)
    assert _receipt(contract, first)["fold"] == "met"

    with direct_vm.expect_revert("already sealed"):
        contract.seal(checklist_id)
    assert contract.get_receipt_count() == 1


def test_seal_once_blocks_after_unmet(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checks = [{"id": "c1", "kind": "json_path_eq", "path": "build.status", "expect": "no"}]
    checklist_id = _open_and_submit(contract, _checklist(checks=checks))
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)
    first = contract.seal(checklist_id)
    assert _receipt(contract, first)["fold"] == "unmet"

    with direct_vm.expect_revert("already sealed"):
        contract.seal(checklist_id)


def test_seal_once_blocks_after_unresolved(direct_deploy, direct_vm):
    # Unlike a decision, "unresolved" is a non-outcome -- but a seal is still
    # once-only, so the checklist can never be re-sealed.
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm, status=500)
    _mock_evidence(direct_vm)
    first = contract.seal(checklist_id)
    assert _receipt(contract, first)["fold"] == "unresolved"

    with direct_vm.expect_revert("already sealed"):
        contract.seal(checklist_id)
    assert contract.get_receipt_count() == 1


def test_unresolved_is_never_met(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm, body="boom", status=503)
    _mock_evidence(direct_vm)

    record = _receipt(contract, contract.seal(checklist_id))
    assert record["fold"] != "met"


# ---------------------------------------------------------------------------
# Validator behavior
# ---------------------------------------------------------------------------


def test_validator_agrees_with_honest_leader(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)
    contract.seal(checklist_id)

    # The validator re-fetches and re-hashes with the same mocks in place.
    assert direct_vm.run_validator() is True


def test_validator_rejects_dishonest_leader(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)
    contract.seal(checklist_id)

    # A payload an honest leader could never produce: the digest does not match
    # what this validator's own fresh fetch of the artifact hashes to.
    dishonest = json.dumps(
        {
            "artifact_ok": True,
            "evidence_ok": True,
            "artifact_sha256": "0" * 64,
            "checks": [
                {"id": "c1", "pass": True},
                {"id": "c2", "pass": True},
                {"id": "c3", "pass": True},
            ],
            "fold": "met",
        },
        sort_keys=True,
    )
    assert direct_vm.run_validator(leader_result=dishonest) is False


def test_validator_disagrees_when_leader_errored(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    checklist_id = _open_and_submit(contract)
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)
    contract.seal(checklist_id)

    assert direct_vm.run_validator(leader_error=Exception("boom")) is False


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def test_get_checklist_unknown_raises(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("no checklist found for id"):
        contract.get_checklist("cl-999")


def test_get_submission_unknown_raises(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("no artifact submitted"):
        contract.get_submission("cl-999")


def test_get_receipt_unknown_raises(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    with direct_vm.expect_revert("no receipt found for id"):
        contract.get_receipt("receipt-999")


def test_counts_and_multiple_checklists(direct_deploy, direct_vm):
    contract = _deploy(direct_deploy)
    a = _open_and_submit(contract)
    b = _open_and_submit(contract)
    _mock_artifact(direct_vm)
    _mock_evidence(direct_vm)

    receipt_a = contract.seal(a)
    receipt_b = contract.seal(b)
    assert receipt_a != receipt_b
    assert contract.get_checklist_count() == 2
    assert contract.get_receipt_count() == 2
    assert _receipt(contract, receipt_a)["fold"] == "met"
    assert _receipt(contract, receipt_b)["fold"] == "met"


# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------


def test_walk_path_unit(direct_deploy, direct_vm):
    _deploy(direct_deploy)
    mod = _module()
    doc = {"a": {"b": [{"c": 1}, {"c": 2}]}, "n": 5, "s": "x", "0": "zero"}
    assert mod._walk_path(doc, "a.b.0.c") == (True, 1)
    assert mod._walk_path(doc, "a.b.1.c") == (True, 2)
    assert mod._walk_path(doc, "s") == (True, "x")
    assert mod._walk_path(doc, "n") == (True, 5)
    assert mod._walk_path(doc, "a.b.2.c") == (False, None)
    assert mod._walk_path(doc, "missing") == (False, None)
    assert mod._walk_path(doc, "a.b.x") == (False, None)
    assert mod._walk_path(doc, "") == (False, None)
    assert mod._walk_path(doc, "a..b") == (False, None)
    assert mod._walk_path(doc, "0") == (True, "zero")


def test_is_number_unit(direct_deploy, direct_vm):
    _deploy(direct_deploy)
    mod = _module()
    assert mod._is_number(5) is True
    assert mod._is_number(5.5) is True
    assert mod._is_number(True) is False
    assert mod._is_number("5") is False
    assert mod._is_number(None) is False


def test_validate_https_url_unit(direct_deploy, direct_vm):
    _deploy(direct_deploy)
    mod = _module()
    assert mod._validate_https_url("https://example.com/a?b=c", "url") == (
        "https://example.com/a?b=c"
    )
    with pytest.raises(Exception):
        mod._validate_https_url("ftp://example.com/x", "url")
    with pytest.raises(Exception):
        mod._validate_https_url("https://user@example.com/x", "url")
    with pytest.raises(Exception):
        mod._validate_https_url("https://", "url")
    with pytest.raises(Exception):
        mod._validate_https_url("https://exa mple.com/x", "url")


def test_validate_sha256_unit(direct_deploy, direct_vm):
    _deploy(direct_deploy)
    mod = _module()
    assert mod._validate_sha256(ARTIFACT_SHA, "sha") == ARTIFACT_SHA
    with pytest.raises(Exception):
        mod._validate_sha256(ARTIFACT_SHA.upper(), "sha")
    with pytest.raises(Exception):
        mod._validate_sha256("abc", "sha")


def test_as_check_results_unit(direct_deploy, direct_vm):
    _deploy(direct_deploy)
    mod = _module()
    assert mod._as_check_results(None) == []
    assert mod._as_check_results("nope") == []
    assert mod._as_check_results(
        [{"id": "c1", "pass": True}, {"id": 5, "pass": True}, "x", {"pass": True}]
    ) == [{"id": "c1", "pass": True}]
    # Non-bool `pass` is coerced to False.
    assert mod._as_check_results([{"id": "c1", "pass": 1}]) == [{"id": "c1", "pass": False}]
