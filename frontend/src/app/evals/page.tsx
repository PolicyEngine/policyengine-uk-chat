"use client";

import type { CSSProperties, ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import {
  IconAlertCircle,
  IconChartLine,
  IconClock,
  IconGitCompare,
  IconRefresh,
  IconSearch,
  IconShieldLock,
} from "@tabler/icons-react";

import { getBackendEndpoint } from "@/utils/backend";

type EvalRunSummary = {
  run_id: string;
  mode: string;
  provider: string;
  model: string | null;
  git_sha: string | null;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  suites: string[];
  passed: number;
  failed: number;
  skipped: number;
  total_cases: number;
  run_label: string | null;
  metadata: Record<string, string>;
  slowest_case_id: string | null;
  slowest_case_duration_ms: number;
  p95_case_duration_ms: number;
};

type TimingEvent = {
  name: string;
  duration_ms: number;
  attributes: Record<string, unknown>;
};

type CaseResult = {
  id: string;
  suite: string;
  status: "passed" | "failed" | "skipped";
  score: number;
  errors: string[];
  details: Record<string, unknown>;
  duration_ms: number;
  timings: TimingEvent[];
};

type SuiteTimingSummary = {
  count: number;
  total_ms: number;
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
  max_ms: number;
};

type EvalReport = {
  run_id: string | null;
  mode: string;
  suites: string[];
  provider: string;
  model: string | null;
  git_sha: string | null;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  timing_summary: Record<string, SuiteTimingSummary>;
  metadata: Record<string, string>;
  results: CaseResult[];
};

type EvalRunDetail = {
  run_id: string;
  summary: EvalRunSummary;
  report: EvalReport;
};

type SuiteTimingDelta = {
  base: SuiteTimingSummary | null;
  head: SuiteTimingSummary | null;
  total_ms_delta: number;
  p95_ms_delta: number;
};

type CaseDelta = {
  suite: string;
  id: string;
  change: string;
  base_status: string | null;
  head_status: string | null;
  base_score: number | null;
  head_score: number | null;
  score_delta: number | null;
  base_duration_ms: number | null;
  head_duration_ms: number | null;
  duration_delta_ms: number | null;
};

type EvalComparison = {
  base: EvalRunSummary;
  head: EvalRunSummary;
  counts_delta: Record<string, number>;
  suite_timing_delta: Record<string, SuiteTimingDelta>;
  case_deltas: CaseDelta[];
};

const STATUS_COLORS: Record<string, string> = {
  passed: "#047857",
  failed: "#b91c1c",
  skipped: "#a16207",
};

function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "n/a";
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(2)}s`;
  return `${value.toFixed(1)}ms`;
}

function formatDelta(value: number | null | undefined, suffix = ""): string {
  if (value === null || value === undefined) return "n/a";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}${suffix}`;
}

