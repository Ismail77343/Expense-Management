import frappe
from frappe.utils import flt

def sync_payment_request_status(doc, method=None):
    """
    When Payment Request is submitted/cancelled/updated after submit:
    - Calculate total paid from submitted Expense Payment docs linked to same reference
    - Update Payment Request status to Paid / Partially Paid / Requested (fallback)
    """

    # لازم يكون عنده مرجع (Cash Requests / Full & Final ..)
    ref_dt = doc.get("reference_doctype")
    ref_dn = doc.get("reference_name")
    if not ref_dt or not ref_dn:
        return

    required = flt(doc.get("requested_amount") or doc.get("grand_total") or doc.get("amount") or 0)
    if required <= 0:
        return

    total_paid = _sum_paid_for_reference(ref_dt, ref_dn)

    eps = 0.01
    if total_paid + eps >= required:
        new_status = "Paid"
    elif total_paid > eps:
        new_status = "Partially Paid"
    else:
        # رجّعها لحالة مناسبة حسب ستايلات ERPNext
        # إذا docstatus=1 عادة تكون Requested/Initiated
        new_status = "Initiated" if doc.docstatus == 1 else "Draft"

    # تحديث status فقط إذا اختلف
    if doc.get("status") != new_status:
        frappe.db.set_value("Payment Request", doc.name, "status", new_status, update_modified=True)

    # اختياري: خزّن المدفوع في حقل مخصص لو موجود
    if frappe.db.has_column("Payment Request", "paid_amount"):
        frappe.db.set_value("Payment Request", doc.name, "paid_amount", total_paid, update_modified=False)


def _sum_paid_for_reference(ref_dt, ref_dn):
    """
    Sum total paid from all submitted Expense Payment that have the same reference.
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
        if ep.get("total_amount") is not None:
            total += flt(ep.get("total_amount"))
        else:
            total += sum(flt(r.get("amount")) for r in (ep.get("expenses") or []))

    return flt(total)
