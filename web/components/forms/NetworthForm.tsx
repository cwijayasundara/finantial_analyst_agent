"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Field, FormShell } from "./FormShell";

const defaultPeriod = (() => {
  const d = new Date();
  return `${d.getFullYear()}_${String(d.getMonth() + 1).padStart(2, "0")}`;
})();

export function NetworthForm() {
  const [period, setPeriod] = useState(defaultPeriod);

  return (
    <FormShell
      title="Take a net-worth snapshot"
      description="Aggregates current account balances into a single snapshot row for the period."
      onSubmit={() => api.networth.snapshot(period)}
      formatResult={(r) => `snapshot ${r.page_id} — total £${r.total_amount}`}
    >
      <Field name="period" label="period" value={period} onChange={setPeriod}
             placeholder="yyyy_mm" required help="month to snapshot, e.g. 2025_04" />
    </FormShell>
  );
}
