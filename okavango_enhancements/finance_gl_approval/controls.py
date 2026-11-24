"""Server-side finance approval gate. Disabled until the authorised site opts in.

A workflow is a user interface AND a state machine, not a security boundary by
itself. These hooks enforce an independent, named-user approval at submit time.
Do not make DB writes, commits, or bank releases from these hooks.
"""

from hashlib import sha256
from html import escape
import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, get_datetime

FEATURE_FLAG = "okavango_finance_gl_approval_enabled"
SITE_KEY = "okavango_finance_gl_approval_authorized_site"
USERS_KEY = "okavango_finance_gl_approver_users"
PREPARER_ROLE = "Okavango GL Preparer"
PAYMENT_DRAFT_ROLE = "Okavango Payment Entry Operator"
RECEIPT_ROLE = "Okavango Customer Receipt Poster"
APPROVER_ROLE = "Okavango GL Final Approver"
DRAFT = "Draft"
PENDING = "Pending Executive Approval"
RETURNED = "Returned for Correction"
APPROVED = "Approved"
RECEIPT = "Customer Receipt Posted"
CONTROLLED_DOCTYPES = ("Payment Entry", "Journal Entry")
AUDIT_FIELDS = (
    "custom_gl_prepared_by", "custom_gl_sent_at", "custom_gl_approved_by",
    "custom_gl_approved_at", "custom_gl_last_rejected_by",
    "custom_gl_last_rejected_at",
)
REASON_FIELD = "custom_gl_rejection_reason"
# Key exclusions must not include financial fields or child rows.
SNAPSHOT_EXCLUDE = {
    "name", "doctype", "owner", "creation", "modified", "modified_by",
    "docstatus", "workflow_state", "_user_tags", "_comments", "_assign",
    "_liked_by", "_seen", "idx", "parent", "parentfield", "parenttype",
    "status", "title", "in_words", "__islocal", "__unsaved",
}


def _enabled():
    return cint(frappe.conf.get(FEATURE_FLAG, 0)) == 1


def _require_site():
    if frappe.conf.get(SITE_KEY) != frappe.local.site:
        frappe.throw(_("GL approval configuration is not authorised for this site."), frappe.PermissionError)


def _approver_users():
    users = frappe.conf.get(USERS_KEY) or []
    if isinstance(users, str):
        try:
            users = json.loads(users)
        except (ValueError, TypeError):
            frappe.throw(_("Approver user configuration must be a JSON list."))
    if not isinstance(users, (list, tuple)) or len(set(users)) != 2:
        frappe.throw(_("Exactly two distinct COO/CEO user accounts must be configured."))
    return set(users)


def is_authorized_approver(user=None):
    """Exact named-user allowlist AND dedicated role; no Administrator exemption."""
    _require_site()
    user = user or frappe.session.user
    if user in (None, "Guest", "Administrator"):
        return False
    return user in _approver_users() and APPROVER_ROLE in frappe.get_roles(user)


EXPENSE_SOURCE_FIELD = "custom_gl_source_expense_entry"


def _verify_expense_source(doc, previous=None):
    """Validate an immutable, submitted Expense Entry source."""
    if doc.doctype != "Journal Entry":
        return None

    source_name = doc.get(EXPENSE_SOURCE_FIELD)

    if previous is not None:
        old_source = previous.get(EXPENSE_SOURCE_FIELD)
        if (source_name or "") != (old_source or ""):
            frappe.throw(
                _("Source Expense Entry cannot be changed or removed."),
                frappe.PermissionError
            )

    if not source_name:
        bill_no = doc.get("bill_no")
        if bill_no and frappe.db.exists("Expense Entry", bill_no):
            frappe.throw(
                _("Journal Entries linked to an Expense Entry must retain the source reference."),
                frappe.PermissionError
            )
        return None

    expense = frappe.db.get_value(
        "Expense Entry",
        source_name,
        ["owner", "status", "docstatus", "company"],
        as_dict=True
    )

    if (
        not expense
        or cint(expense.docstatus) != 1
        or expense.status != "Approved"
        or not expense.owner
    ):
        frappe.throw(
            _("The linked Expense Entry must exist and be submitted and approved."),
            frappe.PermissionError
        )

    if doc.get("company") != expense.company:
        frappe.throw(
            _("Journal Entry and Expense Entry companies must match."),
            frappe.PermissionError
        )

    if doc.get("bill_no") != source_name:
        frappe.throw(
            _("The Journal Entry bill reference must match its source Expense Entry."),
            frappe.PermissionError
        )

    return expense


