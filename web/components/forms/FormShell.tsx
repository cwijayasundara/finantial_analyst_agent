"use client";

import { ReactNode, useState } from "react";

type FormShellProps<T> = {
  title: string;
  description?: string;
  submitLabel?: string;
  onSubmit: () => Promise<T>;
  formatResult?: (result: T) => string;
  children: ReactNode;
};

// Small wrapper around the create-form pattern: handles loading state,
// error display, and a success line. Keeps per-form components small.
export function FormShell<T>({
  title, description, submitLabel = "Save",
  onSubmit, formatResult, children,
}: FormShellProps<T>) {
  const [busy,    setBusy]    = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function handle(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null); setSuccess(null);
    try {
      const result = await onSubmit();
      setSuccess(formatResult ? formatResult(result) : "saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="border border-black/10 dark:border-white/10 rounded p-4 space-y-3">
      <header>
        <h2 className="text-lg font-semibold">{title}</h2>
        {description && <p className="text-sm opacity-70">{description}</p>}
      </header>
      <form onSubmit={handle} className="space-y-3">
        {children}
        <div className="flex items-center gap-3">
          <button
            type="submit" disabled={busy}
            className="px-3 py-1.5 border border-black/10 dark:border-white/10 rounded text-sm hover:bg-black/5 dark:hover:bg-white/5 disabled:opacity-50"
          >
            {busy ? "saving…" : submitLabel}
          </button>
          {error   && <span className="text-sm text-red-600 dark:text-red-400">{error}</span>}
          {success && <span className="text-sm text-green-700 dark:text-green-400">{success}</span>}
        </div>
      </form>
    </section>
  );
}

export function Field({
  name, label, type = "text", required = false, defaultValue, placeholder, help,
  value, onChange,
}: {
  name: string; label: string; type?: string; required?: boolean;
  defaultValue?: string; placeholder?: string; help?: string;
  value?: string; onChange?: (v: string) => void;
}) {
  return (
    <label className="block">
      <span className="block text-xs uppercase opacity-60 mb-1">{label}</span>
      <input
        name={name} type={type} required={required}
        defaultValue={defaultValue} value={value} placeholder={placeholder}
        onChange={onChange ? (e) => onChange(e.target.value) : undefined}
        className="w-full border border-black/10 dark:border-white/10 rounded px-3 py-1.5 bg-transparent text-sm"
      />
      {help && <span className="block text-xs opacity-50 mt-1">{help}</span>}
    </label>
  );
}

export function Select({
  name, label, options, defaultValue, value, onChange,
}: {
  name: string; label: string; options: { value: string; label: string }[];
  defaultValue?: string; value?: string;
  onChange?: (v: string) => void;
}) {
  return (
    <label className="block">
      <span className="block text-xs uppercase opacity-60 mb-1">{label}</span>
      <select
        name={name} defaultValue={defaultValue} value={value}
        onChange={onChange ? (e) => onChange(e.target.value) : undefined}
        className="w-full border border-black/10 dark:border-white/10 rounded px-3 py-1.5 bg-transparent text-sm"
      >
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}
