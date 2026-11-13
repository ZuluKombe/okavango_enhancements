"""Create OPR configuration records on initial application installation."""

from pathlib import Path

import frappe
from frappe.core.doctype.data_import.data_import import import_doc


def seed_opr_defaults():
    """Install missing OPR records without changing existing settings."""
    seed_dir = Path(__file__).resolve().parent

    records = [
        (
            "Workflow",
            "Okavango Operational Payment Approval",
            "workflow_seed.json",
        ),
        (
            "Client Script",
            "Okavango OPR Original Package Clone",
            "client_script_seed.json",
        ),
    ]

    for doctype, name, filename in records:
        if frappe.db.exists(doctype, name):
            continue

        import_doc(str(seed_dir / filename))
