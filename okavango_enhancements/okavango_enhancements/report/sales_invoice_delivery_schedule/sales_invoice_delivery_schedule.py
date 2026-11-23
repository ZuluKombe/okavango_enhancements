"""Read-only EDT delivery-date schedule for ERPNext v15 (Africa/Lusaka)."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import getdate


TIME_ZONE = ZoneInfo("Africa/Lusaka")
EDT_FIELD = "custom_expected_delivery_time"
MAX_INVOICES = 50000  # Never silently truncate a selected period.


def _delivery_field():
    field = frappe.get_meta("Sales Invoice").get_field(EDT_FIELD)
    if field is None:
        frappe.throw(_("Sales Invoice is missing EDT field {0}.").format(EDT_FIELD))
    if field.fieldtype != "Datetime":
        frappe.throw(_("EDT field {0} must be Datetime.").format(EDT_FIELD))
    return field


def _local_datetime(value):
    """Read ERPNext naive datetimes as site-local/Lusaka wall time."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt.astimezone(TIME_ZONE).replace(tzinfo=None) if dt.tzinfo else dt


def _seconds(value):
    """Parse Time-filter values; do not use the delivery Datetime as a Time."""
    if isinstance(value, timedelta):
        seconds = int(value.total_seconds())
        return seconds if 0 <= seconds < 86400 else None
    if isinstance(value, time):
        return value.hour * 3600 + value.minute * 60 + value.second
    if isinstance(value, str):
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
            try:
                return _seconds(datetime.strptime(value.strip(), fmt).time())
            except ValueError:
                pass
    return None


def _time_filter(value, label):
    parsed = _seconds(value)
    if parsed is None:
        frappe.throw(_("Invalid {0}; use a time such as 05:00 or 20:00.").format(label))
    return parsed


def execute(filters=None):
    filters = filters or {}
    if not frappe.has_permission("Sales Invoice", ptype="read"):
        frappe.throw(_("Not permitted to read Sales Invoices."), frappe.PermissionError)

    # Date selection is mandatory: never silently default to today's date.
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Select both Expected Delivery Date From and To."))

    from_date = getdate(filters["from_date"])
    to_date = getdate(filters["to_date"])
    if from_date > to_date:
        frappe.throw(_("Expected Delivery Date From cannot be after To."))

    start_seconds = _time_filter(filters.get("from_time") or "05:00", _("From Time"))
    end_seconds = _time_filter(filters.get("to_time") or "20:00", _("To Time"))
    if start_seconds > end_seconds:
        frappe.throw(_("Delivery Time From cannot be later than To."))

    _delivery_field()
    invoice_filters = {
        "docstatus": 1,
        EDT_FIELD: ["between", [
            f"{from_date} 00:00:00",
            f"{to_date} 23:59:59.999999",
        ]],
    }
    if filters.get("company"):
        invoice_filters["company"] = filters["company"]

    # frappe.get_list applies Sales Invoice/user permission filtering.
    invoices = frappe.get_list(
        "Sales Invoice",
        fields=["name", "posting_date", "customer", "customer_name", "company",
                "grand_total", "currency", "status", EDT_FIELD],
        filters=invoice_filters,
        order_by=f"{EDT_FIELD} asc, name asc",
        limit_page_length=MAX_INVOICES + 1,
    )
    if len(invoices) > MAX_INVOICES:
        frappe.throw(_("More than 50,000 invoices in the selected delivery-date period. Narrow the dates or company."))

    result = []
    skipped = 0
    for row in invoices:
        dt = _local_datetime(row.get(EDT_FIELD))
        if dt is None:
            skipped += 1
            continue
        seconds = dt.hour * 3600 + dt.minute * 60 + dt.second
        # Revalidate local date for timezone-aware values in the unlikely event
        # that they cross a date boundary when converted to Africa/Lusaka.
        if not (from_date <= dt.date() <= to_date and start_seconds <= seconds <= end_seconds):
            continue
        result.append({
            "delivery_date": dt.date(),
            "delivery_time": dt.strftime("%H:%M"),
            "invoice": row.name,
            "posting_date": row.posting_date,
            "customer_name": row.customer_name,
            "company": row.company,
            "status": row.status,
            "grand_total": row.grand_total,
            "currency": row.currency,
            "_sort": (dt, row.name),
        })

    result.sort(key=lambda row: row["_sort"])
    for row in result:
        del row["_sort"]

    columns = [
        {"label": _("Expected Delivery Date"), "fieldname": "delivery_date", "fieldtype": "Date", "width": 150},
        {"label": _("Expected Delivery Time (Lusaka)"), "fieldname": "delivery_time", "fieldtype": "Data", "width": 205},
        {"label": _("Sales Invoice"), "fieldname": "invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 185},
        {"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 185},
        {"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 115},
        {"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 180},
        {"label": _("Invoice Status"), "fieldname": "status", "fieldtype": "Data", "width": 125},
        {"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "options": "currency", "width": 125},
        {"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "width": 90},
    ]
    message = _("Showing submitted Sales Invoices by their EDT delivery date and time (Africa/Lusaka). Date filters use EDT, not invoice Posting Date.")
    if skipped:
        message += _(" {0} invoices were skipped due to missing or invalid EDT.").format(skipped)
    return columns, result, message
