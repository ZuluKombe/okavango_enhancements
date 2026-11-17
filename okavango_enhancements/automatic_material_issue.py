"""Purpose-aware, site-authorized automatic stock posting on final COO approval.

This module deliberately has NO DB commit in the submit hook: approval and stock
posting are one transaction.  It is disabled by default on other sites until explicitly authorized.
"""

import json
from collections import defaultdict

import frappe
from frappe.utils import cint, flt

CLONE_SITE = "okavango-clone.localhost"
SITE_ALLOW_KEY = "okavango_auto_mr_issue_authorized_site"
FLAG = "okavango_auto_mr_issue_enabled"
LINK_FIELD = "custom_okavango_auto_mr"
RULES_KEY = "okavango_auto_mr_purpose_rules"
REVERSAL_FLAG = "okavango_auto_mr_issue_allow_reversal"
BARTER_FLAG = "okavango_auto_mr_barter_accounting_approved"
WORKFLOW = "Material Request - COO Approval"

# 'manual' means no automatic Stock Entry. It does not mean the purpose is ignored.
# Purchase/manufacture/customer-provided stock is not issued on MR approval.
DEFAULT_RULES = {
    "Staff Use": "Material Issue",
    "Giveaway": "Material Issue",
    "Gifts": "Material Issue",
    "Content Creation": "Material Issue",
    "Barter": "manual",
    "Material Issue": "Material Issue",
    "Material Transfer": "Material Transfer",
    "Purchase": "manual",
    "Manufacture": "manual",
    "Customer Provided": "manual",
}
ALLOWED_RULES = {
    purpose: ({"manual", mode} if mode != "manual" else {"manual"})
    for purpose, mode in DEFAULT_RULES.items()
}
# Barter may be explicitly opted in only with a separate company accounting signoff.
ALLOWED_RULES["Barter"] = {"manual", "Material Issue"}


def _site_authorized():
    return (
        frappe.local.site == CLONE_SITE
        or frappe.conf.get(SITE_ALLOW_KEY) == frappe.local.site
    )


def _require_authorized_site():
    if not _site_authorized():
        frappe.throw("Automatic Material Request posting is not authorized on this site.")


def _enabled():
    return _site_authorized() and cint(frappe.conf.get(FLAG, 0)) == 1


def purpose_rules():
    """Merge site-config overrides, rejecting unknown and unsafe combinations."""
    _require_authorized_site()
    configured = frappe.conf.get(RULES_KEY, {}) or {}
    if isinstance(configured, str):
        try:
            configured = json.loads(configured)
        except ValueError:
            frappe.throw(f"{RULES_KEY} must contain a JSON object.")
    if not isinstance(configured, dict):
        frappe.throw(f"{RULES_KEY} must be a JSON object mapping purposes to actions.")
    rules = dict(DEFAULT_RULES)
    for purpose, mode in configured.items():
        if purpose not in ALLOWED_RULES or mode not in ALLOWED_RULES[purpose]:
            frappe.throw(f"Unsafe automatic stock rule: {purpose!r} -> {mode!r}.")
        rules[purpose] = mode
    if rules["Barter"] == "Material Issue" and not cint(frappe.conf.get(BARTER_FLAG, 0)):
        frappe.throw("Automatic Barter issuing requires explicit accounting approval flag.")
    return rules


def install_support_field():
    """Idempotent Stock Entry provenance Link. Does not enable posting."""
    _require_authorized_site()
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields({"Stock Entry": [{
        "fieldname": LINK_FIELD,
        "label": "Auto Stock Movement From Material Request",
        "fieldtype": "Link",
        "options": "Material Request",
        "insert_after": "remarks",
        "read_only": 1,
        "no_copy": 1,
        "unique": 1,
        "description": "Unique audit reference for automatic issue or transfer.",
    }]}, update=False)
    frappe.db.commit()
    frappe.clear_cache(doctype="Stock Entry")
    if not frappe.get_meta("Stock Entry").has_field(LINK_FIELD):
        frappe.throw("Missing Stock Entry provenance field; keep the feature disabled.")
    return {"site": frappe.local.site, "installed": True, "field": LINK_FIELD}


