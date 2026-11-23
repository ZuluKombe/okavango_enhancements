// Users select the date period themselves; no date default from the system clock.
frappe.query_reports["Sales Invoice Delivery Schedule"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("Expected Delivery Date From"),
            fieldtype: "Date",
            reqd: 1
        },
        {
            fieldname: "to_date",
            label: __("Expected Delivery Date To"),
            fieldtype: "Date",
            reqd: 1
        },
        {
            fieldname: "from_time",
            label: __("Delivery Time From (Lusaka)"),
            fieldtype: "Time",
            default: "05:00:00",
            reqd: 1
        },
        {
            fieldname: "to_time",
            label: __("Delivery Time To (Lusaka)"),
            fieldtype: "Time",
            default: "20:00:00",
            reqd: 1
        },
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company"
        }
    ]
};