def _require_approver(doc=None):
    user = frappe.session.user
    if not is_authorized_approver(user):
        frappe.throw(_("Only the named COO or CEO may approve this ledger posting."), frappe.PermissionError)
    if doc and user in (doc.owner, doc.get("custom_gl_prepared_by")):
        frappe.throw(_("You cannot approve a transaction you prepared or created."), frappe.PermissionError)

    if doc and doc.doctype == "Journal Entry":
        expense = _verify_expense_source(doc)
        if expense and user == expense.owner:
            frappe.throw(
                _("You cannot approve a Journal Entry originating from an Expense Entry you created."),
                frappe.PermissionError
            )


def _require_preparer():
    if frappe.session.user in (None, "Guest") or PREPARER_ROLE not in frappe.get_roles():
        frappe.throw(_("An authorised Accounting preparer is required."), frappe.PermissionError)


def _require_receipt_poster():
    """Separate customer-collection authority from outgoing GL preparation.

    Role membership does not itself grant Submit DocPerm. ERPNext's normal
    Payment Entry permissions and the workflow transition still apply.
    """
    actor = frappe.session.user
    if actor in (None, "Guest", "Administrator") or RECEIPT_ROLE not in frappe.get_roles(actor):
        frappe.throw(
            _("An authorised customer-receipt poster is required."),
            frappe.PermissionError,
        )


def _account_type(name, company, expected):
    if not name:
        return False
    account = frappe.db.get_value("Account", name, ["company", "account_type", "disabled", "is_group"], as_dict=True)
    return bool(account and account.company == company and account.account_type in expected
                and not account.disabled and not account.is_group)


def genuine_customer_receipt(doc):
    """A Receive flag alone must not exempt an alternative bank transfer."""
    return bool(
        doc.doctype == "Payment Entry"
        and doc.get("payment_type") == "Receive"
        and doc.get("party_type") == "Customer"
        and doc.get("party")
        and not (doc.get("deductions") or [])
        and float(doc.get("paid_amount") or 0) > 0
        and float(doc.get("received_amount") or 0) > 0
        and _account_type(doc.get("paid_from"), doc.get("company"), {"Receivable"})
        and _account_type(doc.get("paid_to"), doc.get("company"), {"Bank", "Cash"})
    )


