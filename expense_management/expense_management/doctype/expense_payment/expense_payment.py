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
        self._update_related_payment_request_status()

    def before_cancel(self):
        self.ignore_linked_doctypes = ("GL Entry",)
        

    def on_cancel(self):
        # اعكس/الغِ GL Entries
        self._make_gl_entries(cancel=1)
        self._update_related_payment_request_status()

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

    def _update_related_payment_request_status(self):
        """
        Update Payment Request status based on total submitted Expense Payments for the same reference.
        - Paid if total_paid >= requested
        - Partially Paid if total_paid > 0
        """

        # لازم يكون عندنا مرجع
        ref_dt = self.get("reference_doctype")
        ref_dn = self.get("reference")
        if not ref_dt or not ref_dn:
            return

        # هات كل Payment Requests submitted اللي لنفس المرجع
        pr_names = frappe.get_all(
            "Payment Request",
            filters={
                "docstatus": 1,
                "reference_doctype": ref_dt,
                "reference_name": ref_dn,
            },
            pluck="name",
        )
        if not pr_names:
            return

        # اجمع كل Expense Payments submitted لنفس المرجع
        total_paid = self._get_total_paid_for_reference(ref_dt, ref_dn)

        for pr_name in pr_names:
            pr = frappe.get_doc("Payment Request", pr_name)

            required = flt(
                pr.get("requested_amount")
                or pr.get("grand_total")
                or pr.get("amount")
                or 0
            )

            # إذا مبلغ PR غير واضح، لا تغير
            if required <= 0:
                continue

            new_status = pr.get("status")
            eps = 0.01

            if total_paid + eps >= required:
                new_status = "Paid"
            elif total_paid > eps:
                new_status = "Partially Paid"
            else:
                # رجّعه لحالة مناسبة (اختياري)
                if pr.get("status") in ["Paid", "Partially Paid"]:
                    new_status = "Initiated"

            if new_status and new_status != pr.get("status"):
                pr.db_set("status", new_status, update_modified=True)

            # لو عندكم حقل مخصص للمدفوع (اختياري)
            if hasattr(pr, "paid_amount"):
                pr.db_set("paid_amount", total_paid, update_modified=False)

    def _get_total_paid_for_reference(self, ref_dt, ref_dn):
        """
        Sum total_amount from all submitted Expense Payment docs for same reference.
        """
        ep_names = frappe.get_all(
            "Expense Payment",
            filters={
                "docstatus": 1,
                "reference_doctype": ref_dt,
                "reference": ref_dn,
            },
            pluck="name",
        )

        total = 0.0
        for name in ep_names:
            ep = frappe.get_doc("Expense Payment", name)

            # لو عندك total_amount في الرأس (واضح من كودك)
            if ep.get("total_amount") is not None:
                total += flt(ep.get("total_amount"))
            else:
                total += sum(flt(r.get("amount")) for r in (ep.get("expenses") or []))

        return flt(total)


@frappe.whitelist()
def preview_gl_entries(doc):
    d = frappe.get_doc(frappe.parse_json(doc))
    d.set_missing_values()
    d.run_method("validate")
    return d.build_gl_preview()
