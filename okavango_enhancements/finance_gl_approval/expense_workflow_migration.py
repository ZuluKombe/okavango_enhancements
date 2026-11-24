"""Controlled migration of Okavango Expense Entry approval workflow.

This module is staged only. Importing it makes no database changes.
Never run the apply function on production.
"""

import frappe
from frappe.utils import cint

from .controls import FEATURE_FLAG

SITE = "okavango-clone.localhost"
WORKFLOW = "Expense Approval"
RETURNED = "Returned for Correction"
ACTION = "Return for Correction"


def preview_clone():
    """Read-only preflight against the test clone."""
    if frappe.local.site != SITE:
        frappe.throw("Expense workflow migration is clone-only.")

    if cint(frappe.conf.get(FEATURE_FLAG, 0)):
        frappe.throw("Disable GL Approval before workflow migration.")

    wf = frappe.get_doc("Workflow", WORKFLOW)

    if wf.document_type != "Expense Entry" or not cint(wf.is_active):
        frappe.throw("Unexpected Expense Approval workflow.")

    if wf.workflow_state_field != "status":
        frappe.throw("Unexpected workflow state field.")

    meta = frappe.get_meta("Expense Entry")
    field = meta.get_field("status")

    if not field or field.fieldtype != "Select":
        frappe.throw("Expense Entry status field mismatch.")

    options = (field.options or "").split("\n")

    if options != ["Pending", "Approved", "Rejected"]:
        frappe.throw("Expense Entry status options differ from baseline.")

    states = {s.state: cint(s.doc_status) for s in wf.states}

    if states != {
        "Pending": 0,
        "Approved": 1,
        "Rejected": 1,
    }:
        frappe.throw("Expense workflow states differ from baseline.")

    approve = [
        t for t in wf.transitions
        if t.state == "Pending"
        and t.action == "Approve"
        and t.next_state == "Approved"
    ]

    reject = [
        t for t in wf.transitions
        if t.state == "Pending"
        and t.action == "Reject"
        and t.next_state == "Rejected"
    ]

    if len(wf.transitions) != 2 or len(approve) != 1 or len(reject) != 1:
        frappe.throw("Expense workflow transitions differ from baseline.")

    if (
        approve[0].allowed != "Accounts User"
        or cint(approve[0].allow_self_approval) != 1
        or reject[0].allowed != "Expense Approver"
    ):
        frappe.throw("Expense workflow role settings differ from baseline.")

    return {
        "workflow": WORKFLOW,
        "site": SITE,
        "status": "READY_FOR_REVIEW",
        "existing_rejected_state_preserved": True,
        "new_returned_state_docstatus": 0,
        "approve_role": "Expense Approver",
        "approve_self_approval": False,
        "changes_applied": False,
    }


def apply_clone(confirmation=None):
    """Apply only after a separately authorised clone test."""
    if confirmation != "AUTHORISE_TEST_CLONE_EXPENSE_MIGRATION":
        frappe.throw("Explicit test-clone authorisation required.")

    preview_clone()

    from frappe.custom.doctype.property_setter.property_setter import (
        make_property_setter,
    )

    wf = frappe.get_doc("Workflow", WORKFLOW)

    if not frappe.db.exists("Workflow Action Master", ACTION):
        frappe.get_doc({
            "doctype": "Workflow Action Master",
            "workflow_action_name": ACTION,
        }).insert(ignore_permissions=True)

    # Create a dedicated editable reason field for future returns only.
    # Historical Rejected records and their docstatus remain unchanged.
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    if not frappe.get_meta("Expense Entry").has_field("custom_gl_expense_return_reason"):
        create_custom_fields({
            "Expense Entry": [{
                "fieldname": "custom_gl_expense_return_reason",
                "label": "Reason for Returning Expense",
                "fieldtype": "Small Text",
                "insert_after": "status",
                "reqd": 0,
                "read_only": 0,
                "no_copy": 1,
                "print_hide": 1,
            }],
        }, update=False)

    # Extend the Select options without altering historical statuses.
    make_property_setter(
        "Expense Entry",
        "status",
        "options",
        "Pending\nApproved\nRejected\nReturned for Correction",
        "Text",
    )

    wf.append("states", {
        "state": RETURNED,
        "doc_status": "0",
        "allow_edit": "Accounts User",
    })

    for transition in wf.transitions:
        if transition.action == "Approve":
            transition.allowed = "Expense Approver"
            transition.allow_self_approval = 0

        elif transition.action == "Reject":
            transition.action = ACTION
            transition.next_state = RETURNED
            transition.allowed = "Expense Approver"
            transition.allow_self_approval = 0

    wf.append("transitions", {
        "state": RETURNED,
        "action": "Resubmit",
        "next_state": "Pending",
        "allowed": "Accounts User",
        "allow_self_approval": 1,
    })

    wf.save(ignore_permissions=True)
    frappe.clear_cache(doctype="Expense Entry")

    # No explicit commit. The caller owns transaction handling.
    return {
        "status": "CONFIGURATION_UPDATED",
        "historical_documents_modified": False,
        "workflow": WORKFLOW,
    }
