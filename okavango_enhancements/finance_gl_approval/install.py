"""Deliberate CLONE-ONLY staging; no installation/migration auto-enable.

Run with `bench --site okavango-clone.localhost execute
okavango_enhancements.finance_gl_approval.install.prepare_clone`.
Review workflow conflicts and role permissions before activating anything.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from .controls import (APPROVER_ROLE, PREPARER_ROLE, APPROVED, DRAFT,
                       PENDING, RECEIPT, RETURNED, USERS_KEY, SITE_KEY,
                       FEATURE_FLAG, AUDIT_FIELDS, REASON_FIELD,
                       PAYMENT_DRAFT_ROLE, RECEIPT_ROLE)

CLONE = "okavango-clone.localhost"
WF_PAYMENT = "Okavango GL Payment Entry Executive Approval"
WF_JOURNAL = "Okavango GL Journal Entry Executive Approval"


def _required_clone():
    if frappe.local.site != CLONE:
        frappe.throw("Installer is restricted to the named TEST CLONE. No production auto-install.")


def _ensure_role(name):
    if not frappe.db.exists("Role", name):
        frappe.get_doc({"doctype": "Role", "role_name": name, "desk_access": 1}).insert(ignore_permissions=True)


def _ensure_state(name):
    if not frappe.db.exists("Workflow State", name):
        frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": name,
                        "style": "Success" if name in (APPROVED, RECEIPT) else "Warning"}).insert(ignore_permissions=True)


def _ensure_action(name):
    if not frappe.db.exists("Workflow Action Master", name):
        frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": name}).insert(ignore_permissions=True)


def _states(is_payment):
    states = [
        {"state": DRAFT, "doc_status": "0",
         "allow_edit": PAYMENT_DRAFT_ROLE if is_payment else PREPARER_ROLE},
        {"state": PENDING, "doc_status": "0", "allow_edit": APPROVER_ROLE},
        {"state": RETURNED, "doc_status": "0", "allow_edit": PREPARER_ROLE},
        {"state": APPROVED, "doc_status": "1", "allow_edit": APPROVER_ROLE},
    ]
    if is_payment:
        states.append({"state": RECEIPT, "doc_status": "1", "allow_edit": RECEIPT_ROLE})
    return states


def _transitions(is_payment):
    transitions = [
        {"state": DRAFT, "action": "Send for Executive Approval", "next_state": PENDING,
         "allowed": PREPARER_ROLE, "allow_self_approval": 1},
        {"state": RETURNED, "action": "Resend for Executive Approval", "next_state": PENDING,
         "allowed": PREPARER_ROLE, "allow_self_approval": 1},
        {"state": PENDING, "action": "Approve and Post", "next_state": APPROVED,
         "allowed": APPROVER_ROLE, "allow_self_approval": 0},
        {"state": PENDING, "action": "Return for Correction", "next_state": RETURNED,
         "allowed": APPROVER_ROLE, "allow_self_approval": 0},
    ]
    if is_payment:
        transitions.append({
            "state": DRAFT, "action": "Post Customer Receipt", "next_state": RECEIPT,
            "allowed": RECEIPT_ROLE, "allow_self_approval": 1,
            "condition": "doc.payment_type == 'Receive' and doc.party_type == 'Customer' and not doc.deductions",
        })
    return transitions


def _ensure_workflow(doctype, name, is_payment):
    if frappe.db.exists("Workflow", name):
        return "already_exists_unchanged"
    frappe.get_doc({
        "doctype": "Workflow", "workflow_name": name,
        "document_type": doctype, "workflow_state_field": "workflow_state",
        "is_active": 0, "override_status": 0, "send_email_alert": 1,
        "states": _states(is_payment), "transitions": _transitions(is_payment),
    }).insert(ignore_permissions=True)
    return "created_INACTIVE"


def prepare_clone():
    """Idempotently install support fields, roles and INACTIVE workflow skeletons."""
    _required_clone()
    for role in (PREPARER_ROLE, APPROVER_ROLE, PAYMENT_DRAFT_ROLE, RECEIPT_ROLE):
        _ensure_role(role)
    for name in (DRAFT, PENDING, RETURNED, APPROVED, RECEIPT):
        _ensure_state(name)
    for name in ("Send for Executive Approval", "Resend for Executive Approval",
                 "Approve and Post", "Return for Correction", "Post Customer Receipt"):
        _ensure_action(name)
    field_config = [
        ("custom_gl_prepared_by", "Prepared / Sent By", "Link", "User", 1),
        ("custom_gl_sent_at", "Sent for Approval At", "Datetime", None, 1),
        ("custom_gl_approved_by", "GL Approved By", "Link", "User", 1),
        ("custom_gl_approved_at", "GL Approved At", "Datetime", None, 1),
        ("custom_gl_last_rejected_by", "Last Returned By", "Link", "User", 1),
        ("custom_gl_last_rejected_at", "Last Returned At", "Datetime", None, 1),
        ("custom_gl_rejection_reason", "Reason for Returning", "Small Text", None, 0),
    ]
    mapping = {}
    for doctype in ("Payment Entry", "Journal Entry"):
        fields = [{"fieldname": field, "label": label, "fieldtype": typ,
                   "options": opts, "insert_after": "posting_date", "read_only": readonly,
                   "no_copy": 1, "print_hide": 1}
                  for field, label, typ, opts, readonly in field_config]
        if doctype == "Journal Entry":
            fields.append({
                "fieldname": "custom_gl_source_expense_entry",
                "label": "Source Expense Entry",
                "fieldtype": "Link",
                "options": "Expense Entry",
                "insert_after": "bill_no",
                "read_only": 1,
                "no_copy": 1,
                "print_hide": 1,
            })
        # Frappe's workflow setup may create workflow_state itself. Only create
        # it if absent, and never replace an existing workflow-state field.
        if not frappe.get_meta(doctype).has_field("workflow_state"):
            fields.append({"fieldname": "workflow_state", "label": "Workflow State",
                           "fieldtype": "Link", "options": "Workflow State",
                           "insert_after": "posting_date", "read_only": 1,
                           "no_copy": 1})
        mapping[doctype] = fields
    create_custom_fields(mapping, update=False)
    statuses = {
        "Payment Entry": _ensure_workflow("Payment Entry", WF_PAYMENT, True),
        "Journal Entry": _ensure_workflow("Journal Entry", WF_JOURNAL, False),
    }
    # NEVER assign users, set site-config flags, grant Submit, or activate a
    # workflow automatically. The operator must inspect conflicts first.
    frappe.db.commit()
    return {"site": frappe.local.site, "workflows": statuses,
            "both_workflows_active": False,
            "must_assign_roles_review_permissions_and_test": True}


def readiness_report():
    """Read-only deployment evidence, safe in clone and live."""
    info = {"site": frappe.local.site,
            "flag_value": frappe.conf.get(FEATURE_FLAG, 0),
            "authorized_site": frappe.conf.get(SITE_KEY),
            "configured_approver_accounts": frappe.conf.get(USERS_KEY),
            "doctypes": {}}
    for doctype in ("Payment Entry", "Journal Entry", "Purchase Invoice", "Expense Claim"):
        if not frappe.db.exists("DocType", doctype):
            info["doctypes"][doctype] = {"exists": False}
            continue
        meta = frappe.get_meta(doctype)
        info["doctypes"][doctype] = {
            "exists": True,
            "audit_fields_present": all(meta.has_field(f) for f in AUDIT_FIELDS + (REASON_FIELD,)),
            "active_workflows": frappe.get_all("Workflow", filters={
                "document_type": doctype, "is_active": 1}, pluck="name"),
        }
    return info