def readiness_report():
    """Read only. Use before enabling on an authorized site."""
    _require_authorized_site()
    workflow = frappe.db.get_value("Workflow", WORKFLOW, ["is_active", "document_type"], as_dict=True)
    rules = purpose_rules()
    return {
        "site": frappe.local.site,
        "enabled": _enabled(),
        "workflow_active": bool(workflow and cint(workflow.is_active) and workflow.document_type == "Material Request"),
        "origin_field_exists": bool(frappe.get_meta("Stock Entry").has_field(LINK_FIELD)),
        "material_issue_type_exists": bool(frappe.db.exists("Stock Entry Type", "Material Issue")),
        "material_transfer_type_exists": bool(frappe.db.exists("Stock Entry Type", "Material Transfer")),
        "rules": rules,
        "reversal_enabled": bool(cint(frappe.conf.get(REVERSAL_FLAG, 0))),
        "previously_approved_requests_not_backfilled": True,
        "batch_serial_items_blocked": True,
    }


def _mode_for(mr):
    return purpose_rules().get(mr.material_request_type, "manual")


def _source_target(mr, row, mode):
    """MR warehouse is the SOURCE for Issue, TARGET for Transfer."""
    if mode == "Material Issue":
        return row.get("warehouse"), None
    if mode == "Material Transfer":
        # Explicit row and document sources win; otherwise use the Item Default
        # for this same company. Never assume the target is the source.
        source = row.get("from_warehouse") or mr.get("set_from_warehouse")
        if not source:
            source = frappe.db.get_value(
                "Item Default",
                {"parent": row.item_code, "company": mr.company},
                "default_warehouse",
            )
        # Item.warehouse is the TARGET for a Material Transfer. It may be
        # auto-populated by ERPNext, so source == target MUST remain blocked.
        target = row.get("warehouse") or mr.get("set_warehouse")
        return source, target
    frappe.throw(f"Unsupported automatic stock action: {mode}")


def preview_request(name):
    """Read-only preview, no Bin locking or ledger writes."""
    _require_authorized_site()
    mr = frappe.get_doc("Material Request", name)
    mode = _mode_for(mr)
    rows = []
    for row in mr.items:
        source, target = _source_target(mr, row, mode) if mode != "manual" else (None, None)
        stock = frappe.db.get_value("Bin", {"item_code": row.item_code, "warehouse": source},
                                    ["actual_qty", "reserved_qty"], as_dict=True) if source else None
        actual = flt(stock.actual_qty) if stock else 0.0
        reserved = flt(stock.reserved_qty) if stock else 0.0
        rows.append({
            "item": row.item_code, "source": source, "target": target,
            "qty_stock_uom": flt(row.stock_qty), "actual": actual,
            "reserved": reserved, "unreserved": actual - reserved,
            "enough_unreserved": bool(source and actual - reserved >= flt(row.stock_qty)),
            "different_transfer_warehouses": (
                bool(source and target and source != target)
                if mode == "Material Transfer" else None
            ),
        })
    return {"request": mr.name, "state": mr.workflow_state, "purpose": mr.material_request_type,
            "automatic_action": mode, "rows": rows, "does_not_post": True}


def _validate_workflow_and_origin(mr, mode):
    if mr.docstatus != 1 or mr.workflow_state != "Approved":
        frappe.throw("A submitted, COO-approved Material Request is required.")
    previous = mr.get_doc_before_save()
    if not previous or previous.get("workflow_state") != "Pending COO Approval":
        frappe.throw("Automatic posting only runs during Pending COO Approval -> Approved.")
    if frappe.session.user in ("Guest", None) or "COO Approver" not in frappe.get_roles(frappe.session.user):
        frappe.throw("Automatic stock posting requires an authenticated COO Approver.")
    if not frappe.get_meta("Stock Entry").has_field(LINK_FIELD):
        frappe.throw("Missing Stock Entry origin field. Turn off automatic posting.")
    if not frappe.db.exists("Stock Entry Type", mode):
        frappe.throw(f"Stock Entry Type {mode} does not exist.")
    # Serializes concurrent automation requests for the same MR. The unique Link
    # also makes an origin collision impossible if this check is bypassed.
    frappe.db.sql("SELECT name FROM `tabMaterial Request` WHERE name=%s FOR UPDATE", (mr.name,))
    if frappe.db.get_value("Stock Entry", {LINK_FIELD: mr.name}, "name"):
        frappe.throw(f"A Stock Entry already originates from {mr.name}.")
    other = frappe.db.sql(
        """SELECT se.name FROM `tabStock Entry Detail` d JOIN `tabStock Entry` se
           ON se.name=d.parent WHERE d.material_request=%s AND se.docstatus IN (0,1) LIMIT 1""",
        (mr.name,),
    )
    if other:
        frappe.throw(f"Stock Entry {other[0][0]} already references {mr.name}.")