def _clean(value):
    if isinstance(value, dict):
        return {
            key: _clean(val) for key, val in value.items()
            if key not in SNAPSHOT_EXCLUDE
            and not str(key).startswith("custom_gl_")
            and not str(key).startswith("_")
        }
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _fingerprint(doc):
    """Capture full financial parent/child payload, not merely the grand total."""
    data = doc.as_dict()
    return sha256(json.dumps(_clean(data), sort_keys=True, default=str,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _require_fields(doc):
    meta = frappe.get_meta(doc.doctype)
    needed = AUDIT_FIELDS + (REASON_FIELD, "workflow_state")
    if doc.doctype == "Journal Entry":
        needed += (EXPENSE_SOURCE_FIELD,)
    missing = [field for field in needed if not meta.has_field(field)]
    if missing:
        frappe.throw(_("GL workflow is not installed on {0}: {1}").format(
            doc.doctype, ", ".join(missing)))


def _require_support(doc):
    """Supporting file may be attached to this entry or its linked OPR."""
    if frappe.db.exists("File", {"attached_to_doctype": doc.doctype,
                                "attached_to_name": doc.name, "is_folder": 0}):
        return
    source_name = doc.get("custom_operational_payment_request") if doc.doctype == "Journal Entry" else None
    if source_name and frappe.db.exists("Operational Payment Request", source_name):
        if frappe.db.exists("File", {"attached_to_doctype": "Operational Payment Request",
                                    "attached_to_name": source_name, "is_folder": 0}):
            return
        if frappe.db.get_value("Operational Payment Request", source_name, "supporting_document"):
            return
    frappe.throw(_("Attach supporting evidence to the entry (or linked OPR) before approval."))


def validate(doc, method=None):
    """Validate state changes, freeze pending financial amounts, seal audit fields."""
    if not _enabled() or doc.doctype not in CONTROLLED_DOCTYPES:
        return
    _require_site()
    _require_fields(doc)
    previous = doc.get_doc_before_save()
    _verify_expense_source(doc, previous)
    state = doc.get("workflow_state") or DRAFT
    old_state = (previous.get("workflow_state") or DRAFT) if previous else None

    if not previous:
        if state != DRAFT:
            frappe.throw(_("New finance transactions must start in Draft."), frappe.PermissionError)
        for field in AUDIT_FIELDS:
            doc.set(field, None)
        doc.set(REASON_FIELD, None)
        return

    # Desk may round read-only Datetime fields to whole seconds.
    # Restore the exact server value; never persist client audit changes.
    for field in AUDIT_FIELDS:
        incoming = doc.get(field)
        stored = previous.get(field)

        if incoming == stored:
            continue

        field_meta = frappe.get_meta(doc.doctype).get_field(field)

        # Browser JSON uses strings; database values may be datetime objects.
        # Equivalent timestamps are not audit modifications.
        if (
            field_meta
            and field_meta.fieldtype == "Datetime"
            and incoming is not None
            and stored is not None
            and get_datetime(incoming) == get_datetime(stored)
        ):
            doc.set(field, stored)
            continue

        if (
            field_meta
            and field_meta.fieldtype == "Datetime"
            and incoming
            and stored
        ):
            incoming_dt = get_datetime(incoming)
            stored_dt = get_datetime(stored)

            if (
                incoming_dt.microsecond == 0
                and abs((incoming_dt - stored_dt).total_seconds()) < 1
            ):
                doc.set(field, stored)
                continue

        frappe.throw(
            _("Approval audit fields are controlled by the server."),
            frappe.PermissionError
        )

    # Financial changes after an approval request require returning for correction.
    if old_state == PENDING and _fingerprint(doc) != _fingerprint(previous):
        frappe.throw(_("Financial details changed during approval; return the draft for correction."))

    if state == old_state:
        if old_state == PENDING and doc.get(REASON_FIELD) != previous.get(REASON_FIELD):
            _require_approver(doc)
        return

    if old_state in (DRAFT, RETURNED) and state == PENDING:
        if cint(doc.docstatus) != 0:
            frappe.throw(_("Pending approval must remain a Draft document."))
        _require_preparer()
        doc.set("custom_gl_prepared_by", frappe.session.user)
        doc.set("custom_gl_sent_at", now_datetime())
        doc.set(REASON_FIELD, None)
        return

    if old_state == PENDING and state == RETURNED:
        _require_approver(doc)
        if cint(doc.docstatus) != 0:
            frappe.throw(_("Returned transactions must remain unsubmitted."))
        if len((doc.get(REASON_FIELD) or "").strip()) < 8:
            frappe.throw(_("Record a clear rejection reason of at least 8 characters before returning."))
        doc.set("custom_gl_last_rejected_by", frappe.session.user)
        doc.set("custom_gl_last_rejected_at", now_datetime())
        return

    if old_state == PENDING and state == APPROVED:
        _require_approver(doc)
        if cint(doc.docstatus) != 1:
            frappe.throw(_("Approval must submit and post the document in one transaction."))
        return

    if old_state == DRAFT and state == RECEIPT and doc.doctype == "Payment Entry":
        _require_receipt_poster()
        if not genuine_customer_receipt(doc) or cint(doc.docstatus) != 1:
            frappe.throw(_("Receipt-only posting requires a genuine incoming customer receipt."))
        return

    frappe.throw(_("Unauthorised financial workflow state transition: {0} -> {1}").format(
        old_state, state), frappe.PermissionError)


EXPENSE_RETURN_REASON_FIELD = "custom_gl_expense_return_reason"


def expense_validate_return(doc, method=None):
    """Enforce independent return decisions and a recorded reason."""
    if not _enabled() or doc.doctype != "Expense Entry":
        return
    _require_site()
    prior = doc.get_doc_before_save()
    if not prior:
        if doc.get("status") == RETURNED:
            frappe.throw(_("New Expense Entries cannot start Returned."), frappe.PermissionError)
        return

    previous_state = prior.get("status")
    current_state = doc.get("status")
    reason = (doc.get(EXPENSE_RETURN_REASON_FIELD) or "").strip()
    if previous_state == "Pending" and current_state == RETURNED:
        actor = frappe.session.user
        if (
            actor in (None, "Guest", "Administrator")
            or "Expense Approver" not in frappe.get_roles(actor)
            or not prior.owner or actor == prior.owner
            or doc.owner != prior.owner
            or cint(prior.docstatus) != 0
            or cint(doc.docstatus) != 0
        ):
            frappe.throw(_("Independent Expense Approver required for return."), frappe.PermissionError)
        if len(reason) < 8:
            frappe.throw(_("Enter a return reason of at least 8 characters before returning."))
        return

    if current_state == RETURNED and previous_state != RETURNED:
        frappe.throw(_("Invalid Expense Entry return transition."), frappe.PermissionError)

    if previous_state == RETURNED and current_state == RETURNED:
        if doc.get(EXPENSE_RETURN_REASON_FIELD) != prior.get(EXPENSE_RETURN_REASON_FIELD):
            frappe.throw(_("Saved return reasons cannot be silently changed."), frappe.PermissionError)

    if previous_state == RETURNED and current_state == "Pending":
        # Reason is preserved in a timestamped Comment at the moment of return.
        doc.set(EXPENSE_RETURN_REASON_FIELD, None)


def expense_on_update(doc, method=None):
    """Preserve rejection reason as an auditable timeline event."""
    if not _enabled() or doc.doctype != "Expense Entry":
        return
    _require_site()
    prior = doc.get_doc_before_save()
    if not prior or prior.get("status") == RETURNED or doc.get("status") != RETURNED:
        return
    reason = (doc.get(EXPENSE_RETURN_REASON_FIELD) or "").strip()
    frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Info",
        "reference_doctype": "Expense Entry",
        "reference_name": doc.name,
        "content": escape("Returned for correction | Actor: {0} | At: {1} | Reason: {2}".format(
            frappe.session.user, now_datetime(), reason
        )),
    }).insert(ignore_permissions=True)


