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

    def _get_expense_account_from_dimension_table(self, row, dims):
        """
        يرجع حساب المصروف من جدول المابينج الخاص بـ Accounting Dimension
        باستخدام قيم الـ dimensions الموجودة في سطر المصروف.
        """

        # ✅ عدّل هذي الثلاثة حسب جدولك الحقيقي
        MAP_CHILD_DTYPE = "Accounting Dimension Account"   # مثال
        MAP_DIM_VALUE_FIELD = "dimension_value"            # مثال
        MAP_ACCOUNT_FIELD = "expense_account"              # مثال

        # امشِ على كل dimension fieldname معرف في النظام (branch, department, cost_center, project...)
        for dim_fieldname in dims:
            dim_value = getattr(row, dim_fieldname, None)
            if not dim_value:
                continue

            # اسم الـ Accounting Dimension record اللي يمثل هذا fieldname
            dim_name = frappe.db.get_value(
                "Accounting Dimension",
                {"disabled": 0, "fieldname": dim_fieldname},
                "name",
            )
            if not dim_name:
                continue

            filters = {
                "parent": dim_name,               # child مربوط بـ Accounting Dimension
                MAP_DIM_VALUE_FIELD: dim_value,
            }

            # لو جدول المابينج فيه company (اختياري)
            if frappe.get_meta(MAP_CHILD_DTYPE).has_field("company"):
                filters["company"] = self.company

            acc = frappe.db.get_value(MAP_CHILD_DTYPE, filters, MAP_ACCOUNT_FIELD)
            if acc:
                return acc

        return None

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
        for i, row in enumerate(self.expenses or [], start=1):
            amt = flt(row.amount)
            if amt <= 0:
                continue

            # ✅ هنا التغيير الرئيسي: حساب المصروف من جدول Accounting Dimension
            expense_acc = self._get_expense_account_from_dimension_table(row, dims)

            # لو ما لقى mapping، تقدر تخليه fallback على row.expense_account أو تمنع الحفظ
            if not expense_acc:
                frappe.throw(
                    f"Row #{i}: No Expense Account mapping found from Accounting Dimension table "
                    f"for the selected dimensions in this row."
                )
                # أو لو تبي fallback:
                # expense_acc = row.expense_account

            gl = self.get_gl_dict(
                {
                    "account": expense_acc,
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

        # 2) Credit: bank/cash from main doctype (زي ما هو)
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
