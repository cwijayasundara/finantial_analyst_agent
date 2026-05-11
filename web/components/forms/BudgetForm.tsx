"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Field, FormShell, Select } from "./FormShell";

const defaultPeriod = (() => {
  const d = new Date();
  return `${d.getFullYear()}_${String(d.getMonth() + 1).padStart(2, "0")}`;
})();

export function BudgetForm() {
  const [period,     setPeriod]     = useState(defaultPeriod);
  const [scopeType,  setScopeType]  = useState("category");
  const [scopeId,    setScopeId]    = useState("groceries");
  const [target,     setTarget]     = useState("200");
  const [notes,      setNotes]      = useState("");

  return (
    <FormShell
      title="Create a budget"
      description="Sets a target amount for a scope and period. Variance shows up on the analyst memo + the Budgets page."
      onSubmit={() => api.budgets.create({
        period, scope_type: scopeType, scope_id: scopeId,
        target_amount: Number(target), notes,
      })}
      formatResult={(r) => `saved ${r.page_id}`}
    >
      <div className="grid grid-cols-2 gap-3">
        <Field name="period"  label="period"  value={period}    onChange={setPeriod}   placeholder="yyyy_mm" required />
        <Field name="target"  label="target £" value={target}   onChange={setTarget}  type="number" required />
        <Select
          name="scope_type" label="scope type" value={scopeType} onChange={setScopeType}
          options={[
            { value: "category", label: "category" },
            { value: "merchant", label: "merchant" },
            { value: "account",  label: "account"  },
          ]}
        />
        <Field name="scope_id" label="scope id" value={scopeId} onChange={setScopeId}
               help="category name (e.g. groceries), merchant id, or account id" required />
      </div>
      <Field name="notes" label="notes (optional)" value={notes} onChange={setNotes} />
    </FormShell>
  );
}
