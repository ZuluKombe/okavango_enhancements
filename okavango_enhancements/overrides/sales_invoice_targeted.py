"""Targeted Okavango Sales Invoice extension.

Inherits standard ERPNext SalesInvoice.
Candidate only: not activated until regression testing passes.
"""

import frappe
from frappe.utils import cint, flt
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.stock.doctype.packed_item.packed_item import (
    get_packed_item_bin_qty,
    get_packed_item_details,
    get_product_bundle_items,
    is_product_bundle,
    set_product_bundle_rate_amount,
    update_product_bundle_rate,
    update_packed_item_basic_data,
    update_packed_item_from_cancelled_doc,
    update_packed_item_price_data,
)
from okavango_enhancements.app import set_batch_nos


class TargetedSalesInvoice(SalesInvoice):
    """Preserve Okavango batch and Product Bundle behavior."""

    def validate(self):
        previous = getattr(
            self.flags, "okavango_skip_packing", False
        )
        self.flags.okavango_skip_packing = True
        try:
            super().validate()
        finally:
            self.flags.okavango_skip_packing = previous

    def before_save(self):
        super().before_save()
        if (
            self._action != "submit"
            and self.update_stock
            and not self.is_return
        ):
            set_batch_nos(self, "warehouse", True)
        self.update_packing_list()


    def before_submit(self):
        super().before_submit()

        if not cint(self.update_stock):
            return

        if cint(self.is_return):
            self._validate_batch_return_packing_v15()
            return

        from erpnext.stock.doctype.batch.batch import get_batch_qty

        allocated = {}

        for item_row in self.get("items") or []:
            if not is_product_bundle(item_row.item_code):
                continue

            for component in get_product_bundle_items(item_row.item_code):
                has_batch = frappe.db.get_value(
                    "Item", component.item_code, "has_batch_no"
                )

                if not has_batch:
                    continue

                required = (
                    flt(component.qty) * flt(item_row.stock_qty)
                )

                rows = [
                    row for row in self.get("packed_items") or []
                    if row.parent_detail_docname == item_row.name
                    and row.item_code == component.item_code
                ]

                packed_total = sum(flt(row.qty) for row in rows)

                if abs(required - packed_total) > 0.000001:
                    frappe.throw(
                        f"Batch allocation incomplete for "
                        f"{component.item_code}: required {required}, "
                        f"allocated {packed_total}"
                    )

                for row in rows:
                    if flt(row.qty) <= 0:
                        frappe.throw(
                            f"Invalid packed quantity for {component.item_code}"
                        )

                    if not row.batch_no or not row.warehouse:
                        frappe.throw(
                            f"Batch and warehouse are required for "
                            f"{component.item_code}"
                        )

                    batch_item = frappe.db.get_value(
                        "Batch", row.batch_no, "item"
                    )

                    if batch_item != component.item_code:
                        frappe.throw(
                            f"Batch {row.batch_no} does not belong "
                            f"to {component.item_code}"
                        )

                    key = (
                        component.item_code,
                        row.batch_no,
                        row.warehouse,
                    )

                    allocated[key] = (
                        allocated.get(key, 0) + flt(row.qty)
                    )

        for (item_code, batch_no, warehouse), required in allocated.items():
            available = flt(
                get_batch_qty(
                    batch_no=batch_no,
                    warehouse=warehouse,
                    item_code=item_code,
                )
            )

            if required > available + 0.000001:
                frappe.throw(
                    f"Insufficient batch stock: {item_code}, "
                    f"batch {batch_no}, warehouse {warehouse}. "
                    f"Required {required}, available {available}"
                )


    def update_packing_list(self):
        if getattr(self.flags, "okavango_skip_packing", False):
            return
        if cint(self.update_stock) == 1:
            self.make_or_update_packing_list()
        else:
            self.set("packed_items", [])

    def add_packed_item_row(self, packing_item, qty, batch, warehouse, main_item_row, packed_items_table, reset):
        """Add and return packed item row.
        doc: Transaction document
        packing_item (dict): Packed Item details
        main_item_row (dict): Items table row corresponding to packed item
        packed_items_table (dict): Packed Items table before save (indexed)
        reset (bool): State if table is reset or preserved as is
        """
        exists, pi_row = False, {}

        # check if row already exists in packed items table
        if batch:
            key = (main_item_row.item_code, packing_item.item_code, main_item_row.name, batch, warehouse)
        else:
            key = (main_item_row.item_code, packing_item.item_code, main_item_row.name)

        if packed_items_table.get(key):
            pi_row, exists = packed_items_table.get(key), True

        if not exists:
            pi_row = self.append("packed_items", {})
        elif reset:  # add row if row exists but table is reset
            pi_row.idx, pi_row.name = None, None
            pi_row = self.append("packed_items", pi_row)

        return pi_row

    def get_indexed_packed_items_table(self):
        """
        Create dict from stale packed items table like:
        {(Parent Item 1, Bundle Item 1, ae4b5678): {...}, (key): {value}}

        Use: to quickly retrieve/check if row existed in table instead of looping n times
        """
        indexed_table = {}
        for packed_item in self.get("packed_items"):
            if packed_item.batch_no:
                key = (packed_item.parent_item, packed_item.item_code, packed_item.parent_detail_docname, packed_item.batch_no, packed_item.warehouse)
            else:
                key = (packed_item.parent_item, packed_item.item_code, packed_item.parent_detail_docname)
            indexed_table[key] = packed_item

        return indexed_table

    def is_batch(self, item):
        """Check Item batch tracking, not existing Batch records."""
        return bool(
            frappe.db.get_value("Item", item, "has_batch_no")
        )



    def _return_bundle_component_batches_v15(
        self, bundle_item, item_row
    ):
        """Reconstruct a full batch return from its original invoice."""
        tolerance = 0.000001

        if not self.return_against:
            frappe.throw(
                "Batch Product Bundle returns require an original Sales Invoice"
            )

        source_row_name = item_row.get("sales_invoice_item")
        if not source_row_name:
            frappe.throw(
                "Original Sales Invoice Item reference is required "
                "for a batch Product Bundle return"
            )

        if frappe.db.get_value(
            "Item", bundle_item.item_code, "has_serial_no"
        ):
            frappe.throw(
                "Combined serial/batch Product Bundle returns "
                "require separate validation"
            )

        source_invoice = frappe.db.get_value(
            "Sales Invoice",
            self.return_against,
            ["docstatus", "company", "is_return", "update_stock"],
            as_dict=True,
        )

        if (
            not source_invoice
            or source_invoice.docstatus != 1
            or source_invoice.company != self.company
            or source_invoice.is_return
            or not source_invoice.update_stock
        ):
            frappe.throw(
                "Invalid original Sales Invoice for batch return"
            )

        source_item = frappe.db.get_value(
            "Sales Invoice Item",
            source_row_name,
            ["parent", "item_code", "stock_qty"],
            as_dict=True,
        )

        if (
            not source_item
            or source_item.parent != self.return_against
            or source_item.item_code != item_row.item_code
            or flt(source_item.stock_qty) <= 0
        ):
            frappe.throw(
                "Invalid original Sales Invoice Item reference"
            )

        if abs(
            flt(item_row.stock_qty) + flt(source_item.stock_qty)
        ) > tolerance:
            frappe.throw(
                "Partial batch Product Bundle returns are not yet "
                "enabled. Return the full original quantity."
            )

        previous_returns = frappe.get_all(
            "Sales Invoice",
            filters={
                "return_against": self.return_against,
                "is_return": 1,
                "docstatus": 1,
            },
            pluck="name",
            limit_page_length=0,
        )

        for return_name in previous_returns:
            if frappe.db.exists(
                "Sales Invoice Item",
                {
                    "parent": return_name,
                    "sales_invoice_item": source_row_name,
                },
            ):
                frappe.throw(
                    "This batch Product Bundle invoice item "
                    "has already been returned"
                )

        source_rows = frappe.get_all(
            "Packed Item",
            filters={
                "parenttype": "Sales Invoice",
                "parent": self.return_against,
                "parent_detail_docname": source_row_name,
                "item_code": bundle_item.item_code,
            },
            fields=["batch_no", "warehouse", "qty"],
            order_by="idx asc",
            limit_page_length=0,
        )

        if not source_rows:
            frappe.throw(
                f"No original packed batch rows found for "
                f"{bundle_item.item_code}"
            )

        original_total = sum(flt(row.qty) for row in source_rows)
        expected_total = (
            flt(bundle_item.qty) * flt(source_item.stock_qty)
        )

        if abs(original_total - expected_total) > tolerance:
            frappe.throw(
                f"Product Bundle component quantity has changed "
                f"for {bundle_item.item_code}. "
                "Manual review is required."
            )

        result = []

        for row in source_rows:
            if (
                not row.batch_no
                or not row.warehouse
                or flt(row.qty) <= 0
            ):
                frappe.throw(
                    "Original invoice contains an invalid "
                    "batch allocation"
                )

            batch_item = frappe.db.get_value(
                "Batch", row.batch_no, "item"
            )

            if batch_item != bundle_item.item_code:
                frappe.throw(
                    f"Original batch {row.batch_no} does not "
                    f"belong to {bundle_item.item_code}"
                )

            result.append({
                "item_code": bundle_item.item_code,
                "qty": -flt(row.qty),
                "batch": row.batch_no,
                "warehouse": row.warehouse,
                "actual_batch_qty": flt(row.qty),
            })

        return result

    def _validate_batch_return_packing_v15(self):
        """Fail closed when full-return batch rows do not match source."""
        tolerance = 0.000001

        for item_row in self.get("items") or []:
            if not is_product_bundle(item_row.item_code):
                continue

            for component in get_product_bundle_items(
                item_row.item_code
            ):
                if not frappe.db.get_value(
                    "Item", component.item_code, "has_batch_no"
                ):
                    continue

                expected = self._return_bundle_component_batches_v15(
                    component, item_row
                )

                actual = [
                    row for row in self.get("packed_items") or []
                    if row.parent_detail_docname == item_row.name
                    and row.item_code == component.item_code
                ]

                if len(expected) != len(actual):
                    frappe.throw(
                        "Batch return packing mismatch: "
                        f"{component.item_code}"
                    )

                expected_map = {}
                actual_map = {}

                for row in expected:
                    key = (row["batch"], row["warehouse"])
                    expected_map[key] = (
                        expected_map.get(key, 0) + flt(row["qty"])
                    )

                for row in actual:
                    if (
                        not row.batch_no
                        or not row.warehouse
                        or flt(row.qty) >= 0
                    ):
                        frappe.throw(
                            "Invalid return batch row: "
                            f"{component.item_code}"
                        )

                    key = (row.batch_no, row.warehouse)
                    actual_map[key] = (
                        actual_map.get(key, 0) + flt(row.qty)
                    )

                if expected_map.keys() != actual_map.keys():
                    frappe.throw(
                        "Batch return references do not match "
                        "the original invoice"
                    )

                for key, expected_qty in expected_map.items():
                    if abs(
                        expected_qty - actual_map[key]
                    ) > tolerance:
                        frappe.throw(
                            "Batch return quantity does not match "
                            f"the original invoice: {key}"
                        )

    def _allocate_batch_components_v15(
        self, bundle_item, item_row, allocated_batches
    ):
        """Allocate available batches for an outgoing bundle component."""
        from erpnext.stock.doctype.batch.batch import get_batch_qty

        item_code = bundle_item.item_code
        warehouse = item_row.get("warehouse")

        required = flt(bundle_item.qty) * flt(item_row.stock_qty)

        if required <= 0:
            return []

        if not warehouse:
            frappe.throw(
                f"Warehouse is required for batch-tracked "
                f"bundle component {item_code}"
            )

        batches = get_batch_qty(
            item_code=item_code,
            warehouse=warehouse,
        )

        remaining = required
        selected = []

        for batch in batches:
            batch_no = batch.get("batch_no")
            batch_warehouse = batch.get("warehouse")

            if not batch_no or batch_warehouse != warehouse:
                continue

            key = (item_code, batch_no, warehouse)

            already_allocated = flt(
                allocated_batches.get(key, 0)
            )

            available = max(
                0,
                flt(batch.get("qty")) - already_allocated,
            )

            quantity = min(remaining, available)

            if quantity <= 0:
                continue

            selected.append({
                "item_code": item_code,
                "qty": quantity,
                "batch": batch_no,
                "warehouse": warehouse,
                "actual_batch_qty": flt(batch.get("qty")),
            })

            allocated_batches[key] = (
                already_allocated + quantity
            )

            remaining -= quantity

            if remaining <= 0.000001:
                break

        return selected

    def make_or_update_packing_list(self):
        "Make/Update packing list for Product Bundle Item."

        if self.get("_action") and self._action == "update_after_submit":
            return

        parent_items_price, reset = {}, False
        set_price_from_children = frappe.db.get_single_value(
            "Selling Settings", "editable_bundle_item_rates"
        )

        stale_packed_items_table = self.get_indexed_packed_items_table()

        reset = self.reset_packing_list()
        allocated_batches = {}

        # get_row_batches(doc, cdt, bundle_item, item)

        for item_row in self.get("items"):
            if is_product_bundle(item_row.item_code):

                for bundle_item in get_product_bundle_items(item_row.item_code):

                    if self.is_batch(bundle_item.item_code):
                        # decide how many rows, qty and assign batch here
                        packed_item_batches = (
                            self._return_bundle_component_batches_v15(
                                bundle_item, item_row
                            )
                            if cint(self.is_return)
                            else self._allocate_batch_components_v15(
                                bundle_item, item_row, allocated_batches
                            )
                        )
                        if packed_item_batches:
                            for pi in packed_item_batches:
                                self.process_packed_item_row(bundle_item, item_row, stale_packed_items_table, reset, set_price_from_children, parent_items_price, pi)
                    else:
                        self.process_packed_item_row(bundle_item, item_row, stale_packed_items_table, reset, set_price_from_children, parent_items_price)

        if parent_items_price:
            set_product_bundle_rate_amount(self, parent_items_price)

    def process_packed_item_row(self, bundle_item, item_row, stale_packed_items_table, reset, set_price_from_children, parent_items_price, packed_item=None):
        if not packed_item:
            qty = flt(bundle_item.qty) * flt(item_row.stock_qty)
            batch = ""
            warehouse = ""
        else:
            qty = packed_item.get('qty')
            batch = packed_item.get('batch')
            warehouse = packed_item.get('warehouse')

        pi_row = self.add_packed_item_row(
            packing_item=bundle_item,
            qty=qty,
            batch=batch,
            warehouse=warehouse,
            main_item_row=item_row,
            packed_items_table=stale_packed_items_table,
            reset=reset,
        )
        item_data = get_packed_item_details(bundle_item.item_code, self.company)
        if packed_item:
            self.update_packed_item_basic_data(item_row, pi_row, bundle_item, item_data, qty=packed_item.get('qty'), batch=packed_item.get('batch'), warehouse=packed_item.get('warehouse'), actual_batch_qty=packed_item.get('actual_batch_qty') )
        else:
            update_packed_item_basic_data(item_row, pi_row, bundle_item, item_data)

        self.update_packed_item_stock_data(item_row, pi_row, bundle_item, item_data, doc=self)
        update_packed_item_price_data(pi_row, item_data, self)
        update_packed_item_from_cancelled_doc(item_row, bundle_item, pi_row, self)

        if set_price_from_children:  # create/update bundle item wise price dict
            update_product_bundle_rate(parent_items_price, pi_row, item_row)

    def _packing_inputs_v15(self, item):
        """Fields that determine packed-item quantity and location."""
        return (
            item.name,
            item.item_code,
            flt(item.get("qty")),
            flt(item.get("stock_qty")),
            item.get("warehouse"),
            item.get("target_warehouse"),
            item.get("uom"),
            flt(item.get("conversion_factor")),
        )

    def reset_packing_list(self):
        "Conditionally reset the table and return if it was reset or not."
        reset_table = False
        doc_before_save = self.get_doc_before_save()

        if doc_before_save:
            # reset table if:
            # 1. items were deleted
            # 2. if bundle item replaced by another item (same no. of items but different items)
            # we maintain list to track recurring item rows as well
            items_before_save = [
                self._packing_inputs_v15(item)
                for item in (doc_before_save.get("items") or [])
            ]
            items_after_save = [
                self._packing_inputs_v15(item)
                for item in (self.get("items") or [])
            ]
            #items_before_save = [(item.name, item.item_code) for item in doc_before_save.get("items")]
            #items_after_save = [(item.name, item.item_code) for item in self.get("items")]
            reset_table = items_before_save != items_after_save
        else:
            # reset: if via Update Items OR
            # if new mapped doc with packed items set (SO -> DN)
            # (cannot determine action)
            reset_table = True

        if reset_table:
            self.set("packed_items", [])
        return reset_table

    def update_packed_item_basic_data(self, main_item_row, pi_row, packing_item, item_data, qty, batch, warehouse, actual_batch_qty):
        pi_row.parent_item = main_item_row.item_code
        pi_row.parent_detail_docname = main_item_row.name
        pi_row.item_code = packing_item.item_code
        pi_row.item_name = item_data.item_name
        pi_row.uom = item_data.stock_uom
        pi_row.qty = qty
        pi_row.batch_no = batch
        pi_row.actual_batch_qty = actual_batch_qty
        pi_row.warehouse = warehouse
        pi_row.conversion_factor = main_item_row.conversion_factor

        if not pi_row.description:
            pi_row.description = packing_item.get("description")

    def update_packed_item_stock_data(self, main_item_row, pi_row, packing_item, item_data, doc):
        # TODO batch_no, actual_batch_qty, incoming_rate
        if not pi_row.warehouse and not doc.amended_from:
            fetch_warehouse = doc.get("is_pos") or item_data.is_stock_item or not item_data.default_warehouse
            pi_row.warehouse = (
                main_item_row.warehouse
                if (fetch_warehouse and main_item_row.warehouse)
                else item_data.default_warehouse
            )

        if not pi_row.target_warehouse:
            pi_row.target_warehouse = main_item_row.get("target_warehouse")

        bin = get_packed_item_bin_qty(packing_item.item_code, pi_row.warehouse)

        pi_row.actual_qty = flt(bin.get("actual_qty"))
        pi_row.projected_qty = flt(bin.get("projected_qty"))
        pi_row.use_serial_batch_fields = frappe.db.get_single_value(
            "Stock Settings", "use_serial_batch_fields"
        )
