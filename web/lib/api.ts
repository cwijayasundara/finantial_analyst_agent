const BASE =
  typeof window === "undefined"
    ? `http://${process.env.PFH_API_HOST ?? "127.0.0.1"}:${process.env.PFH_API_PORT ?? "8000"}`
    : "";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`GET ${path} → ${res.status} ${body.slice(0, 120)}`);
  }
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`POST ${path} → ${res.status} ${text.slice(0, 120)}`);
  }
  return res.json();
}

export const api = {
  health: () => get<{ status: string; host: string; version: string }>("/api/health"),
  memos: {
    list: () => get<MemoSummary[]>("/api/memos"),
    get:  (period: string) => get<WikiPage>(`/api/memos/${period}`),
  },
  merchants: {
    list: (params?: { category?: string; q?: string; limit?: number }) => {
      const qs = new URLSearchParams();
      if (params?.category) qs.set("category", params.category);
      if (params?.q) qs.set("q", params.q);
      if (params?.limit) qs.set("limit", String(params.limit));
      const tail = qs.toString() ? `?${qs}` : "";
      return get<MerchantRow[]>(`/api/merchants${tail}`);
    },
    get: (id: string) => get<MerchantDetail>(`/api/merchants/${id}`),
  },
  statements: {
    list: (account?: string) => get<StatementRow[]>(
      `/api/statements${account ? `?account=${account}` : ""}`,
    ),
    get: (id: string) => get<WikiPage>(`/api/statements/${id}`),
  },
  recommendations: {
    list: (status?: string) => get<RecommendationRow[]>(
      `/api/recommendations${status ? `?status=${status}` : ""}`,
    ),
    get: (id: string) => get<WikiPage>(`/api/recommendations/${id}`),
    accept:  (id: string, body: { actor?: string; reason?: string }) =>
      post<{ ok: boolean; status: string }>(`/api/recommendations/${id}/accept`, body),
    dismiss: (id: string, body: { actor?: string; reason?: string }) =>
      post<{ ok: boolean; status: string }>(`/api/recommendations/${id}/dismiss`, body),
  },
  budgets: {
    list:     (period?: string) => get<BudgetRow[]>(`/api/budgets${period ? `?period=${period}` : ""}`),
    variance: (period: string) => get<BudgetVariance[]>(`/api/budgets/variance/${period}`),
    create:   (b: CreateBudget) => post<{ ok: boolean; page_id: string }>("/api/budgets", b),
  },
  decisions: {
    get: (id: string) => get<DecisionDetail>(`/api/decisions/${id}`),
  },
  graph: {
    snapshot: (params?: { type?: string; limit?: number }) => {
      const qs = new URLSearchParams();
      if (params?.type) qs.set("type", params.type);
      if (params?.limit) qs.set("limit", String(params.limit));
      const tail = qs.toString() ? `?${qs}` : "";
      return get<GraphSnapshot>(`/api/graph/snapshot${tail}`);
    },
  },
  qa: {
    askSync: (question: string) =>
      post<{ answer: string; tool_calls: Array<{ name: string; args: unknown }>; refused: string[]; iterations: number }>(
        "/api/qa/ask-sync",
        { question, allow_writes: false },
      ),
  },
  // P7
  goals: {
    list:     (status?: string) => get<GoalRow[]>(`/api/goals${status ? `?status=${status}` : ""}`),
    get:      (id: string) => get<GoalRow>(`/api/goals/${id}`),
    progress: (period: string) => get<GoalProgress[]>(`/api/goals/progress/${period}`),
    create:   (g: CreateGoal) => post<{ ok: boolean; page_id: string }>("/api/goals", g),
    setStatus: (id: string, status: string) =>
      post<{ ok: boolean; status: string }>(`/api/goals/${id}/status`, { status }),
  },
  networth: {
    list:    () => get<NetWorthRow[]>("/api/networth"),
    get:     (period: string) => get<NetWorthDetail>(`/api/networth/${period}`),
    snapshot: (period: string) =>
      post<{ ok: boolean; page_id: string; total_amount: string; by_account: Record<string, string> }>(
        "/api/networth", { period },
      ),
  },
  // P8
  forecast: {
    listCategories: (params: { period: string; horizon?: number; lookback?: number; top_n?: number }) => {
      const qs = new URLSearchParams({ period: params.period });
      if (params.horizon)  qs.set("horizon", String(params.horizon));
      if (params.lookback) qs.set("lookback", String(params.lookback));
      if (params.top_n)    qs.set("top_n", String(params.top_n));
      return get<CategoryForecast[]>(`/api/forecast/categories?${qs}`);
    },
    getCategory: (category: string, params: { period: string; horizon?: number; lookback?: number }) => {
      const qs = new URLSearchParams({ period: params.period });
      if (params.horizon)  qs.set("horizon", String(params.horizon));
      if (params.lookback) qs.set("lookback", String(params.lookback));
      return get<CategoryForecast>(`/api/forecast/categories/${category}?${qs}`);
    },
  },
};

