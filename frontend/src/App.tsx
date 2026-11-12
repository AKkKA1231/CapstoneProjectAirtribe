import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type Usage = {
  key_id: string;
  key_prefix: string;
  tenant_name: string;
  request_count: number;
  total_tokens: number;
  cost_usd: string;
  cache_hits: number;
  fallbacks: number;
};

type CacheStats = {
  key_id: string;
  tenant_name: string;
  entry_count: number;
  active_entries: number;
  lookup_hits: number;
  request_count: number;
  request_cache_hits: number;
};

type RequestItem = {
  id: string;
  tenant_name: string;
  request_alias: string;
  resolved_alias: string | null;
  provider: string | null;
  status: string;
  total_tokens: number | null;
  cost_usd: string | null;
  cache_hit: boolean;
  fallback_triggered: boolean;
  routing_reason: string | null;
  streamed: boolean;
  provider_latency_ms: number | null;
  created_at: string;
};

type Attempt = {
  id: string;
  tenant_name: string;
  provider: string;
  model: string;
  attempt_number: number;
  retry_number: number;
  outcome: string;
  latency_ms: number | null;
  http_status: number | null;
  error_code: string | null;
  started_at: string;
};

type Page<T> = { items: T[]; total: number; limit: number; offset: number };
type Tab = "overview" | "requests" | "providers";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

function money(value: string | null): string {
  return `$${Number(value ?? 0).toFixed(6)}`;
}

function rate(hits: number, total: number): string {
  return total ? `${((hits / total) * 100).toFixed(1)}%` : "0.0%";
}

function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

function Empty({ message }: { message: string }) {
  return <div className="empty">{message}</div>;
}

