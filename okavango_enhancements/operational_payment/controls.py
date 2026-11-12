"""Kombe original Operational Payment Request fields on the existing clone app.

Adapted from the USER-SUPPLIED controls.py for the already-installed roles,
workflow, and JE hooks in okavango_enhancements.  The feature flag is OFF by
installer default.  Only linked OPR Journal Entries are affected. No core edits.
"""

from decimal import Decimal, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.utils import flt, nowdate

REQUEST_DTYPE = 'Operational Payment Request'
JOURNAL_DTYPE = 'Journal Entry'
JE_LINK_FIELD = 'custom_operational_payment_request'
REQUEST_JE_FIELD = 'journal_entry'
LEGACY_JE_LINK_FIELD = 'custom_expense_journal_entry'
FINANCE_ROLE = 'Operational Payment Finance'  # already assigned on clone
APPROVER_ROLE = 'Operational Payment Approver'
APPROVED_STATE = 'Approved'                 # already active on clone
PAID_STATE = 'Paid'
FEATURE_FLAG = 'okavango_opr_expense_enabled'
IMMUTABLE_AFTER_APPROVAL = (
    'request_date', 'company', 'department', 'category', 'requested_amount',
    'currency', 'required_by', 'payee_name', 'payment_method',
    'recipient_details', 'purpose', 'route_or_shift', 'sales_order',
    'order_reference', 'customer_delivery_charged', 'supporting_document',
    'cost_center',
)


def _enabled():
    return str(frappe.conf.get(FEATURE_FLAG, '0')).lower() in ('true', '1', 'yes')


def _finance_user():
    return frappe.session.user == 'Administrator' or FINANCE_ROLE in frappe.get_roles()


def _approver_user():
    return frappe.session.user == 'Administrator' or APPROVER_ROLE in frappe.get_roles()


def _require_finance():
    if not _finance_user():
        frappe.throw(_('Only authorised Operational Payment Finance users may process this expense.'),
                     frappe.PermissionError)


def _get_source(name):
    source = frappe.get_doc(REQUEST_DTYPE, name)
    source.check_permission('read')
    return source


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _order_ref(source):
    return source.get('sales_order') or source.get('order_reference') or ''


def _require_source_ready(source):
    if int(source.docstatus) != 1 or source.workflow_state != APPROVED_STATE:
        frappe.throw(_('The Operational Payment Request must be Approved and Submitted.'))
    if _money(source.requested_amount) <= 0:
        frappe.throw(_('Approved amount must be greater than zero.'))
    if not source.company:
        frappe.throw(_('A Company is required.'))
    base = frappe.get_cached_value('Company', source.company, 'default_currency')
    if source.currency != 'ZMW' or base != 'ZMW':
        frappe.throw(_('This direct-expense process supports ZMW only.'))
    if source.category == 'Customer Delivery' and not _order_ref(source):
        frappe.throw(_('Customer Delivery requires Sales Order or Other Order Reference.'))
    return base


def _active_journal(source_name, ignore_name=None):
    # Called after row locking in validate_journal_entry to prevent races.
    rows = frappe.db.sql(
        '''SELECT name, docstatus FROM `tabJournal Entry`
           WHERE custom_operational_payment_request = %s AND docstatus < 2
             AND name != %s ORDER BY creation LIMIT 1''',
        (source_name, ignore_name or ''), as_dict=True,
    )
    return rows[0] if rows else None


def _lock_source(source_name):
    rows = frappe.db.sql(
        'SELECT name FROM `tabOperational Payment Request` WHERE name = %s FOR UPDATE',
        (source_name,), as_dict=True,
    )
    if not rows:
        frappe.throw(_('Operational Payment Request not found.'))


def _source_note(source):
    pieces = [
        'OPR: ' + source.name,
        'Category: ' + str(source.get('category') or ''),
        'Purpose: ' + str(source.get('purpose') or ''),
        'Payee: ' + str(source.get('payee_name') or ''),
        'Requested mode: ' + str(source.get('payment_method') or ''),
        'Order: ' + str(_order_ref(source)),
        'Route/shift: ' + str(source.get('route_or_shift') or ''),
    ]
    return ' | '.join(pieces)[:1000]


@frappe.whitelist()
def get_button_state(source_name):
    if not _enabled() or not _finance_user():
        return {'allowed': False}
    try:
        source = _get_source(source_name)
        _require_source_ready(source)
        if not frappe.has_permission(JOURNAL_DTYPE, 'create'):
            return {'allowed': False}
        other = _active_journal(source_name)
        return {'allowed': not bool(other), 'existing_name': other.name if other else None}
    except Exception:
        return {'allowed': False}


