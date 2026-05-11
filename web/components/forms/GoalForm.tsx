"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Field, FormShell, Select } from "./FormShell";

export function GoalForm() {
  const [name,       setName]       = useState("");
  const [target,     setTarget]     = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [scopeType,  setScopeType]  = useState("savings_account");
  const [scopeId,    setScopeId]    = useState("");
  const [notes,      setNotes]      = useState("");

  return (
    <FormShell
      title="Create a goal"
      description="Tracks progress toward a target by a target date. Progress is computed from the scope you select."
      onSubmit={() => api.goals.create({
        name, target_amount: Number(target), target_date: targetDate,
        scope_type: scopeType, scope_id: scopeId, notes,
      })}
      formatResult={(r) => `saved ${r.page_id}`}
    >
      <div className="grid grid-cols-2 gap-3">
        <Field name="name"        label="name"         value={name}        onChange={setName}      required placeholder="Emergency fund" />
        <Field name="target"      label="target £"     value={target}      onChange={setTarget}    required type="number" />
        <Field name="target_date" label="target date"  value={targetDate}  onChange={setTargetDate} required type="date" />
        <Select
          name="scope_type" label="scope type" value={scopeType} onChange={setScopeType}
          options={[
            { value: "savings_account",     label: "savings account"      },
            { value: "debt_payoff",         label: "debt payoff"          },
            { value: "category_underspend", label: "category underspend"  },
            { value: "custom",              label: "custom"               },
          ]}
        />
        <Field name="scope_id" label="scope id" value={scopeId} onChange={setScopeId}
               help="account id, debt id, or category name depending on scope type" required />
      </div>
      <Field name="notes" label="notes (optional)" value={notes} onChange={setNotes} />
    </FormShell>
  );
}
