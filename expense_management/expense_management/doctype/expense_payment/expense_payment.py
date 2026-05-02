import frappe
from frappe.utils import cint, flt
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
        self._set_tax_totals()
        self._validate_bank_charge()

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

    def _resolve_expense_account(self, row, dims, row_idx):
        expense_acc = self._get_expense_account_from_dimension_table(row, dims)
        if not expense_acc:
            frappe.throw(
                f"Row #{row_idx}: No Expense Account mapping found from Accounting Dimension table "
                f"for the selected dimensions in this row."
            )

        return expense_acc

    def _set_tax_totals(self):
        total_amount = sum(flt(row.amount) for row in (self.expenses or []))
        total_tax = 0

        for idx, row in enumerate(self.expense_taxes_and_charges or [], start=1):
            rate = flt(getattr(row, "rate", 0))
            tax_amount = (
                total_amount * (rate / 100.0)
                if rate
                else flt(getattr(row, "tax_amount", 0))
            )
            account_head = getattr(row, "account_head", None)

            if tax_amount and not account_head:
                frappe.throw(f"Tax Row #{idx}: Account Head is required")

            row.tax_amount = tax_amount
            row.total = total_amount + tax_amount
            total_tax += tax_amount

        self.total_amount = total_amount
        self.total_tax_and_charges = total_tax
        self.grand_total = total_amount + total_tax

    def _validate_bank_charge(self):
        if not cint(self.apply_bank_charge or 0):
            self.bank_charge_amount = 0
            self.bank_charge_account = None
            return

        if not self.bank_charge_account and self.company:
            self.bank_charge_account = frappe.db.get_value(
                "Company", self.company, "default_charges_account"
            )

        if flt(self.bank_charge_amount) <= 0:
            frappe.throw("Bank Charge Amount must be greater than zero")

        if not self.bank_charge_account:
            frappe.throw("Bank Charge Account is required when Apply Bank Charge is enabled")

    def _append_inter_account_gl_entries(self, gl_entries, total_credit, dims):
        is_inter = int(getattr(self, "is_inter_account", 0) or 0)
        from_acc = getattr(self, "from_account", None)
        to_acc = getattr(self, "to_account", None)

        if not is_inter:
            return

        if not from_acc or not to_acc:
            frappe.throw("From Account and To Account are required when Inter Account is enabled")

        gl_entries.append(
            self.get_gl_dict(
                {
                    "account": to_acc,
                    "debit": flt(total_credit),
                    "credit": 0,
                    "debit_in_account_currency": flt(total_credit),
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
                    "credit": flt(total_credit),
                    "debit_in_account_currency": 0,
                    "credit_in_account_currency": flt(total_credit),
                    "posting_date": self.posting_date,
                    "company": self.company,
                    "remarks": self.remarks,
                }
            )
        )

    def build_gl_preview(self):
        dims = frappe.get_all("Accounting Dimension", filters={"disabled": 0}, pluck="fieldname")
        self._set_tax_totals()

        total_amount = sum(flt(r.amount) for r in (self.expenses or []))
        total_tax_amount = sum(flt(r.tax_amount) for r in (self.expense_taxes_and_charges or []))
        grand_total = total_amount + total_tax_amount

        if grand_total <= 0:
            return []

        gl_entries = []

        # 1) Debit: expense lines
        for i, row in enumerate(self.expenses or [], start=1):
            amt = flt(row.amount)
            if amt <= 0:
                continue

            expense_acc = self._resolve_expense_account(row, dims, i)

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
                    "credit": flt(grand_total),
                    "debit_in_account_currency": 0,
                    "credit_in_account_currency": flt(grand_total),
                    "posting_date": self.posting_date,
                    "company": self.company,
                    "remarks": self.remarks,
                }
            )
        )

        # 2.1) Debit: tax lines
        for row in self.expense_taxes_and_charges or []:
            tax_amount = flt(getattr(row, "tax_amount", 0))
            if not tax_amount:
                continue

            tax_gl = self.get_gl_dict(
                {
                    "account": row.account_head,
                    "debit": tax_amount if tax_amount > 0 else 0,
                    "credit": abs(tax_amount) if tax_amount < 0 else 0,
                    "debit_in_account_currency": tax_amount if tax_amount > 0 else 0,
                    "credit_in_account_currency": abs(tax_amount) if tax_amount < 0 else 0,
                    "cost_center": getattr(row, "cost_center", None),
                    "project": getattr(row, "project", None),
                    "posting_date": self.posting_date,
                    "company": self.company,
                    "remarks": self.remarks,
                },
                item=row,
            )

            for d in dims:
                if hasattr(row, d) and getattr(row, d):
                    tax_gl[d] = getattr(row, d)

            gl_entries.append(tax_gl)

        # 2.2) Bank charge
        bank_charge_amount = flt(self.bank_charge_amount)
        if cint(self.apply_bank_charge or 0) and bank_charge_amount > 0:
            gl_entries.append(
                self.get_gl_dict(
                    {
                        "account": self.bank_charge_account,
                        "debit": bank_charge_amount,
                        "credit": 0,
                        "debit_in_account_currency": bank_charge_amount,
                        "credit_in_account_currency": 0,
                        "cost_center": getattr(self, "cost_center", None),
                        "project": getattr(self, "project", None),
                        "posting_date": self.posting_date,
                        "company": self.company,
                        "remarks": self.remarks,
                    }
                )
            )

            gl_entries.append(
                self.get_gl_dict(
                    {
                        "account": self.paid_from_account,
                        "debit": 0,
                        "credit": bank_charge_amount,
                        "debit_in_account_currency": 0,
                        "credit_in_account_currency": bank_charge_amount,
                        "posting_date": self.posting_date,
                        "company": self.company,
                        "remarks": self.remarks,
                    }
                )
            )

        self._append_inter_account_gl_entries(gl_entries, grand_total, dims)

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
        Sum grand_total from all submitted Expense Payment docs for same reference.
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

            total += flt(ep.get("grand_total") or ep.get("total_amount") or 0)

        return flt(total)


@frappe.whitelist()
def preview_gl_entries(doc):
    d = frappe.get_doc(frappe.parse_json(doc))
    d.set_missing_values()
    d.run_method("validate")
    return d.build_gl_preview()
