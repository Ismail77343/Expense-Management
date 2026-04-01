frappe.ui.form.on('Expense Payment', {
  setup(frm) {
    // Paid From: Bank / Cash فقط + نفس الشركة + Leaf
    frm.set_query('paid_from_account', function () {
      return {
        filters: {
          company: frm.doc.company,
          is_group: 0,
          account_type: ['in', ['Bank', 'Cash']]
        }
      };
    });
    frm.set_query('from_account', function () {
      return {
        filters: {
          company: frm.doc.company,
          is_group: 0,
          root_type: ['in', ['Asset']]
        }
      };
    });
    frm.set_query('to_account', function () {
      return {
        filters: {
          company: frm.doc.company,
          is_group: 0,
          root_type: ['in', ['Asset']]
        }
      };
    });

    // Cost Center داخل الجدول (اختياري)
    frm.set_query('cost_center', function () {
      return {
        filters: {
          company: frm.doc.company,
          is_group: 0
        }
      };
    });

    // Project داخل الجدول (اختياري)
    frm.set_query('project', function () {
      return {
        filters: {
          company: frm.doc.company
        }
      };
    });
    // Expense Account داخل الجدول: Expense فقط + نفس الشركة + Leaf
    frm.set_query('expense_account', 'expenses', function () {
      return {
        filters: {
          company: frm.doc.company,
          is_group: 0,
          root_type: 'Expense'
        }
      };
    });

    // Cost Center داخل الجدول (اختياري)
    frm.set_query('cost_center', 'expenses', function () {
      return {
        filters: {
          company: frm.doc.company,
          is_group: 0
        }
      };
    });

    // Project داخل الجدول (اختياري)
    frm.set_query('project', 'expenses', function () {
      return {
        filters: {
          company: frm.doc.company
        }
      };
    });

    frm.set_query('account_head', 'expense_taxes_and_charges', function () {
      return {
        filters: {
          company: frm.doc.company,
          is_group: 0
        }
      };
    });

    frm.set_query('cost_center', 'expense_taxes_and_charges', function () {
      return {
        filters: {
          company: frm.doc.company,
          is_group: 0
        }
      };
    });

    frm.set_query('project', 'expense_taxes_and_charges', function () {
      return {
        filters: {
          company: frm.doc.company
        }
      };
    });

    frm.set_query('bank_charge_account', function () {
      return {
        filters: {
          company: frm.doc.company,
          is_group: 0,
          root_type: 'Expense'
        }
      };
    });
  },

  refresh(frm) {
    frm.trigger('calc_total');

    // امسح الأزرار القديمة (عشان ما تتكرر)
    frm.clear_custom_buttons();

    // ✅ إذا Draft: اعرض Preview GL
    if (frm.doc.docstatus === 0) {
        frm.add_custom_button(__('Preview GL'), async () => {
        const r = await frappe.call({
            method: 'expense_management.expense_management.doctype.expense_payment.expense_payment.preview_gl_entries',
            args: { doc: JSON.stringify(frm.doc) }   // مهم
        });

        const rows = (r.message || []).map(d => ({
            account: d.account,
            debit: d.debit || 0,
            credit: d.credit || 0,
            cost_center: d.cost_center,
            project: d.project
        }));

        const dialog = new frappe.ui.Dialog({
            title: __('GL Preview'),
            size: 'large',
            fields: [
            {
                fieldtype: 'Table',
                fieldname: 'gl',
                label: 'GL Entries',
                cannot_add_rows: 1,
                in_place_edit: 0,
                data: rows,
                fields: [
                { fieldtype: 'Link', fieldname: 'account', label: 'Account', options: 'Account', in_list_view: 1, read_only: 1 },
                { fieldtype: 'Currency', fieldname: 'debit', label: 'Debit', in_list_view: 1, read_only: 1 },
                { fieldtype: 'Currency', fieldname: 'credit', label: 'Credit', in_list_view: 1, read_only: 1 },
                { fieldtype: 'Link', fieldname: 'cost_center', label: 'Cost Center', options: 'Cost Center', in_list_view: 1, read_only: 1 },
                { fieldtype: 'Link', fieldname: 'project', label: 'Project', options: 'Project', in_list_view: 1, read_only: 1 }
                ]
            }
            ]
        });

        dialog.show();
        }, __('View'));

        return;
    }

    // ✅ إذا Submitted: افتح General Ledger للمستند
    if (frm.doc.docstatus === 1) {
        frm.add_custom_button(__('Accounting Ledger'), () => {
        frappe.set_route('query-report', 'General Ledger', {
            company: frm.doc.company,
            voucher_no: frm.doc.name,
            from_date: frm.doc.posting_date,
            to_date: frm.doc.posting_date
        });
        }, __('View'));

       
    }
  },

  company(frm) {
    // عند تغيير الشركة: امسح الحسابات المرتبطة لتفادي حسابات لشركة ثانية
    frm.set_value('paid_from_account', null);
    if (!frm.doc.apply_bank_charge) {
      frm.set_value('bank_charge_account', null);
    } else {
      frm.set_value('bank_charge_account', null);
      set_default_bank_charge_account(frm);
    }

    (frm.doc.expenses || []).forEach(r => {
      r.expense_account = null;
      r.cost_center = null;
      r.project = null;
    });

    frm.refresh_field('expenses');
    frm.trigger('calc_total');
  },

  apply_bank_charge(frm) {
    if (!frm.doc.apply_bank_charge) {
      frm.set_value('bank_charge_amount', 0);
      frm.set_value('bank_charge_account', null);
      return;
    }

    set_default_bank_charge_account(frm);
  },

  calc_total(frm) {
    let total = 0;
    (frm.doc.expenses || []).forEach(r => {
      total += flt(r.amount);
    });

    let total_tax = 0;
    (frm.doc.expense_taxes_and_charges || []).forEach(r => {
      if (flt(r.rate)) {
        r.tax_amount = total * (flt(r.rate) / 100);
      }

      const tax_amount = flt(r.tax_amount);
      r.total = total + tax_amount;
      total_tax += tax_amount;
    });

    frm.set_value('total_amount', total);
    frm.set_value('total_tax_and_charges', total_tax);
    frm.set_value('grand_total', total + total_tax);
    frm.refresh_field('expense_taxes_and_charges');
  },

  project(frm) {
    // يطبق مشروع الأب على كل الصفوف
    (frm.doc.expenses || []).forEach(r => {
      r.project = frm.doc.project;
    });
    frm.refresh_field('expenses');
  },

  cost_center(frm) {
    // يطبق Cost Center الأب على كل الصفوف
    (frm.doc.expenses || []).forEach(r => {
      r.cost_center = frm.doc.cost_center;
    });
    frm.refresh_field('expenses');
  }
});

