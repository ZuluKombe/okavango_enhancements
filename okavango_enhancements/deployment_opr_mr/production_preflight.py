"""Read-only OPR/MR deployment readiness review.

No database writes, commits, migrations or workflow activation.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "metadata"

EXPECTED = {
    "Client Script": 1,
    "Custom Field": 6,
    "DocType": 1,
    "Property Setter": 5,
    "Role": 6,
    "Workflow": 2,
    "Workflow Action Master": 11,
    "Workflow State": 9,
}

TARGET_WORKFLOWS = {
    "Material Request - COO Approval": "Material Request",
    "Okavango Operational Payment Approval":
        "Operational Payment Request",
}


def load_records():
    manifest = json.loads(
        (ROOT / "manifest.json").read_text()
    )

    for flag in (
        "installation_enabled",
        "workflow_activation_approved",
        "business_transactions_included",
    ):
        if manifest.get(flag) is not False:
            raise RuntimeError(
                "STOP: Unsafe manifest flag: " + flag
            )

    records = []
    keys = set()

    for entry in manifest["records"]:
        doctype = entry["doctype"]
        name = entry["name"]
        key = (doctype, name)

        if key in keys:
            raise RuntimeError("Duplicate: " + str(key))
        keys.add(key)

        path = (ROOT / entry["file"]).resolve()

        if not path.is_relative_to(ROOT.resolve()):
            raise RuntimeError("Unsafe metadata path")

        payload_bytes = path.read_bytes()
        payload = json.loads(payload_bytes)

        if payload.get("name") != name:
            raise RuntimeError("Record name mismatch")

        checksum = entry.get("sha256")
        if checksum and hashlib.sha256(
            payload_bytes
        ).hexdigest() != checksum:
            raise RuntimeError("Checksum mismatch: " + name)

        records.append((doctype, name, payload))

    counts = Counter(dt for dt, _, _ in records)

    if dict(counts) != EXPECTED:
        raise RuntimeError(
            "Unexpected package counts: " + str(counts)
        )

    return records


def preview_package():
    records = load_records()

    print("===== PRODUCTION PACKAGE PREFLIGHT =====")
    print("Configuration records:", len(records))

    for doctype, count in sorted(
        Counter(dt for dt, _, _ in records).items()
    ):
        print(doctype + ":", count)

    print("Installation authorized: NO")
    print("Workflow activation authorized: NO")
    print("Business transactions included: NO")
    print("PACKAGE INTEGRITY: PASS")


def preview_live():
    """Invoke only within an initialized Frappe site context."""
    import frappe

    site = str(getattr(frappe.local, "site", "") or "")

    if not site:
        raise RuntimeError("No initialized Frappe site")

    records = load_records()

    print("===== LIVE CONFIGURATION REVIEW =====")
    print("Site:", site)
    print("Read-only inspection: YES")

    present = []
    missing = []

    for doctype, name, payload in records:
        if frappe.db.exists(doctype, name):
            present.append((doctype, name))
        else:
            missing.append((doctype, name))

    print("Existing package records:", len(present))
    print("Missing package records:", len(missing))

    for doctype, name in missing:
        print("MISSING:", doctype, "|", name)

    print("\n===== EXISTING WORKFLOWS =====")

    for expected, target_doctype in TARGET_WORKFLOWS.items():
        workflows = frappe.get_all(
            "Workflow",
            filters={"document_type": target_doctype},
            fields=["name", "is_active"],
        )

        print("Target:", target_doctype)

        for workflow in workflows:
            status = (
                "ACTIVE" if workflow.is_active
                else "INACTIVE"
            )
            print(" ", workflow.name, "|", status)

        if not workflows:
            print("  No existing workflow")

    print("\n===== FEATURE FLAGS =====")

    for flag in (
        "okavango_opr_expense_enabled",
        "okavango_auto_mr_issue_enabled",
    ):
        print(flag + ":", frappe.conf.get(flag, 0))

    print("\n===== IMPORTANT =====")
    print("Existing records require field-level comparison.")
    print("No records have been updated.")
    print("No workflow has been activated.")
    print("No transaction has been modified.")
    print("RESULT: READ-ONLY REVIEW COMPLETE")

    return {
        "existing": len(present),
        "missing": len(missing),
        "site": site,
    }


if __name__ == "__main__":
    preview_package()