@frappe.whitelist()
def get_journal_entry_prefill(source_name):
    """Return UNSAVED prefill; finance chooses two ledger accounts before saving."""
    if not _enabled():
        frappe.throw(_('Operational expense creation remains disabled on this site.'))
    _require_finance()
    source = _get_source(source_name)
    _require_source_ready(source)
    if not frappe.has_permission(JOURNAL_DTYPE, 'create'):
        frappe.throw(_('Journal Entry create permission is required.'), frappe.PermissionError)
    duplicate = _active_journal(source.name)
    if duplicate:
        frappe.throw(_('Active Journal Entry already exists: {0}').format(duplicate.name))
    amount = flt(source.requested_amount, 2)
    return {
        'header': {
            'company': source.company,
            'voucher_type': 'Journal Entry',
            'posting_date': nowdate(),
            JE_LINK_FIELD: source.name,
            'custom_customer_order_reference': _order_ref(source),
            'user_remark': _source_note(source),
        },
        'accounts': [
            {'debit_in_account_currency': amount, 'credit_in_account_currency': 0,
             'debit': amount, 'credit': 0, 'cost_center': source.get('cost_center') or None},
            {'debit_in_account_currency': 0, 'credit_in_account_currency': amount,
             'debit': 0, 'credit': amount},
        ],
        'amount': amount,
        'company_currency': 'ZMW',
    }


def _check_account(account_name, company, *, expense):
    if not account_name:
        frappe.throw(_('Finance must select both an Expense and a Bank/Cash ledger.'))
    a = frappe.db.get_value('Account', account_name,
        ['company', 'is_group', 'disabled', 'root_type', 'account_type', 'account_currency'],
        as_dict=True)
    if not a or a.company != company or a.is_group or a.disabled:
        frappe.throw(_('Account {0} is invalid, disabled, grouped, or not in the selected company.').format(account_name))
    if expense:
        if a.root_type != 'Expense':
            frappe.throw(_('The debit account must be an Expense ledger.'))
    elif a.root_type != 'Asset' or a.account_type not in ('Cash', 'Bank'):
        frappe.throw(_('The credit account must be an Asset ledger classified Cash or Bank.'))
    if a.account_currency and a.account_currency != 'ZMW':
        frappe.throw(_('Both ledgers must be ZMW-denominated.'))


def validate_journal_entry(doc, method=None):
    """Server-side enforcement for linked JEs, including direct API saves."""
    source_name = doc.get(JE_LINK_FIELD)
    previous = doc.get_doc_before_save()
    previous_source = previous.get(JE_LINK_FIELD) if previous else None
    if previous_source and previous_source != source_name:
        frappe.throw(_('Cannot remove or change a saved Journal Entry source link.'))
    if not source_name:
        return  # Supplier/unrelated JEs remain unaffected.
    if not _enabled() and not previous_source:
        frappe.throw(_('Operational expense creation is disabled on this site.'))
    _require_finance()
    permission = 'create' if doc.is_new() else ('submit' if int(doc.docstatus) == 1 else 'write')
    if not frappe.has_permission(JOURNAL_DTYPE, permission, doc=doc):
        frappe.throw(_('Insufficient Journal Entry permissions.'), frappe.PermissionError)
    _lock_source(source_name)
    source = _get_source(source_name)
    _require_source_ready(source)
    other = _active_journal(source_name, ignore_name=doc.name)
    if other:
        frappe.throw(_('Duplicate active Journal Entry blocked: {0}').format(other.name))
    if doc.company != source.company or doc.voucher_type != 'Journal Entry':
        frappe.throw(_('Company or Journal Entry type differs from approved request.'))
    if doc.get('multi_currency'):
        frappe.throw(_('Multi-currency is excluded from this operational-expense process.'))
    if (doc.get('custom_customer_order_reference') or '') != _order_ref(source):
        frappe.throw(_('Order reference must match the request.'))
    if _source_note(source) not in (doc.get('user_remark') or ''):
        frappe.throw(_('The source purpose, payee, category and order reference must remain in the remarks.'))
    rows = doc.get('accounts') or []
    if len(rows) != 2:
        frappe.throw(_('Exactly two accounting rows are required.'))
    debit, credit = rows

    # The expense must be charged to the approved OPR cost center.
    expected_cost_center = source.get('cost_center')
    if expected_cost_center and debit.get('cost_center') != expected_cost_center:
        frappe.throw(
            _('Expense cost center must match the approved payment request ({0}).')
            .format(expected_cost_center)
        )

    amount = _money(source.requested_amount)
    if (_money(debit.debit) != amount or _money(debit.credit) != 0 or
        _money(credit.debit) != 0 or _money(credit.credit) != amount or
        _money(debit.debit_in_account_currency) != amount or
        _money(credit.credit_in_account_currency) != amount or
        _money(debit.credit_in_account_currency) != 0 or
        _money(credit.debit_in_account_currency) != 0):
        frappe.throw(_('Journal Entry debit and credit must each equal the approved request amount.'))
    for row in rows:
        if row.party or row.party_type or row.reference_type or row.reference_name:
            frappe.throw(_('No supplier/party or invoice allocations are allowed in this direct expense.'))
    _check_account(debit.account, source.company, expense=True)
    _check_account(credit.account, source.company, expense=False)