def _validate_warehouse(name, company, row_idx, kind):
    warehouse = frappe.db.get_value("Warehouse", name, ["company", "is_group", "disabled"], as_dict=True) if name else None
    if not warehouse or warehouse.company != company or cint(warehouse.is_group) or cint(warehouse.disabled):
        frappe.throw(f"Row {row_idx}: invalid {kind} warehouse {name!r} for {company}.")


def _validate_accounting(row, company, mode):
    for field, doctype in (("expense_account", "Account"), ("cost_center", "Cost Center")):
        value = row.get(field)
        if not value and mode == "Material Issue":
            frappe.throw(f"Row {row.idx}: {field} is required for a Material Issue.")
        if not value:
            continue
        info = frappe.db.get_value(doctype, value, ["company", "is_group", "disabled"], as_dict=True)
        if not info or info.company != company or cint(info.is_group) or cint(info.disabled):
            frappe.throw(f"Row {row.idx}: invalid {field} {value!r} for {company}.")
        if field == "expense_account" and mode == "Material Issue":
            acct_type = frappe.db.get_value("Account", value, "account_type")
            if acct_type == "Stock":
                frappe.throw(f"Row {row.idx}: Stock account cannot be used as a Material Issue expense.")


def _build_rows_and_lock_stock(mr, mode):
    if not mr.items:
        frappe.throw("Material Request contains no items.")
    quantities = defaultdict(float)
    expected = defaultdict(float)
    rows = []
    for row in mr.items:
        source, target = _source_target(mr, row, mode)
        if not row.item_code or not source:
            frappe.throw(f"Row {row.idx}: item and source warehouse are mandatory.")
        if mode == "Material Transfer" and (not target or target == source):
            frappe.throw(f"Row {row.idx}: transfer needs a different target warehouse.")
        qty = flt(row.stock_qty)
        if qty <= 0:
            frappe.throw(f"Row {row.idx}: stock quantity must be positive.")
        item = frappe.db.get_value("Item", row.item_code,
            ["is_stock_item", "has_serial_no", "has_batch_no", "stock_uom", "disabled"], as_dict=True)
        if not item or not cint(item.is_stock_item) or cint(item.disabled):
            frappe.throw(f"Row {row.idx}: inactive or non-stock item {row.item_code}.")
        if cint(item.has_serial_no) or cint(item.has_batch_no):
            frappe.throw(f"Row {row.idx}: {row.item_code} is serial/batch controlled; automatic posting is blocked.")
        if row.stock_uom != item.stock_uom:
            frappe.throw(f"Row {row.idx}: stock UOM does not match the Item master.")
        _validate_warehouse(source, mr.company, row.idx, "source")
        if target:
            _validate_warehouse(target, mr.company, row.idx, "target")
        _validate_accounting(row, mr.company, mode)
        quantities[(row.item_code, source)] += qty
        expected[(row.item_code, source)] -= qty
        if target:
            expected[(row.item_code, target)] += qty
        data = {
            "item_code": row.item_code,
            "s_warehouse": source,
            "qty": qty,
            "uom": item.stock_uom,
            "stock_uom": item.stock_uom,
            "conversion_factor": 1,
            "material_request": mr.name,
            "material_request_item": row.name,
        }
        if target:
            data["t_warehouse"] = target
        if row.get("expense_account"):
            data["expense_account"] = row.expense_account
        if row.get("cost_center"):
            data["cost_center"] = row.cost_center
        rows.append(data)

    # Locks are ordered to reduce deadlocks when multiple approvals overlap.
    for (item_code, source), qty in sorted(quantities.items()):
        result = frappe.db.sql(
            "SELECT actual_qty, reserved_qty FROM `tabBin` WHERE item_code=%s AND warehouse=%s FOR UPDATE",
            (item_code, source), as_dict=True,
        )
        actual = flt(result[0].actual_qty) if result else 0.0
        reserved = flt(result[0].reserved_qty) if result else 0.0
        if actual + 1e-8 < qty:
            frappe.throw(f"Insufficient physical stock: {item_code}, {source}; have {actual}, need {qty}.")
        if actual - reserved + 1e-8 < qty:
            frappe.throw(f"Insufficient UNRESERVED stock: {item_code}, {source}; actual {actual}, reserved {reserved}, need {qty}.")
    return rows, dict(expected)


