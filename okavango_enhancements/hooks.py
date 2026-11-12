from . import __version__ as app_version

app_name = "okavango_enhancements"
app_title = "Okavango Enhancements"
app_publisher = "Bantoo"
app_description = "Custom scripts for Okavango"
app_email = "devs@thebantoo.com"
app_license = "MIT"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/okavango_enhancements/css/okavango_enhancements.css"
# app_include_js = "/assets/okavango_enhancements/js/okavango_enhancements.js"

# include js, css files in header of web template
# web_include_css = "/assets/okavango_enhancements/css/okavango_enhancements.css"
# web_include_js = "/assets/okavango_enhancements/js/okavango_enhancements.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "okavango_enhancements/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}


# doctype_js = {"Sales Invoice" : "/public/js/scripts/sales_invoice.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
#	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
#	"methods": "okavango_enhancements.utils.jinja_methods",
#	"filters": "okavango_enhancements.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "okavango_enhancements.install.before_install"
# after_install = "okavango_enhancements.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "okavango_enhancements.uninstall.before_uninstall"
# after_uninstall = "okavango_enhancements.uninstall.after_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "okavango_enhancements.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
#	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
#	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
#	"ToDo": "custom_app.overrides.CustomToDo"
# }

# override_doctype_class = {
# 	"Sales Invoice": "okavango_enhancements.overrides.sales_invoice.CustomSalesInvoice"
# }

# override_whitelisted_methods = {
# 	"erpnext.stock.doctype.batch.batch.get_batch_no": "okavango_enhancements.app.get_single_batch_no"
# }
# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
#	"*": {
#		"on_update": "method",
#		"on_cancel": "method",
#		"on_trash": "method"
#	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
#	"all": [
#		"okavango_enhancements.tasks.all"
#	],
#	"daily": [
#		"okavango_enhancements.tasks.daily"
#	],
#	"hourly": [
#		"okavango_enhancements.tasks.hourly"
#	],
#	"weekly": [
#		"okavango_enhancements.tasks.weekly"
#	],
#	"monthly": [
#		"okavango_enhancements.tasks.monthly"
#	],
# }

# Testing
# -------

# before_tests = "okavango_enhancements.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
#	"frappe.desk.doctype.event.event.get_events": "okavango_enhancements.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
#	"Task": "okavango_enhancements.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]


# User Data Protection
# --------------------

# user_data_fields = [
#	{
#		"doctype": "{doctype_1}",
#		"filter_by": "{filter_by}",
#		"redact_fields": ["{field_1}", "{field_2}"],
#		"partial": 1,
#	},
#	{
#		"doctype": "{doctype_2}",
#		"filter_by": "{filter_by}",
#		"partial": 1,
#	},
#	{
#		"doctype": "{doctype_3}",
#		"strict": False,
#	},
#	{
#		"doctype": "{doctype_4}"
#	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
#	"okavango_enhancements.auth.validate"
# ]


# ----------------------------------------------------------
# Operational Payment Request - accounting and validation
# ----------------------------------------------------------

doc_events = {
    "Journal Entry": {
        "validate": "okavango_enhancements.operational_payment.controls.validate_journal_entry",
        "before_submit": "okavango_enhancements.operational_payment.controls.validate_journal_entry",
        "after_insert": "okavango_enhancements.operational_payment.controls.after_insert_journal_entry",
        "on_update": "okavango_enhancements.operational_payment.controls.on_update_journal_entry",
        "before_cancel": "okavango_enhancements.operational_payment.controls.before_cancel_journal_entry",
        "on_cancel": "okavango_enhancements.operational_payment.controls.on_cancel_journal_entry",
        "on_trash": "okavango_enhancements.operational_payment.controls.on_trash_journal_entry",
    },
    "Operational Payment Request": {
        "validate": "okavango_enhancements.operational_payment.controls.validate_operational_payment_request",
        "before_update_after_submit": "okavango_enhancements.operational_payment.controls.before_update_after_submit_operational_request",
    },
}


# ----------------------------------------------------------
# Operational Payment Request - selected fixtures only
# ----------------------------------------------------------

fixtures = [
    {
        "dt": "Role",
        "filters": [
            ["name", "in", [
                "Operational Payment Requester",
                "Operational Payment Approver",
                "Operational Payment Finance",
            ]]
        ],
    },
    {
        "dt": "Custom Field",
        "filters": [
            ["dt", "=", "Journal Entry"],
            ["fieldname", "in", [
                "custom_operational_payment_request",
                "custom_customer_order_reference",
            ]],
        ],
    },
    {
        "dt": "Client Script",
        "filters": [
            ["name", "=", "Okavango OPR Original Package Clone"]
        ],
    },
    # The Workflow is maintained separately in zz_workflow.json.
    # Frappe v15 otherwise regenerates workflow.json on export-fixtures.
]

# OPR-specific workflow dependencies.
# Shared states Draft, Approved and Pending Approval are excluded.
fixtures.extend([
    {
        "dt": "Workflow State",
        "filters": [["name", "=", "Paid"]],
    },
    {
        "dt": "Workflow Action Master",
        "filters": [["name", "in", [
            "Approve Operational Payment",
            "Mark Operational Payment Paid",
            "Reopen Operational Payment for Correction",
            "Return Operational Payment to Draft",
            "Send Operational Payment for Approval",
        ]]],
    },
])
