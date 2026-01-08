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

        total = 0
        for i, row in enumerate(self.expenses, start=1):
            if not row.expense_account:
                frappe.throw(f"Row #{i}: Expense Account is required")
            if flt(row.amount) <= 0:
                frappe.throw(f"Row #{i}: Amount must be > 0")
            total += flt(row.amount)

        self.total_amount = total

    def on_submit(self):
        self._make_gl_entries(cancel=0)

    def before_cancel(self):
        self.ignore_linked_doctypes = ("GL Entry",)

    def on_cancel(self):
        # اعكس/الغِ GL Entries
        self._make_gl_entries(cancel=1)

    def _make_gl_entries(self, cancel=0):
        gl_entries = self.build_gl_preview()
        make_gl_entries(gl_entries, cancel=cancel, merge_entries=False)

    def build_gl_preview(self):
        dims = frappe.get_all("Accounting Dimension", filters={"disabled": 0}, pluck="fieldname")

        total_amount = sum(flt(r.amount) for r in (self.expenses or []))
        if total_amount <= 0:
            return []

        gl_entries = []

        is_inter = int(getattr(self, "is_inter_account", 0) or 0)
        from_acc = getattr(self, "from_account", None)
        to_acc = getattr(self, "to_account", None)

        if is_inter and (not from_acc or not to_acc):
            frappe.throw("From Account and To Account are required when Inter Account is enabled")

        # 1) Debit: expense lines
        for row in self.expenses or []:
            amt = flt(row.amount)
            if amt <= 0:
                continue

            gl = self.get_gl_dict(
                {
                    "account": row.expense_account,
                    "debit": amt,
                    "credit": 0,
                    "debit_in_account_currency": amt,
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


        credit_account_for_expenses = self.paid_from_account


        gl_entries.append(
            self.get_gl_dict(
                {
                    "account": credit_account_for_expenses,
                    "debit": 0,
                    "credit": flt(total_amount),
                    "debit_in_account_currency": 0,
                    "credit_in_account_currency": flt(total_amount),
                    "posting_date": self.posting_date,
                    "company": self.company,
                    "remarks": self.remarks,
                }
            )
        )

        if is_inter:
            gl_entries.append(
                self.get_gl_dict(
                    {
                        "account": to_acc,
                        "debit": flt(total_amount),
                        "credit": 0,
                        "debit_in_account_currency": flt(total_amount),
                        "credit_in_account_currency": 0,
                        "posting_date": self.posting_date,
                        "company": self.company,
                        "remarks": self.remarks,
                    }
                )
            )

            gl_entries.append(
                self.get_gl_dict(
                    {
                        "account": from_acc,
                        "debit": 0,
                        "credit": flt(total_amount),
                        "debit_in_account_currency": 0,
                        "credit_in_account_currency": flt(total_amount),
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
