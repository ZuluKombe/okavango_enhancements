// OKAVANGO FOODS - MATERIAL REQUEST APPROVAL INDICATOR
// Clone test - display only; no database changes.

(function () {
    const settings = frappe.listview_settings["Material Request"];

    if (!settings) {
        console.error("Material Request list settings not loaded");
        return;
    }

    // Preserve ERPNext's existing stock-progress indicators.
    const original_indicator = settings.get_indicator;

    settings.add_fields = [
        ...new Set([
            ...(settings.add_fields || []),
            "workflow_state"
        ])
    ];

    settings.get_indicator = function (doc) {
        // Cancelled documents always take priority.
        if (doc.docstatus === 2) {
            return [__("Cancelled"), "red", "docstatus,=,2"];
        }

        const indicators = {
            "Approved": ["Approved", "green"],
            "Pending Department Approval": [
                "Pending Department Approval", "orange"
            ],
            "Pending COO Approval": [
                "Pending COO Approval", "orange"
            ],
            "Returned for Correction": [
                "Returned for Correction", "orange"
            ],
            "Rejected": ["Rejected", "red"]
        };

        const state = doc.workflow_state;

        if (state && indicators[state]) {
            // Final approval must be submitted.
            if (state === "Approved" && doc.docstatus !== 1) {
                return original_indicator
                    ? original_indicator.call(this, doc)
                    : null;
            }

            return [
                __(indicators[state][0]),
                indicators[state][1],
                "workflow_state,=," + state
            ];
        }

        // Preserve the standard ERPNext indicator behavior.
        return original_indicator
            ? original_indicator.call(this, doc)
            : null;
    };
})();