frappe.ui.form.on('Child Expense Payment', {
  
  amount(frm) {
    frm.trigger('calc_total');
  },
  
  expenses_add(frm) {
    frm.trigger('calc_total');
  },
  expenses_remove(frm) {
    frm.trigger('calc_total');
  },
  
    async expense_type(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (!row.expense_type) return;

    const r = await frappe.call({
        method: "expense_management.api.get_expense_account_from_type",
        args: {
        expense_type: row.expense_type,
        company: frm.doc.company
        }
    });

    const data = r.message || {};
    const acc = data.account;

    if (acc) {
        row.expense_account = acc;
        frm.refresh_field('expenses');
        return;
    }

    frappe.msgprint({
        title: __("No mapped account"),
        indicator: "orange",
        message: __("No account mapping found for this Expense Claim Type. Reason: {0}", [data.reason || "unknown"])
    });
    }
});

frappe.ui.form.on('Expense Taxes and Charges', {
  account_head(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (row.account_head && !row.description) {
      frappe.model.set_value(cdt, cdn, 'description', row.account_head.split(' - ').slice(0, -1).join(' - '));
    }
  },

  rate(frm) {
    frm.trigger('calc_total');
  },

  tax_amount(frm) {
    frm.trigger('calc_total');
  },

  expense_taxes_and_charges_add(frm) {
    frm.trigger('calc_total');
  },

  expense_taxes_and_charges_remove(frm) {
    frm.trigger('calc_total');
  }
});

async function set_default_bank_charge_account(frm) {
  if (!frm.doc.apply_bank_charge || !frm.doc.company || frm.doc.bank_charge_account) return;

  const { message } = await frappe.db.get_value('Company', frm.doc.company, 'default_charges_account');
  if (message && message.default_charges_account) {
    await frm.set_value('bank_charge_account', message.default_charges_account);
  }
}