def _naming_series():
    field = frappe.get_meta("Stock Entry").get_field("naming_series")
    if not field:
        frappe.throw("No Stock Entry naming_series field found.")
    options = [line.strip() for line in (field.options or "").splitlines() if line.strip()]
    series = field.default or (options[0] if options else None)
    if not series:
        frappe.throw("Stock Entry naming series is not configured.")
    return series


def _verify_ledger(stock_entry_name, expected):
    posted = frappe.db.sql(
        """SELECT item_code, warehouse, SUM(actual_qty) AS qty
           FROM `tabStock Ledger Entry`
           WHERE voucher_type='Stock Entry' AND voucher_no=%s
           GROUP BY item_code, warehouse""",
        (stock_entry_name,), as_dict=True,
    )
    seen = {(p.item_code, p.warehouse): flt(p.qty) for p in posted}
    if set(seen) != set(expected):
        frappe.throw(f"Stock Ledger mismatch for {stock_entry_name}: warehouses/items differ.")
    for key, qty in expected.items():
        if abs(seen.get(key, 0) - qty) > 1e-7:
            frappe.throw(f"Stock Ledger mismatch for {stock_entry_name}: {key}: {seen.get(key)} != {qty}.")


def on_material_request_submit(mr, method=None):
    """One atomic transaction: if Stock Entry posting fails, approval is rolled back."""
    if not _enabled():
        return
    mode = _mode_for(mr)
    if mode == "manual":
        return
    _validate_workflow_and_origin(mr, mode)
    rows, expected = _build_rows_and_lock_stock(mr, mode)
    se = frappe.get_doc({
        "doctype": "Stock Entry",
        "naming_series": _naming_series(),
        "purpose": mode,
        "stock_entry_type": mode,
        "company": mr.company,
        LINK_FIELD: mr.name,
        "remarks": f"AUTO {mode} from {mr.name}; request purpose {mr.material_request_type}; COO {frappe.session.user}",
        "items": rows,
    })
    # Frappe checks the COO's Material Request workflow authorization before
    # this hook. Service-controlled posting is intentionally role constrained.
    se.flags.ignore_permissions = True
    se.insert(ignore_permissions=True)
    se.submit()
    if se.docstatus != 1:
        frappe.throw(f"Automatic Stock Entry {se.name} did not submit.")
    _verify_ledger(se.name, expected)
    return se.name


def before_material_request_cancel(mr, method=None):
    """Protect issued/transferred stock even if feature flag is turned off."""
    if not frappe.get_meta("Stock Entry").has_field(LINK_FIELD):
        return
    active = frappe.db.get_value("Stock Entry", {LINK_FIELD: mr.name, "docstatus": 1}, "name")
    if active:
        frappe.throw(f"Cannot cancel {mr.name}: automatic Stock Entry {active} is submitted. Reverse it first.")


def before_automatic_stock_entry_cancel(se, method=None):
    """Separate administrator-authorized reversal, never delete ledger entries."""
    if not se.get(LINK_FIELD):
        return
    approved = cint(frappe.conf.get(REVERSAL_FLAG, 0)) and "System Manager" in frappe.get_roles(frappe.session.user)
    if not approved:
        frappe.throw("Automatic Stock Entry reversal requires System Manager and temporary reversal flag.")
