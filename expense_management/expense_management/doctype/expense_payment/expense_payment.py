import frappe
from frappe.utils import flt
from erpnext.controllers.accounts_controller import AccountsController
from erpnext.accounts.general_ledger import make_gl_entries


class ExpensePayment(AccountsController):
    def validate(self):
        if not self.company:
            frappe.throw("Company is required")
        if not self.posting_date:
            frappe.throw("Posting Date is required")
        if not self.paid_from_account:
            frappe.throw("Paid From Account is required")
        if not self.expenses:
            frappe.throw("Add at least one expense row")

        self.total_amount = 0
        for i, row in enumerate(self.expenses, start=1):
            if not row.expense_account:
                frappe.throw(f"Row #{i}: Expense Account is required")
            if flt(row.amount) <= 0:
                frappe.throw(f"Row #{i}: Amount must be > 0")
            self.total_amount += flt(row.amount)

    def on_submit(self):
        self._make_gl_entries(cancel=0)

    def on_cancel(self):
        self._make_gl_entries(cancel=1)

    def _make_gl_entries(self, cancel=0):
        gl_entries = self.build_gl_preview()
        make_gl_entries(gl_entries, cancel=cancel, merge_entries=False)

    def build_gl_preview(self):
        dims = frappe.get_all("Accounting Dimension", filters={"disabled": 0}, pluck="fieldname")

        total_amount = 0
        for row in self.expenses or []:
            total_amount += flt(row.amount)

        gl_entries = []

        # Debit lines
        for row in self.expenses or []:
            gl = self.get_gl_dict(
                {
                    "account": row.expense_account,
                    "debit": flt(row.amount),
                    "credit": 0,
                    "debit_in_account_currency": flt(row.amount),
                    "credit_in_account_currency": 0,
                    "cost_center": getattr(row, "cost_center", None),
                    "project": getattr(row, "project", None),
                    "posting_date": self.posting_date,
                    "company": self.company,
                    "remarks": self.remarks,
                },
                item=row,
            )

            # copy dynamic dimensions from row
            for d in dims:
                if hasattr(row, d) and getattr(row, d):
                    gl[d] = getattr(row, d)

            gl_entries.append(gl)

        # Credit bank/cash total
        gl_entries.append(
            self.get_gl_dict(
                {
                    "account": self.paid_from_account,
                    "debit": 0,
                    "credit": flt(total_amount),
                    "debit_in_account_currency": 0,
                    "credit_in_account_currency": flt(total_amount),
					"cost_center": self.cost_center,
					"project": self.project,
                    "posting_date": self.posting_date,
                    "company": self.company,
                    "remarks": self.remarks,
                }
            )
        )

        return gl_entries


@frappe.whitelist()
def preview_gl_entries(doc):
    d = frappe.get_doc(frappe.parse_json(doc))
    d.set_missing_values()
    d.run_method("validate")
    return d.build_gl_preview()
