import frappe

@frappe.whitelist()
def get_expense_account_from_type(expense_type: str, company: str | None = None):
    if not expense_type:
        return {"account": None, "reason": "missing_expense_type"}

    # جلب Expense Claim Type مع جدول accounts
    ect = frappe.get_doc("Expense Claim Type", expense_type)

    # 1) لو عنده جدول accounts اختَر الحساب حسب الشركة
    # أغلب نسخ HRMS: row.company + row.default_account (أو row.account)
    if hasattr(ect, "accounts") and ect.accounts:
        # حاول تطابق الشركة أولاً
        if company:
            for r in ect.accounts:
                row_company = getattr(r, "company", None)
                if row_company == company:
                    acc = getattr(r, "default_account", None) or getattr(r, "account", None)
                    if acc:
                        return {"account": acc, "source": "accounts(company_match)"}

        # لو ما لقينا شركة، خذ أول حساب متوفر
        for r in ect.accounts:
            acc = getattr(r, "default_account", None) or getattr(r, "account", None)
            if acc:
                return {"account": acc, "source": "accounts(first)"}

        return {"account": None, "reason": "accounts_empty"}

    # 2) fallback (لو يستخدم deferred_expense_account)
    acc = getattr(ect, "deferred_expense_account", None)
    if acc:
        return {"account": acc, "source": "deferred_expense_account"}

    return {"account": None, "reason": "no_mapping"}
