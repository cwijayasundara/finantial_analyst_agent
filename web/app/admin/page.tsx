import Link from "next/link";

import { BudgetForm } from "@/components/forms/BudgetForm";
import { GoalForm } from "@/components/forms/GoalForm";
import { NetworthForm } from "@/components/forms/NetworthForm";

export default function AdminPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Admin</h1>
        <p className="text-sm opacity-70">
          Create budgets, goals, and net-worth snapshots. All writes go
          through the same audited action layer as the CLI — each save
          emits a Decision page and an entry in <code>graph/audit.jsonl</code>.
        </p>
        <p className="text-sm opacity-70 mt-2">
          See your records on{" "}
          <Link href="/budgets"  className="underline">Budgets</Link>,{" "}
          <Link href="/goals"    className="underline">Goals</Link>,{" "}
          <Link href="/networth" className="underline">Net Worth</Link>.
        </p>
      </header>

      <BudgetForm />
      <GoalForm />
      <NetworthForm />
    </div>
  );
}