def expense_before_submit(doc, method=None):
    """Require independent approval of Expense Entries."""
    if not _enabled():
        return

    _require_site()

    if doc.doctype != "Expense Entry":
        frappe.throw(
            _("Invalid expense approval document."),
            frappe.PermissionError
        )

    actor = frappe.session.user

    if (
        actor in (None, "Guest", "Administrator")
        or "Expense Approver" not in frappe.get_roles(actor)
    ):
        frappe.throw(
            _("An authorised Expense Approver is required."),
            frappe.PermissionError
        )

    saved = frappe.db.get_value(
        "Expense Entry",
        doc.name,
        ["owner", "status", "docstatus", "company"],
        as_dict=True
    )

    if (
        not saved
        or cint(saved.docstatus) != 0
        or saved.status != "Pending"
    ):
        frappe.throw(
            _("Only a saved, Pending Expense Entry may be approved."),
            frappe.PermissionError
        )

    if doc.get("status") != "Approved" or cint(doc.docstatus) != 1:
        frappe.throw(
            _("Expense Entry submission requires approval."),
            frappe.PermissionError
        )

    if (
        not saved.owner
        or doc.owner != saved.owner
        or doc.company != saved.company
    ):
        frappe.throw(
            _("Expense Entry ownership or company mismatch."),
            frappe.PermissionError
        )

    if actor == saved.owner:
        frappe.throw(
            _("You cannot approve an Expense Entry you created."),
            frappe.PermissionError
        )