export type MemoSummary = {
  page_id: string; period: string; updated: string;
  citations_count: number; confidence?: number;
};
export type WikiPage = {
  id: string; type: string;
  frontmatter: Record<string, unknown>;
  body: string; path?: string;
};
export type MerchantRow = {
  id: string; canonical_name: string;
  category: string | null; txn_count: number;
};
export type MerchantDetail = WikiPage & {
  recent_transactions: Array<{
    id: string; date: string; amount: string;
    raw_description: string; statement_id: string;
  }>;
};
export type StatementRow = {
  id: string; account_id: string;
  period_start: string; period_end: string; txn_count: number;
};
export type RecommendationRow = {
  id: string; period: string; kind: string;
  status: string; confidence?: number; updated: string;
  cites: string[];
};
export type BudgetRow = {
  id: string; period: string; scope_type: string;
  scope_id: string; target_amount: string; notes: string;
};
export type BudgetVariance = {
  budget_id: string; period: string;
  scope_type: string; scope_id: string;
  target: string; actual: string; delta: string;
  pct: number; flag: "over" | "under" | "on_track";
};
export type CreateBudget = {
  period: string; scope_type: string; scope_id: string;
  target_amount: number; notes?: string;
};
export type DecisionDetail = {
  page: WikiPage;
  replay: {
    decision_id: string; ts: string; actor: string; action_id: string;
    live_pages_at_ts: number; prior_decisions_count: number;
    wiki_fingerprint_drift: boolean; ontology_fingerprint_drift: boolean;
  };
};
export type GraphSnapshot = {
  nodes: Array<{ kind: "node"; type: string; id: string; [k: string]: unknown }>;
  edges: Array<{ kind: "edge"; type: string; from: string; to: string }>;
  node_count: number;
  edge_count: number;
};

// --- P7 types ---
export type GoalRow = {
  id: string; name: string; target_amount: string;
  target_date: string; scope_type: string; scope_id: string;
  status: string; started_at: string | null;
  completed_at: string | null; notes: string;
};
export type GoalProgress = {
  goal_id: string; name: string;
  scope_type: string; scope_id: string;
  target_amount: string; target_date: string;
  started_at: string | null;
  current_amount: string; pct_complete: number;
  months_total: number; months_elapsed: number;
  monthly_required: string;
  on_track: boolean;
  status: "on_track" | "behind" | "ahead" | "achieved" | "missed";
};
export type CreateGoal = {
  name: string; target_amount: number; target_date: string;
  scope_type: string; scope_id: string;
  started_at?: string | null; notes?: string;
  status?: string; actor?: string;
};
export type NetWorthRow = {
  period: string; total_amount: string;
  by_account: Record<string, number>;
  computed_at: string; notes: string;
};
export type NetWorthDetail = NetWorthRow & {
  delta: {
    prev_period: string | null;
    prev_total: string | null;
    delta: string | null;
    pct_change: number | null;
  };
};

// --- P8 types ---
export type CategoryForecast = {
  category: string;
  history_periods: string[];
  history: string[];           // Decimals serialised as strings
  forecast_periods: string[];
  forecast: string[];
  method: "seasonal_naive" | "holt_smoothing" | "linear_projection" | "mean";
  rmse: string;
  monthly_average: string;
};