function shortSha(value: string | null): string {
  return value ? value.slice(0, 8) : "unknown";
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function getRunLabel(run: EvalRunSummary): string {
  return run.run_label || shortSha(run.git_sha);
}

function statusPill(status: string) {
  const color = STATUS_COLORS[status] || "#4b5563";
  return {
    color,
    border: `1px solid ${color}33`,
    background: `${color}12`,
  };
}

async function evalRequest<T>(token: string, endpoint: string): Promise<T> {
  const url = new URL(getBackendEndpoint(endpoint), window.location.origin);
  const response = await fetch(url.toString(), {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = body.detail || body.details || body.error || "";
    } catch {}
    const error = new Error(`API error ${response.status}${detail ? `: ${detail}` : ""}`);
    error.name = response.status === 401 ? "UnauthorizedError" : "EvalRequestError";
    throw error;
  }
  return response.json();
}

export default function EvalDashboardPage() {
  const [token, setToken] = useState("");
  const [tokenInput, setTokenInput] = useState("");
  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [detail, setDetail] = useState<EvalRunDetail | null>(null);
  const [comparison, setComparison] = useState<EvalComparison | null>(null);
  const [baseId, setBaseId] = useState("");
  const [headId, setHeadId] = useState("");
  const [mode, setMode] = useState("");
  const [suite, setSuite] = useState("");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const stored = sessionStorage.getItem("evalDashboardToken");
    if (stored) {
      setTokenInput(stored);
      void activateToken(stored);
    }
  }, []);

  const selectedRunId = detail?.run_id || "";
  const suiteOptions = useMemo(() => {
    const suites = new Set<string>();
    runs.forEach((run) => run.suites.forEach((runSuite) => suites.add(runSuite)));
    return Array.from(suites).sort();
  }, [runs]);

  async function loadRuns(activeToken = token) {
    if (!activeToken) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (mode) params.set("mode", mode);
      if (suite) params.set("suite", suite);
      if (status) params.set("status", status);
      if (query.trim()) params.set("q", query.trim());
      params.set("limit", "100");
      const suffix = params.toString();
      const data = await evalRequest<EvalRunSummary[]>(
        activeToken,
        `eval-runs${suffix ? `?${suffix}` : ""}`,
      );
      setRuns(data);
      if (!baseId && data[1]) setBaseId(data[1].run_id);
      if (!headId && data[0]) setHeadId(data[0].run_id);
      if (!detail && data[0]) void loadDetail(data[0].run_id, activeToken);
    } catch (err) {
      if (err instanceof Error && err.name === "UnauthorizedError") {
        lockDashboard("That dashboard token is wrong.");
      } else {
        setError(err instanceof Error ? err.message : "Failed to load eval runs");
      }
    } finally {
      setLoading(false);
    }
  }

  async function loadDetail(runId: string, activeToken = token) {
    if (!activeToken || !runId) return;
    setError(null);
    try {
      const data = await evalRequest<EvalRunDetail>(
        activeToken,
        `eval-runs/${encodeURIComponent(runId)}`,
      );
      setDetail(data);
    } catch (err) {
      if (err instanceof Error && err.name === "UnauthorizedError") {
        lockDashboard("That dashboard token is wrong.");
      } else {
        setError(err instanceof Error ? err.message : "Failed to load eval run");
      }
    }
  }

  async function loadComparison(activeToken = token) {
    if (!activeToken || !baseId || !headId) return;
    setError(null);
    try {
      const params = new URLSearchParams({ base_id: baseId, head_id: headId });
      const data = await evalRequest<EvalComparison>(
        activeToken,
        `eval-runs/compare?${params.toString()}`,
      );
      setComparison(data);
    } catch (err) {
      if (err instanceof Error && err.name === "UnauthorizedError") {
        lockDashboard("That dashboard token is wrong.");
      } else {
        setError(err instanceof Error ? err.message : "Failed to compare eval runs");
      }
    }
  }

  function lockDashboard(message?: string) {
    sessionStorage.removeItem("evalDashboardToken");
    setToken("");
    setRuns([]);
    setDetail(null);
    setComparison(null);
    setBaseId("");
    setHeadId("");
    setError(null);
    setAuthError(message || null);
  }

  async function activateToken(candidate = tokenInput) {
    const trimmed = candidate.trim();
    if (!trimmed) return;
    setAuthSubmitting(true);
    setAuthError(null);
    try {
      const params = new URLSearchParams({ limit: "100" });
      const data = await evalRequest<EvalRunSummary[]>(
        trimmed,
        `eval-runs?${params.toString()}`,
      );
      sessionStorage.setItem("evalDashboardToken", trimmed);
      setToken(trimmed);
      setRuns(data);
      if (data[1]) setBaseId(data[1].run_id);
      if (data[0]) {
        setHeadId(data[0].run_id);
        await loadDetail(data[0].run_id, trimmed);
      }
    } catch (err) {
      sessionStorage.removeItem("evalDashboardToken");
      setToken("");
      setAuthError(
        err instanceof Error && err.name === "UnauthorizedError"
          ? "That dashboard token is wrong."
          : err instanceof Error
            ? err.message
            : "Could not open eval dashboard.",
      );
    } finally {
      setAuthSubmitting(false);
    }
  }

  if (!token) {
    return (
      <main style={styles.authShell}>
        <section style={styles.authPanel}>
          <div style={styles.authIcon}><IconShieldLock size={28} /></div>
          <h1 style={styles.authTitle}>Eval Runs</h1>
          <input
            value={tokenInput}
            onChange={(event) => setTokenInput(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") void activateToken(); }}
            placeholder="Internal eval dashboard token"
            type="password"
            style={styles.tokenInput}
          />
          {authError && (
            <div style={styles.authError}>
              <IconAlertCircle size={16} />
              {authError}
            </div>
          )}
          <button
            onClick={() => void activateToken()}
            style={styles.primaryButton}
            disabled={authSubmitting}
          >
            {authSubmitting ? "Checking..." : "Open dashboard"}
          </button>
        </section>
      </main>
    );
  }

  return (
    <main style={styles.shell}>
      <header style={styles.header}>
        <div>
          <h1 style={styles.title}>Eval Runs</h1>
          <div style={styles.subtitle}>Report-file browser for UK chat eval verification</div>
        </div>
        <div style={styles.headerActions}>
          <button onClick={() => void loadRuns()} style={styles.iconButton} title="Refresh runs">
            <IconRefresh size={16} />
            Refresh
          </button>
          <button
            onClick={() => {
              setTokenInput("");
              lockDashboard();
            }}
            style={styles.secondaryButton}
          >
            Lock
          </button>
        </div>
      </header>

      {error && (
        <div style={styles.error}>
          <IconAlertCircle size={16} />
          {error}
        </div>
      )}

      <section style={styles.toolbar}>
        <label style={styles.searchBox}>
          <IconSearch size={16} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search run, SHA, label"
            style={styles.searchInput}
          />
        </label>
        <select value={mode} onChange={(event) => setMode(event.target.value)} style={styles.select}>
          <option value="">All modes</option>
          <option value="offline">Offline</option>
          <option value="live">Live</option>
        </select>
        <select value={suite} onChange={(event) => setSuite(event.target.value)} style={styles.select}>
          <option value="">All suites</option>
          {suiteOptions.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
        <select value={status} onChange={(event) => setStatus(event.target.value)} style={styles.select}>
          <option value="">Any status</option>
          <option value="passed">Passed</option>
          <option value="failed">Failed</option>
          <option value="skipped">Skipped</option>
        </select>
        <button onClick={() => void loadRuns()} style={styles.primaryButton}>
          Apply
        </button>
      </section>

      <section style={styles.grid}>
        <div style={styles.panel}>
          <div style={styles.panelHeader}>
            <h2 style={styles.panelTitle}>Runs</h2>
            {loading && <span style={styles.muted}>Loading...</span>}
          </div>
          <div style={styles.tableWrap}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>Run</th>
                  <th style={styles.th}>Mode</th>
                  <th style={styles.th}>Cases</th>
                  <th style={styles.th}>Duration</th>
                  <th style={styles.th}>P95</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr
                    key={run.run_id}
                    onClick={() => void loadDetail(run.run_id)}
                    style={{
                      ...styles.tr,
                      ...(selectedRunId === run.run_id ? styles.selectedRow : {}),
                    }}
                  >
                    <td style={styles.td}>
                      <div style={styles.strong}>{getRunLabel(run)}</div>
                      <div style={styles.muted}>{formatDate(run.started_at)}</div>
                      <div style={styles.mono}>{shortSha(run.git_sha)}</div>
                    </td>
                    <td style={styles.td}>{run.mode}</td>
                    <td style={styles.td}>
                      <span style={{ ...styles.pill, ...statusPill("passed") }}>{run.passed}</span>{" "}
                      <span style={{ ...styles.pill, ...statusPill("failed") }}>{run.failed}</span>{" "}
                      <span style={{ ...styles.pill, ...statusPill("skipped") }}>{run.skipped}</span>
                    </td>
                    <td style={styles.td}>{formatMs(run.duration_ms)}</td>
                    <td style={styles.td}>{formatMs(run.p95_case_duration_ms)}</td>
                  </tr>
                ))}
                {!runs.length && (
                  <tr>
                    <td style={styles.emptyCell} colSpan={5}>No eval reports found.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div style={styles.panel}>
          <div style={styles.panelHeader}>
            <h2 style={styles.panelTitle}>Run Detail</h2>
            {detail && <span style={styles.mono}>{detail.run_id}</span>}
          </div>
          {detail ? (
            <>
              <div style={styles.metricGrid}>
                <Metric icon={<IconChartLine size={16} />} label="Passed" value={String(detail.summary.passed)} />
                <Metric icon={<IconAlertCircle size={16} />} label="Failed" value={String(detail.summary.failed)} />
                <Metric icon={<IconClock size={16} />} label="Duration" value={formatMs(detail.summary.duration_ms)} />
                <Metric icon={<IconClock size={16} />} label="Slowest" value={formatMs(detail.summary.slowest_case_duration_ms)} />
              </div>

              <h3 style={styles.sectionTitle}>Suite Timing</h3>
              <div style={styles.tableWrap}>
                <table style={styles.table}>
                  <thead>
                    <tr>
                      <th style={styles.th}>Suite</th>
                      <th style={styles.th}>Cases</th>
                      <th style={styles.th}>Total</th>
                      <th style={styles.th}>Avg</th>
                      <th style={styles.th}>P95</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(detail.report.timing_summary).map(([name, timing]) => (
                      <tr key={name} style={styles.tr}>
                        <td style={styles.td}>{name}</td>
                        <td style={styles.td}>{timing.count}</td>
                        <td style={styles.td}>{formatMs(timing.total_ms)}</td>
                        <td style={styles.td}>{formatMs(timing.avg_ms)}</td>
                        <td style={styles.td}>{formatMs(timing.p95_ms)}</td>
                      </tr>
                    ))}
                    {!Object.keys(detail.report.timing_summary).length && (
                      <tr><td style={styles.emptyCell} colSpan={5}>No timing summary in this report.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>

              <h3 style={styles.sectionTitle}>Cases</h3>
              <div style={styles.caseList}>
                {detail.report.results.map((result) => (
                  <details key={`${result.suite}:${result.id}`} style={styles.caseItem}>
                    <summary style={styles.caseSummary}>
                      <span style={{ ...styles.pill, ...statusPill(result.status) }}>{result.status}</span>
                      <span style={styles.caseName}>{result.suite} / {result.id}</span>
                      <span style={styles.caseDuration}>{formatMs(result.duration_ms)}</span>
                    </summary>
                    <div style={styles.caseBody}>
                      {result.errors.length > 0 && (
                        <pre style={styles.pre}>{result.errors.join("\n")}</pre>
                      )}
                      {result.timings.length > 0 && (
                        <table style={styles.table}>
                          <thead>
                            <tr>
                              <th style={styles.th}>Phase</th>
                              <th style={styles.th}>Duration</th>
                              <th style={styles.th}>Attributes</th>
                            </tr>
                          </thead>
                          <tbody>
                            {result.timings.map((timing, index) => (
                              <tr key={`${timing.name}-${index}`} style={styles.tr}>
                                <td style={styles.td}>{timing.name}</td>
                                <td style={styles.td}>{formatMs(timing.duration_ms)}</td>
                                <td style={styles.td}><code>{JSON.stringify(timing.attributes)}</code></td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  </details>
                ))}
              </div>
            </>
          ) : (
            <div style={styles.emptyState}>Select a run to inspect its cases and timing.</div>
          )}
        </div>
      </section>

      <section style={styles.panel}>
        <div style={styles.panelHeader}>
          <h2 style={styles.panelTitle}>Compare Runs</h2>
          <IconGitCompare size={18} />
        </div>
        <div style={styles.compareBar}>
          <select value={baseId} onChange={(event) => setBaseId(event.target.value)} style={styles.selectWide}>
            <option value="">Base run</option>
            {runs.map((run) => <option key={run.run_id} value={run.run_id}>{getRunLabel(run)} · {run.run_id}</option>)}
          </select>
          <select value={headId} onChange={(event) => setHeadId(event.target.value)} style={styles.selectWide}>
            <option value="">Head run</option>
            {runs.map((run) => <option key={run.run_id} value={run.run_id}>{getRunLabel(run)} · {run.run_id}</option>)}
          </select>
          <button onClick={() => void loadComparison()} style={styles.primaryButton}>Compare</button>
        </div>

        {comparison && (
          <>
            <div style={styles.metricGrid}>
              <Metric label="Passed Δ" value={formatDelta(comparison.counts_delta.passed)} />
              <Metric label="Failed Δ" value={formatDelta(comparison.counts_delta.failed)} />
              <Metric label="Skipped Δ" value={formatDelta(comparison.counts_delta.skipped)} />
              <Metric label="Cases Δ" value={formatDelta(comparison.counts_delta.total_cases)} />
            </div>
            <h3 style={styles.sectionTitle}>Suite Timing Delta</h3>
            <div style={styles.tableWrap}>
              <table style={styles.table}>
                <thead>
                  <tr>
                    <th style={styles.th}>Suite</th>
                    <th style={styles.th}>Total Δ</th>
                    <th style={styles.th}>P95 Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(comparison.suite_timing_delta).map(([name, delta]) => (
                    <tr key={name} style={styles.tr}>
                      <td style={styles.td}>{name}</td>
                      <td style={styles.td}>{formatDelta(delta.total_ms_delta, "ms")}</td>
                      <td style={styles.td}>{formatDelta(delta.p95_ms_delta, "ms")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h3 style={styles.sectionTitle}>Case Changes</h3>
            <div style={styles.tableWrap}>
              <table style={styles.table}>
                <thead>
                  <tr>
                    <th style={styles.th}>Case</th>
                    <th style={styles.th}>Change</th>
                    <th style={styles.th}>Status</th>
                    <th style={styles.th}>Score Δ</th>
                    <th style={styles.th}>Duration Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {comparison.case_deltas.slice(0, 200).map((delta) => (
                    <tr key={`${delta.suite}:${delta.id}`} style={styles.tr}>
                      <td style={styles.td}>
                        <div style={styles.strong}>{delta.id}</div>
                        <div style={styles.muted}>{delta.suite}</div>
                      </td>
                      <td style={styles.td}>{delta.change}</td>
                      <td style={styles.td}>{delta.base_status || "n/a"} → {delta.head_status || "n/a"}</td>
                      <td style={styles.td}>{delta.score_delta === null ? "n/a" : delta.score_delta.toFixed(2)}</td>
                      <td style={styles.td}>{formatDelta(delta.duration_delta_ms, "ms")}</td>
                    </tr>
                  ))}
                  {!comparison.case_deltas.length && (
                    <tr><td style={styles.emptyCell} colSpan={5}>No case deltas.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </main>
  );
}

function Metric({ icon, label, value }: { icon?: ReactNode; label: string; value: string }) {
  return (
    <div style={styles.metric}>
      <div style={styles.metricLabel}>{icon}{label}</div>
      <div style={styles.metricValue}>{value}</div>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  shell: {
    minHeight: "100vh",
    background: "var(--bg)",
    color: "var(--text)",
    padding: "24px",
  },
  authShell: {
    minHeight: "100vh",
    display: "grid",
    placeItems: "center",
    background: "var(--bg)",
    color: "var(--text)",
    padding: "24px",
  },
  authPanel: {
    width: "min(420px, 100%)",
    border: "1px solid var(--border)",
    borderRadius: "8px",
    padding: "24px",
    background: "var(--surface)",
    display: "grid",
    gap: "14px",
  },
  authIcon: {
    width: "44px",
    height: "44px",
    borderRadius: "8px",
    display: "grid",
    placeItems: "center",
    background: "var(--surface-2)",
    color: "var(--text)",
  },
  authTitle: { margin: 0, fontSize: "24px", letterSpacing: 0 },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: "16px",
    marginBottom: "20px",
  },
  headerActions: { display: "flex", gap: "8px", alignItems: "center" },
  title: { margin: 0, fontSize: "28px", letterSpacing: 0 },
  subtitle: { color: "var(--text-3)", fontSize: "14px", marginTop: "4px" },
  toolbar: {
    display: "flex",
    flexWrap: "wrap",
    gap: "10px",
    alignItems: "center",
    padding: "12px",
    border: "1px solid var(--border)",
    borderRadius: "8px",
    background: "var(--surface)",
    marginBottom: "16px",
  },
  searchBox: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    minWidth: "240px",
    flex: "1 1 260px",
    border: "1px solid var(--border)",
    borderRadius: "6px",
    padding: "0 10px",
    background: "var(--surface)",
  },
  searchInput: {
    height: "36px",
    border: "none",
    outline: "none",
    background: "transparent",
    color: "var(--text)",
    width: "100%",
    fontSize: "14px",
  },
  tokenInput: {
    height: "40px",
    border: "1px solid var(--border)",
    borderRadius: "6px",
    padding: "0 12px",
    background: "var(--surface)",
    color: "var(--text)",
    fontSize: "14px",
  },
  select: {
    height: "38px",
    border: "1px solid var(--border)",
    borderRadius: "6px",
    padding: "0 10px",
    background: "var(--surface)",
    color: "var(--text)",
    fontSize: "14px",
  },
  selectWide: {
    height: "38px",
    minWidth: "260px",
    flex: "1 1 260px",
    border: "1px solid var(--border)",
    borderRadius: "6px",
    padding: "0 10px",
    background: "var(--surface)",
    color: "var(--text)",
    fontSize: "14px",
  },
  primaryButton: {
    height: "38px",
    border: "none",
    borderRadius: "6px",
    padding: "0 14px",
    background: "var(--accent)",
    color: "var(--accent-fg)",
    cursor: "pointer",
    fontSize: "14px",
  },
  secondaryButton: {
    height: "38px",
    border: "1px solid var(--border)",
    borderRadius: "6px",
    padding: "0 14px",
    background: "var(--surface)",
    color: "var(--text)",
    cursor: "pointer",
    fontSize: "14px",
  },
  iconButton: {
    height: "38px",
    border: "1px solid var(--border)",
    borderRadius: "6px",
    padding: "0 12px",
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
    background: "var(--surface)",
    color: "var(--text)",
    cursor: "pointer",
    fontSize: "14px",
  },
  error: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    border: "1px solid #b91c1c33",
    background: "#b91c1c12",
    color: "#b91c1c",
    borderRadius: "8px",
    padding: "10px 12px",
    marginBottom: "16px",
    fontSize: "14px",
  },
  authError: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    border: "1px solid #b91c1c33",
    background: "#b91c1c12",
    color: "#b91c1c",
    borderRadius: "6px",
    padding: "9px 10px",
    fontSize: "13px",
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 420px), 1fr))",
    gap: "16px",
    alignItems: "start",
  },
  panel: {
    border: "1px solid var(--border)",
    borderRadius: "8px",
    background: "var(--surface)",
    padding: "14px",
    marginBottom: "16px",
    overflow: "hidden",
  },
  panelHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: "12px",
    marginBottom: "12px",
  },
  panelTitle: { margin: 0, fontSize: "16px", letterSpacing: 0 },
  tableWrap: { overflowX: "auto", width: "100%" },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: "13px",
  },
  th: {
    textAlign: "left",
    color: "var(--text-3)",
    borderBottom: "1px solid var(--border)",
    padding: "8px",
    fontWeight: 600,
    whiteSpace: "nowrap",
  },
  td: {
    borderBottom: "1px solid var(--border-light)",
    padding: "8px",
    verticalAlign: "top",
  },
  tr: { cursor: "default" },
  selectedRow: { background: "var(--surface-2)" },
  strong: { fontWeight: 600 },
  muted: { color: "var(--text-3)", fontSize: "12px" },
  mono: {
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    fontSize: "12px",
    color: "var(--text-3)",
  },
  pill: {
    display: "inline-flex",
    alignItems: "center",
    minWidth: "26px",
    justifyContent: "center",
    borderRadius: "999px",
    padding: "2px 7px",
    fontSize: "12px",
    fontWeight: 600,
  },
  metricGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
    gap: "10px",
    marginBottom: "14px",
  },
  metric: {
    border: "1px solid var(--border)",
    borderRadius: "8px",
    padding: "10px",
    background: "var(--surface-2)",
  },
  metricLabel: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    color: "var(--text-3)",
    fontSize: "12px",
    marginBottom: "4px",
  },
  metricValue: { fontSize: "20px", fontWeight: 650, letterSpacing: 0 },
  sectionTitle: {
    fontSize: "14px",
    margin: "16px 0 8px",
    color: "var(--text-2)",
    letterSpacing: 0,
  },
  caseList: {
    display: "grid",
    gap: "8px",
    maxHeight: "620px",
    overflow: "auto",
  },
  caseItem: {
    border: "1px solid var(--border)",
    borderRadius: "8px",
    background: "var(--surface)",
  },
  caseSummary: {
    display: "grid",
    gridTemplateColumns: "auto minmax(0, 1fr) auto",
    gap: "10px",
    alignItems: "center",
    padding: "8px",
    cursor: "pointer",
  },
  caseName: {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    fontSize: "13px",
  },
  caseDuration: { fontSize: "12px", color: "var(--text-3)" },
  caseBody: { padding: "0 8px 8px", overflowX: "auto" },
  pre: {
    margin: "8px 0",
    padding: "10px",
    borderRadius: "6px",
    background: "var(--code-bg)",
    color: "var(--code-text)",
    overflowX: "auto",
    fontSize: "12px",
  },
  compareBar: {
    display: "flex",
    flexWrap: "wrap",
    gap: "10px",
    alignItems: "center",
    marginBottom: "14px",
  },
  emptyCell: {
    padding: "18px 8px",
    textAlign: "center",
    color: "var(--text-3)",
    borderBottom: "1px solid var(--border-light)",
  },
  emptyState: {
    minHeight: "180px",
    display: "grid",
    placeItems: "center",
    color: "var(--text-3)",
    border: "1px dashed var(--border)",
    borderRadius: "8px",
  },
};