def before_submit(doc, method=None):
    """Never trust a submitted workflow-state field without the prior draft."""
    if not _enabled() or doc.doctype not in CONTROLLED_DOCTYPES:
        return
    _require_site()
    _require_fields(doc)
    old = doc.get_doc_before_save()
    if not old or cint(old.docstatus) != 0:
        frappe.throw(_("A saved, unsubmitted transaction is required."), frappe.PermissionError)

    if doc.doctype == "Payment Entry" and genuine_customer_receipt(doc):
        if old.get("workflow_state") != DRAFT or doc.get("workflow_state") != RECEIPT:
            frappe.throw(_("Incoming receipts must use the Customer Receipt workflow action."), frappe.PermissionError)
        _require_receipt_poster()
        return

    if old.get("workflow_state") != PENDING or doc.get("workflow_state") != APPROVED:
        frappe.throw(_("A pending COO/CEO approval is mandatory before ledger submission."), frappe.PermissionError)
    _require_approver(doc)
    if _fingerprint(old) != _fingerprint(doc):
        frappe.throw(_("Posting values differ from the submitted approval request."))
    _require_support(doc)
    doc.set("custom_gl_approved_by", frappe.session.user)
    doc.set("custom_gl_approved_at", now_datetime())


def before_update_after_submit(doc, method=None):
    """Seal executive audit and source links on updates to posted documents.

    Other permitted post-submit fields remain governed by ERPNext/Frappe.
    """
    if not _enabled() or doc.doctype not in CONTROLLED_DOCTYPES:
        return
    _require_site()
    _require_fields(doc)
    old = doc.get_doc_before_save()
    if not old or cint(old.docstatus) != 1 or cint(doc.docstatus) != 1:
        frappe.throw(_("Posted transaction update requires submitted history."), frappe.PermissionError)
    if doc.owner != old.owner or doc.get("workflow_state") != old.get("workflow_state"):
        frappe.throw(_("Posted transaction owner and approval state are immutable."), frappe.PermissionError)
    for field in AUDIT_FIELDS + (REASON_FIELD,):
        if doc.get(field) != old.get(field):
            frappe.throw(_("Posted approval audit fields cannot be modified."), frappe.PermissionError)
    _verify_expense_source(doc, old)
    if doc.doctype == "Journal Entry" and (
        doc.get("custom_operational_payment_request")
        != old.get("custom_operational_payment_request")
    ):
        frappe.throw(_("Posted OPR source cannot be changed."), frappe.PermissionError)


def before_cancel(doc, method=None):
    if not _enabled() or doc.doctype not in CONTROLLED_DOCTYPES:
        return
    _require_site()
    if doc.doctype == "Payment Entry" and genuine_customer_receipt(doc):
        return  # Customer receipt cancellations follow existing delegated controls.
    _require_approver(doc)


def on_update(doc, method=None):
    """Append explicit timeline notes. Standard Version/Workflow Action add history."""
    if not _enabled() or doc.doctype not in CONTROLLED_DOCTYPES:
        return
    previous = doc.get_doc_before_save()
    if not previous or previous.get("workflow_state") == doc.get("workflow_state"):
        return
    state = doc.get("workflow_state")
    timestamp = now_datetime()
    actor = frappe.session.user
    details = {
        PENDING: "Sent for COO/CEO approval",
        RETURNED: "Returned for correction. Reason: " + str(doc.get(REASON_FIELD) or ""),
        APPROVED: "Approved and submitted to General Ledger",
        RECEIPT: "Incoming customer receipt submitted",
    }.get(state, "Workflow changed")
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info", "reference_doctype": doc.doctype,
        "reference_name": doc.name,
        "content": escape(f"{details} | Actor: {actor} | Timestamp: {timestamp}"),
    }).insert(ignore_permissions=True)