export default function App() {
  const [adminKey, setAdminKey] = useState("");
  const [connected, setConnected] = useState(false);
  const [tab, setTab] = useState<Tab>("overview");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [usage, setUsage] = useState<Usage[]>([]);
  const [cache, setCache] = useState<CacheStats[]>([]);
  const [requests, setRequests] = useState<Page<RequestItem> | null>(null);
  const [attempts, setAttempts] = useState<Page<Attempt> | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [cacheFilter, setCacheFilter] = useState("");
  const [fallbackFilter, setFallbackFilter] = useState("");

  const headers = useMemo(
    () => ({ Authorization: `Bearer ${adminKey}`, "Content-Type": "application/json" }),
    [adminKey],
  );

  const get = useCallback(
    async <T,>(path: string): Promise<T> => {
      const response = await fetch(`${API_BASE}${path}`, { headers });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.error?.message ?? `Request failed (${response.status})`);
      }
      return response.json() as Promise<T>;
    },
    [headers],
  );

  const load = useCallback(async () => {
    if (!adminKey) return;
    setLoading(true);
    setError("");
    const filters = new URLSearchParams({ limit: "50" });
    if (statusFilter) filters.set("status", statusFilter);
    if (cacheFilter) filters.set("cache_hit", cacheFilter);
    if (fallbackFilter) filters.set("fallback", fallbackFilter);
    try {
      const [usageData, cacheData, requestData, attemptData] = await Promise.all([
        get<Usage[]>("/ops/usage"),
        get<CacheStats[]>("/ops/cache"),
        get<Page<RequestItem>>(`/ops/requests?${filters}`),
        get<Page<Attempt>>(`/ops/provider-attempts?${filters}`),
      ]);
      setUsage(usageData);
      setCache(cacheData);
      setRequests(requestData);
      setAttempts(attemptData);
      setConnected(true);
    } catch (caught) {
      setConnected(false);
      setError(caught instanceof Error ? caught.message : "Unable to load operations data");
    } finally {
      setLoading(false);
    }
  }, [adminKey, cacheFilter, fallbackFilter, get, statusFilter]);

  useEffect(() => {
    if (connected) void load();
  }, [statusFilter, cacheFilter, fallbackFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  function connect(event: FormEvent) {
    event.preventDefault();
    void load();
  }

  const totals = usage.reduce(
    (sum, row) => ({
      requests: sum.requests + row.request_count,
      tokens: sum.tokens + row.total_tokens,
      cost: sum.cost + Number(row.cost_usd),
      hits: sum.hits + row.cache_hits,
      fallbacks: sum.fallbacks + row.fallbacks,
    }),
    { requests: 0, tokens: 0, cost: 0, hits: 0, fallbacks: 0 },
  );

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">P</span><div><strong>Prism</strong><small>Gateway operations</small></div></div>
        <nav aria-label="Operations sections">
          {(["overview", "requests", "providers"] as Tab[]).map((item) => (
            <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>
              <span>{item === "overview" ? "◫" : item === "requests" ? "≡" : "↗"}</span>{item}
            </button>
          ))}
        </nav>
        <div className="sidebar-note"><span className={connected ? "status-dot live" : "status-dot"} />{connected ? "Connected" : "Not connected"}</div>
      </aside>

      <main>
        <header>
          <div><p className="eyebrow">CONTROL PLANE</p><h1>{tab[0].toUpperCase() + tab.slice(1)}</h1></div>
          <form className="auth" onSubmit={connect}>
            <label htmlFor="admin-key">Admin key</label>
            <input id="admin-key" type="password" value={adminKey} onChange={(event) => setAdminKey(event.target.value)} placeholder="Enter admin bearer key" autoComplete="off" />
            <button type="submit" disabled={!adminKey || loading}>{loading ? "Loading…" : connected ? "Refresh" : "Connect"}</button>
          </form>
        </header>

        {error && <div className="error" role="alert"><strong>Couldn’t load data.</strong> {error}</div>}
        {!connected && !loading && !error && <section className="welcome"><p className="eyebrow">SECURE ADMIN VIEW</p><h2>See how every request moves through Prism.</h2><p>Enter the configured admin bearer key to inspect spend, routing, cache behavior, and provider reliability. The key remains only in this page’s memory.</p></section>}

        {connected && tab === "overview" && <>
          <section className="cards">
            <article><span>Requests</span><strong>{totals.requests.toLocaleString()}</strong><small>All recorded traffic</small></article>
            <article><span>Token volume</span><strong>{totals.tokens.toLocaleString()}</strong><small>Prompt + completion</small></article>
            <article><span>Spend</span><strong>{money(String(totals.cost))}</strong><small>Settled provider cost</small></article>
            <article><span>Cache hit rate</span><strong>{rate(totals.hits, totals.requests)}</strong><small>{totals.hits} zero-cost hits</small></article>
          </section>
          <section className="panel"><div className="panel-heading"><div><p className="eyebrow">TENANTS</p><h2>Usage and spend</h2></div><Badge tone="green">{usage.length} active views</Badge></div>
            {usage.length === 0 ? <Empty message="No usage has been recorded yet." /> : <div className="table-wrap"><table><thead><tr><th>Tenant</th><th>Key prefix</th><th>Requests</th><th>Tokens</th><th>Cache hits</th><th>Fallbacks</th><th>Spend</th></tr></thead><tbody>{usage.map((row) => <tr key={row.key_id}><td><strong>{row.tenant_name}</strong></td><td><code>{row.key_prefix}</code></td><td>{row.request_count}</td><td>{row.total_tokens.toLocaleString()}</td><td>{row.cache_hits}</td><td>{row.fallbacks}</td><td>{money(row.cost_usd)}</td></tr>)}</tbody></table></div>}
          </section>
          <section className="panel"><div className="panel-heading"><div><p className="eyebrow">SEMANTIC CACHE</p><h2>Isolation and efficiency</h2></div></div>
            {cache.length === 0 ? <Empty message="No cache statistics are available." /> : <div className="cache-grid">{cache.map((row) => <article key={row.key_id}><div><strong>{row.tenant_name}</strong><span>{row.active_entries} active / {row.entry_count} total entries</span></div><div className="ring" style={{"--value": `${row.request_count ? (row.request_cache_hits / row.request_count) * 360 : 0}deg`} as React.CSSProperties}><span>{rate(row.request_cache_hits, row.request_count)}</span></div><small>{row.lookup_hits} vector lookup hits</small></article>)}</div>}
          </section>
        </>}

        {connected && tab === "requests" && <section className="panel"><div className="panel-heading"><div><p className="eyebrow">AUDIT TRAIL</p><h2>Recent requests</h2></div><div className="filters"><select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="">All statuses</option><option>success</option><option>error</option><option>partial_stream_error</option><option>cancelled</option></select><select value={cacheFilter} onChange={(e) => setCacheFilter(e.target.value)}><option value="">Cache: all</option><option value="true">Hits</option><option value="false">Misses</option></select><select value={fallbackFilter} onChange={(e) => setFallbackFilter(e.target.value)}><option value="">Fallback: all</option><option value="true">Triggered</option><option value="false">Not triggered</option></select></div></div>
          {!requests?.items.length ? <Empty message="No requests match these filters." /> : <div className="table-wrap"><table><thead><tr><th>Time</th><th>Tenant</th><th>Route</th><th>Provider</th><th>Status</th><th>Signals</th><th>Latency</th><th>Cost</th></tr></thead><tbody>{requests.items.map((row) => <tr key={row.id}><td>{new Date(row.created_at).toLocaleTimeString()}</td><td><strong>{row.tenant_name}</strong></td><td>{row.request_alias} → {row.resolved_alias ?? "—"}<small className="cell-note">{row.routing_reason}</small></td><td>{row.provider ?? "—"}</td><td><Badge tone={row.status === "success" ? "green" : "red"}>{row.status}</Badge></td><td><div className="badge-row">{row.cache_hit && <Badge tone="blue">cache</Badge>}{row.fallback_triggered && <Badge tone="amber">fallback</Badge>}{row.streamed && <Badge>stream</Badge>}</div></td><td>{row.provider_latency_ms == null ? "—" : `${row.provider_latency_ms} ms`}</td><td>{money(row.cost_usd)}</td></tr>)}</tbody></table></div>}
        </section>}

        {connected && tab === "providers" && <section className="panel"><div className="panel-heading"><div><p className="eyebrow">UPSTREAMS</p><h2>Provider attempts</h2></div><Badge tone={totals.fallbacks ? "amber" : "green"}>{totals.fallbacks} fallback events</Badge></div>
          {!attempts?.items.length ? <Empty message="No provider attempts match these filters." /> : <div className="table-wrap"><table><thead><tr><th>Time</th><th>Tenant</th><th>Provider</th><th>Model</th><th>Sequence</th><th>Outcome</th><th>HTTP</th><th>Latency</th></tr></thead><tbody>{attempts.items.map((row) => <tr key={row.id}><td>{new Date(row.started_at).toLocaleTimeString()}</td><td>{row.tenant_name}</td><td><strong>{row.provider}</strong></td><td><code>{row.model}</code></td><td>#{row.attempt_number} · retry {row.retry_number}</td><td><Badge tone={row.outcome === "success" ? "green" : "red"}>{row.outcome}</Badge>{row.error_code && <small className="cell-note">{row.error_code}</small>}</td><td>{row.http_status ?? "—"}</td><td>{row.latency_ms == null ? "—" : `${row.latency_ms} ms`}</td></tr>)}</tbody></table></div>}
        </section>}
      </main>
    </div>
  );
}
