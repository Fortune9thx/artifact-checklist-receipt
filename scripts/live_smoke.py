"""Live end-to-end smoke test for a deployed ArtifactChecklistReceipt contract.

This drives three consensus transactions against real, committed sources:

  1. ``open_checklist``  - open a checklist over the example artifact
  2. ``submit_artifact`` - pin the artifact URL and its expected SHA-256
  3. ``seal``            - re-fetch, re-hash, and re-check it under consensus

and then reads the resulting receipt back. It is the exact procedure used to
produce the live result recorded in ``README.md``.

It never contains a key. Supply the signing key out of band:

    set ACR_PRIVATE_KEY=0x...            (PowerShell: $env:ACR_PRIVATE_KEY="0x...")
    python scripts/live_smoke.py

Optional environment:

    ACR_ADDRESS        deployed contract address (defaults to the reference deploy)
    ACR_NETWORK        studio-dev | bradbury | asimov | studionet  (default studio-dev)
    ACR_ARTIFACT_URL   https URL of the artifact to pin
    ACR_EVIDENCE_URL   https URL of the evidence source
    ACR_EVIDENCE_NEEDLE  substring that must appear in the evidence source

The script refuses to run without ACR_PRIVATE_KEY. It writes its full result to
``live_smoke_result.json`` next to the current working directory.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.request

from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import (
    localnet,
    studio_devnet,
    studionet,
    testnet_asimov,
    testnet_bradbury,
)

DEFAULT_ADDRESS = "0x12ccdC7A77eBBc5b907293E606EE317B69bcD2c7"
DEFAULT_REPO = "Fortune9thx/artifact-checklist-receipt"
DEFAULT_ARTIFACT_URL = (
    "https://raw.githubusercontent.com/"
    + DEFAULT_REPO
    + "/master/docs/EXAMPLE_ARTIFACT.json"
)
DEFAULT_EVIDENCE_URL = (
    "https://raw.githubusercontent.com/"
    + DEFAULT_REPO
    + "/master/docs/EXAMPLE_EVIDENCE.txt"
)
DEFAULT_EVIDENCE_NEEDLE = "checklist-evidence-ok"

NETWORKS = {
    "studio-dev": studio_devnet,
    "studionet": studionet,
    "localnet": localnet,
    "asimov": testnet_asimov,
    "bradbury": testnet_bradbury,
}


def http_get(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req).read()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ArtifactChecklistReceipt live smoke test"
    )
    parser.add_argument("--address", default=os.environ.get("ACR_ADDRESS", DEFAULT_ADDRESS))
    parser.add_argument("--network", default=os.environ.get("ACR_NETWORK", "studio-dev"))
    parser.add_argument(
        "--artifact-url", default=os.environ.get("ACR_ARTIFACT_URL", DEFAULT_ARTIFACT_URL)
    )
    parser.add_argument(
        "--evidence-url", default=os.environ.get("ACR_EVIDENCE_URL", DEFAULT_EVIDENCE_URL)
    )
    parser.add_argument(
        "--evidence-needle",
        default=os.environ.get("ACR_EVIDENCE_NEEDLE", DEFAULT_EVIDENCE_NEEDLE),
    )
    args = parser.parse_args()

    private_key = os.environ.get("ACR_PRIVATE_KEY")
    if not private_key:
        print("ACR_PRIVATE_KEY is not set; refusing to run.", file=sys.stderr)
        return 2

    account = Account.from_key(private_key)
    client = create_client(chain=NETWORKS[args.network], account=account)
    print(f"signer:  {account.address}", flush=True)
    print(f"contract: {args.address}  network: {args.network}", flush=True)

    artifact_bytes = http_get(args.artifact_url, {"User-Agent": "artifact-checklist-receipt"})
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()

    checklist = {
        "schema": "acr.checklist.v1",
        "title": "example artifact checklist",
        "evidence": [{"url": args.evidence_url, "needle": args.evidence_needle}],
        "checks": [
            {"id": "c1", "kind": "json_path_eq", "path": "status", "expect": "ok"},
            {"id": "c2", "kind": "json_number_gte", "path": "score", "min": 1},
            {
                "id": "c3",
                "kind": "json_path_eq",
                "path": "name",
                "expect": "artifact-checklist-receipt",
            },
        ],
    }
    checklist_json = json.dumps(checklist, sort_keys=True)
    print("checklist: " + checklist_json, flush=True)
    print("artifact_sha256: " + artifact_sha256, flush=True)

    result = {"network": args.network, "address": args.address, "checklist": checklist}

    before = int(client.read_contract(args.address, "get_checklist_count"))

    open_fees = client.estimate_transaction_fees_for_write(
        args.address, "open_checklist", args=[checklist_json]
    )
    open_tx = client.write_contract(
        args.address, "open_checklist", args=[checklist_json], fees=open_fees
    )
    print(f"open_checklist tx: {open_tx}", flush=True)
    client.wait_for_transaction_receipt(
        open_tx, wait_until="finalized", interval=5000, retries=240
    )
    checklist_id = "cl-" + str(
        int(client.read_contract(args.address, "get_checklist_count")) - 1
    )
    result["open_tx"] = str(open_tx)
    result["checklist_id"] = checklist_id
    print(f"checklist_id: {checklist_id}", flush=True)

    submit_fees = client.estimate_transaction_fees_for_write(
        args.address,
        "submit_artifact",
        args=[checklist_id, args.artifact_url, artifact_sha256],
    )
    submit_tx = client.write_contract(
        args.address,
        "submit_artifact",
        args=[checklist_id, args.artifact_url, artifact_sha256],
        fees=submit_fees,
    )
    print(f"submit_artifact tx: {submit_tx}", flush=True)
    client.wait_for_transaction_receipt(
        submit_tx, wait_until="finalized", interval=5000, retries=240
    )
    result["submit_tx"] = str(submit_tx)

    seal_fees = client.estimate_transaction_fees_for_write(
        args.address, "seal", args=[checklist_id]
    )
    seal_tx = client.write_contract(
        args.address, "seal", args=[checklist_id], fees=seal_fees
    )
    print(f"seal tx: {seal_tx}", flush=True)
    client.wait_for_transaction_receipt(
        seal_tx, wait_until="finalized", interval=5000, retries=240
    )
    result["seal_tx"] = str(seal_tx)

    receipt_id = "receipt-" + str(
        int(client.read_contract(args.address, "get_receipt_count")) - 1
    )
    receipt = client.read_contract(args.address, "get_receipt", args=[receipt_id])
    result["receipt_id"] = receipt_id
    result["receipt"] = json.loads(receipt) if isinstance(receipt, str) else receipt
    print(f"receipt_id: {receipt_id}", flush=True)
    print("receipt: " + json.dumps(result["receipt"], sort_keys=True), flush=True)
    print(f"fold: {result['receipt'].get('fold')}", flush=True)

    with open("live_smoke_result.json", "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)

    return 0 if result["receipt"].get("fold") == "met" else 1


if __name__ == "__main__":
    raise SystemExit(main())