def _sync_request(doc, *, status, clear=False):
    source_name = doc.get(JE_LINK_FIELD)
    if not source_name:
        return
    current = frappe.db.get_value(REQUEST_DTYPE, source_name, REQUEST_JE_FIELD)
    if clear:
        if current != doc.name:
            return
        value = None
    else:
        if current and current != doc.name:
            prior_status = frappe.db.get_value(JOURNAL_DTYPE, current, 'docstatus')
            if prior_status is not None and int(prior_status) != 2:
                frappe.throw(_('The request is already linked to active Journal Entry {0}.').format(current))
        value = doc.name
    frappe.db.set_value(REQUEST_DTYPE, source_name, {
        REQUEST_JE_FIELD: value,
        LEGACY_JE_LINK_FIELD: value,  # maintain clone legacy reverse-link without exposing the field
        'accounting_status': status,
    }, update_modified=False)


def after_insert_journal_entry(doc, method=None):
    if doc.get(JE_LINK_FIELD):
        _sync_request(doc, status='Draft JE')


def on_update_journal_entry(doc, method=None):
    if doc.get(JE_LINK_FIELD) and int(doc.docstatus) != 2:
        _sync_request(doc, status='Posted' if int(doc.docstatus) == 1 else 'Draft JE')


def before_cancel_journal_entry(doc, method=None):
    if not doc.get(JE_LINK_FIELD):
        return
    _lock_source(doc.get(JE_LINK_FIELD))
    request = frappe.get_doc(REQUEST_DTYPE, doc.get(JE_LINK_FIELD))
    if request.workflow_state == PAID_STATE:
        frappe.throw(_('Reopen the Paid request through the authorised workflow before cancelling.'))


def on_cancel_journal_entry(doc, method=None):
    _sync_request(doc, status='Cancelled', clear=True)


def on_trash_journal_entry(doc, method=None):
    if doc.get(JE_LINK_FIELD):
        _sync_request(doc, status='Not Created', clear=True)


def _assert_paid_evidence(doc):
    j = frappe.db.get_value(JOURNAL_DTYPE,
        {JE_LINK_FIELD: doc.name, 'docstatus': 1},
        ['name', 'company', 'total_debit', 'total_credit'], as_dict=True)
    if (not j or j.company != doc.company or
        _money(j.total_debit) != _money(doc.requested_amount) or
        _money(j.total_credit) != _money(doc.requested_amount)):
        frappe.throw(_('A matching, submitted Journal Entry is required before Paid.'))
    if _money(doc.get('paid_amount')) != _money(doc.requested_amount):
        frappe.throw(_('Full payment must match the approved amount.'))
    if not doc.get('paid_on') or not doc.get('payment_proof'):
        frappe.throw(_('Payment date and payment proof are mandatory before Paid.'))
    if not frappe.db.exists('File', {
        'file_url': doc.payment_proof,
        'attached_to_doctype': REQUEST_DTYPE,
        'attached_to_name': doc.name,
    }):
        frappe.throw(_('Payment proof must be attached to this request.'))
    if doc.get(REQUEST_JE_FIELD) != j.name:
        frappe.throw(_('Journal Entry link does not match the submitted entry.'))


def validate_operational_payment_request(doc, method=None):
    previous = doc.get_doc_before_save()
    previous_state = previous.workflow_state if previous else None
    state = doc.get('workflow_state')
    if state in ('Pending Approval', APPROVED_STATE, PAID_STATE):
        if _money(doc.get('requested_amount')) <= 0:
            frappe.throw(_('Amount Requested must be positive.'))
        if doc.get('currency') != 'ZMW':
            frappe.throw(_('Only ZMW direct-expense requests are supported.'))
        if doc.get('category') == 'Customer Delivery' and not _order_ref(doc):
            frappe.throw(_('Customer Delivery requires Sales Order or Other Order Reference.'))
    if state == APPROVED_STATE and previous_state != APPROVED_STATE:
        if previous_state == PAID_STATE:
            if not _finance_user():
                frappe.throw(_('Only Finance can reopen a Paid Operational Payment Request.'), frappe.PermissionError)
        elif not _approver_user():
            frappe.throw(_('Only Operations can approve an Operational Payment Request.'), frappe.PermissionError)
    if previous and previous_state in (APPROVED_STATE, PAID_STATE):
        for field in IMMUTABLE_AFTER_APPROVAL:
            if field == 'requested_amount':
                changed = Decimal(str(previous.get(field) or 0)) != Decimal(str(doc.get(field) or 0))
            else:
                changed = str(previous.get(field) or '') != str(doc.get(field) or '')
            if changed:
                frappe.throw(_('Approved request field {0} cannot be changed.').format(field))
        if (previous.get('journal_entry') or '') != (doc.get('journal_entry') or '') or \
           (previous.get('accounting_status') or '') != (doc.get('accounting_status') or ''):
            frappe.throw(_('Accounting link and status are controlled by the server, not editable on the request.'))
    if state == PAID_STATE:
        if not doc.is_new():
            _lock_source(doc.name)
        if previous_state != PAID_STATE:
            _require_finance()
        _assert_paid_evidence(doc)


def before_update_after_submit_operational_request(doc, method=None):
    validate_operational_payment_request(doc, method)
