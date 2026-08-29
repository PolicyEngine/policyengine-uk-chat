"use client";

import { IconBug, IconChevronDown } from "@tabler/icons-react";
import { useMemo, useState } from "react";

export type InvocationVisibility = "public" | "private";
export type InvocationKind = "capability" | "tool";
export type InvocationStatus =
  | "running"
  | "completed"
  | "needs_input"
  | "unsupported"
  | "failed"
  | "cancelled";

export interface InvocationActivityItem {
  turn_id: string;
  invocation_id: string;
  parent_invocation_id: string | null;
  sequence: number;
  kind: InvocationKind;
  identifier: string;
  version: string;
  visibility: InvocationVisibility;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  status: InvocationStatus;
  summary: string;
  debug_input?: unknown | null;
  debug_output?: unknown | null;
}

export interface ConversationActivityResponse {
  projection: "normal" | "debug";
  invocations: InvocationActivityItem[];
}

export const mergeInvocationActivity = (
  current: InvocationActivityItem[],
  incoming: InvocationActivityItem,
): InvocationActivityItem[] => {
  const byId = new Map(current.map((item) => [item.invocation_id, item]));
  byId.set(incoming.invocation_id, incoming);
  return [...byId.values()].sort(
    (left, right) => left.sequence - right.sequence || left.started_at.localeCompare(right.started_at),
  );
};

interface InvocationActivityProps {
  invocations: InvocationActivityItem[];
  debug: boolean;
}

const statusLabel = (status: InvocationStatus): string => status.replace("_", " ");

const ActivitySpinner = ({ label }: { label: string }) => (
  <span
    role="status"
    aria-label={label}
    style={{
      width: "11px",
      height: "11px",
      flex: "0 0 auto",
      border: "2px solid var(--border)",
      borderTopColor: "var(--accent)",
      borderRadius: "50%",
      animation: "invocation-activity-spin 800ms linear infinite",
    }}
  />
);

const isJsonObject = (value: unknown): value is Record<string, unknown> => (
  typeof value === "object" && value !== null && !Array.isArray(value)
);

const collectionLabel = (value: unknown): string => {
  if (Array.isArray(value)) return `Array (${value.length})`;
  if (isJsonObject(value)) return `Object (${Object.keys(value).length})`;
  return "";
};

const primitiveLabel = (value: unknown): string => {
  if (value === null) return "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "[unsupported value]";
};

interface JsonTreeNodeProps {
  label: string;
  value: unknown;
  depth: number;
}

const JsonTreeNode = ({ label, value, depth }: JsonTreeNodeProps) => {
  const entries = Array.isArray(value)
    ? value.map((item, index) => [`[${index}]`, item] as const)
    : isJsonObject(value)
      ? Object.entries(value)
      : null;

  if (entries === null) {
    return (
      <div style={{ display: "flex", gap: "7px", minWidth: 0 }}>
        <span style={{ color: "var(--accent)", flex: "0 0 auto" }}>{label}:</span>
        <span style={{ color: "var(--text-2)", overflowWrap: "anywhere" }}>
          {primitiveLabel(value)}
        </span>
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div style={{ display: "flex", gap: "7px" }}>
        <span style={{ color: "var(--accent)" }}>{label}:</span>
        <span style={{ color: "var(--text-3)" }}>{Array.isArray(value) ? "[]" : "{}"}</span>
      </div>
    );
  }

  return (
    <details>
      <summary
        style={{
          color: "var(--text-2)",
          cursor: "pointer",
          overflowWrap: "anywhere",
        }}
      >
        <span style={{ color: "var(--accent)" }}>{label}</span>
        <span style={{ color: "var(--text-3)" }}> · {collectionLabel(value)}</span>
      </summary>
      <div
        role="group"
        aria-label={`${label} values`}
        style={{
          borderLeft: "1px solid var(--border)",
          display: "grid",
          gap: "3px",
          margin: "3px 0 4px 5px",
          paddingLeft: `${Math.min(depth, 4) * 2 + 10}px`,
        }}
      >
        {entries.map(([childLabel, childValue]) => (
          <JsonTreeNode
            key={childLabel}
            label={childLabel}
            value={childValue}
            depth={depth + 1}
          />
        ))}
      </div>
    </details>
  );
};

const JsonTree = ({ label, value }: { label: string; value: unknown }) => {
  const entries = Array.isArray(value)
    ? value.map((item, index) => [`[${index}]`, item] as const)
    : isJsonObject(value)
      ? Object.entries(value)
      : null;

  return (
    <div
      role="tree"
      aria-label={`${label} JSON`}
      style={{
        marginTop: "6px",
        padding: "8px 10px",
        maxHeight: "320px",
        overflow: "auto",
        borderRadius: "8px",
        background: "var(--surface-2)",
        color: "var(--text-2)",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        fontSize: "11px",
        lineHeight: 1.55,
      }}
    >
      <style>{`@keyframes invocation-activity-spin { to { transform: rotate(360deg); } }`}</style>
      {entries === null ? (
        <span>{primitiveLabel(value)}</span>
      ) : entries.length === 0 ? (
        <span>{Array.isArray(value) ? "[]" : "{}"}</span>
      ) : (
        <div style={{ display: "grid", gap: "3px" }}>
          {entries.map(([childLabel, childValue]) => (
            <JsonTreeNode
              key={childLabel}
              label={childLabel}
              value={childValue}
              depth={1}
            />
          ))}
        </div>
      )}
    </div>
  );
};

export default function InvocationActivity({ invocations, debug }: InvocationActivityProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [expandedInvocations, setExpandedInvocations] = useState<Set<string>>(
    () => new Set(),
  );
  const rows = useMemo(() => {
    const visible = debug
      ? invocations
      : invocations.filter((item) => item.visibility === "public");
    const byId = new Map(visible.map((item) => [item.invocation_id, item]));
    const attempts = new Map<string, number>();
    return [...visible]
      .sort((left, right) => left.sequence - right.sequence)
      .map((item) => {
        let depth = 0;
        let parentId = item.parent_invocation_id;
        const visited = new Set<string>();
        while (parentId && byId.has(parentId) && !visited.has(parentId)) {
          visited.add(parentId);
          depth += 1;
          parentId = byId.get(parentId)?.parent_invocation_id ?? null;
        }
        const attemptKey = `${item.turn_id}:${item.parent_invocation_id ?? "root"}:${item.kind}:${item.identifier}`;
        const attempt = (attempts.get(attemptKey) ?? 0) + 1;
        attempts.set(attemptKey, attempt);
        return { item, depth, attempt };
      });
  }, [debug, invocations]);
  const isRunning = rows.some(({ item }) => item.status === "running");

  if (!rows.length) return null;

  const toggleInvocation = (invocationId: string) => {
    setExpandedInvocations((current) => {
      const updated = new Set(current);
      if (updated.has(invocationId)) updated.delete(invocationId);
      else updated.add(invocationId);
      return updated;
    });
  };

  const renderProjection = (label: "Input" | "Output", value: unknown) => (
    <details
      style={{
        borderTop: "1px solid var(--border)",
        padding: "6px 0 0",
        marginTop: "6px",
      }}
    >
      <summary
        style={{
          color: "var(--text-3)",
          cursor: "pointer",
          fontSize: "10px",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        {label}
      </summary>
      <JsonTree label={label} value={value} />
    </details>
  );

  return (
    <section
      aria-label="Invocation activity"
      style={{
        width: "100%",
        maxWidth: "760px",
        margin: "0 auto 14px",
        border: "1px solid var(--border)",
        borderRadius: "14px",
        background: "var(--surface)",
        overflow: "hidden",
      }}
    >
      <button
        type="button"
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((value) => !value)}
        style={{
          width: "100%",
          border: 0,
          background: "transparent",
          color: "var(--text-2)",
          padding: "9px 12px",
          display: "flex",
          alignItems: "center",
          gap: "7px",
          cursor: "pointer",
          fontFamily: "inherit",
          fontSize: "12px",
          textAlign: "left",
        }}
      >
        <IconChevronDown
          size={13}
          style={{ transform: collapsed ? "rotate(-90deg)" : "none", transition: "transform 120ms" }}
        />
        <IconBug size={13} />
        {isRunning && <ActivitySpinner label="Capability processing" />}
        <span>Activity · {rows.length} calls</span>
        <span style={{ marginLeft: "auto", color: "var(--text-3)" }}>
          {debug ? "public and private" : "public"}
        </span>
      </button>
      {!collapsed && (
        <ol style={{ listStyle: "none", margin: 0, padding: "0 12px 10px", maxHeight: "min(60vh, 560px)", overflowY: "auto" }}>
          {rows.map(({ item, depth, attempt }) => {
            const hasDebugDetails = debug && (
              item.debug_input !== null && item.debug_input !== undefined
              || item.debug_output !== null && item.debug_output !== undefined
            );
            const expanded = hasDebugDetails && expandedInvocations.has(item.invocation_id);
            return (
              <li
                key={item.invocation_id}
                data-visibility={item.visibility}
                style={{
                  marginLeft: `${Math.min(depth, 6) * 16}px`,
                  borderLeft: depth ? "2px solid var(--border)" : undefined,
                  padding: "5px 0 5px 9px",
                  minWidth: 0,
                  fontSize: "12px",
                }}
              >
                <button
                  type="button"
                  aria-label={hasDebugDetails ? `Toggle ${item.identifier} details` : undefined}
                  aria-expanded={hasDebugDetails ? expanded : undefined}
                  disabled={!hasDebugDetails}
                  onClick={hasDebugDetails ? () => toggleInvocation(item.invocation_id) : undefined}
                  style={{
                    width: "100%",
                    padding: 0,
                    border: 0,
                    background: "transparent",
                    color: "inherit",
                    display: "flex",
                    alignItems: "baseline",
                    gap: "7px",
                    minWidth: 0,
                    cursor: hasDebugDetails ? "pointer" : "default",
                    fontFamily: "inherit",
                    fontSize: "12px",
                    textAlign: "left",
                  }}
                >
                  {hasDebugDetails && (
                    <IconChevronDown
                      size={11}
                      style={{
                        flex: "0 0 auto",
                        transform: expanded ? "none" : "rotate(-90deg)",
                        transition: "transform 120ms",
                      }}
                    />
                  )}
                  {item.status === "running" && (
                    <ActivitySpinner label={`${item.identifier} running`} />
                  )}
                  <span style={{ color: "var(--text)", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>
                    {item.identifier}
                  </span>
                  <span style={{ color: "var(--text-3)" }}>{item.kind}</span>
                  <span style={{ color: "var(--text-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.summary}
                  </span>
                  {attempt > 1 && <span style={{ color: "var(--text-3)" }}>attempt {attempt}</span>}
                  {item.visibility === "private" && (
                    <span style={{ color: "var(--accent)", fontSize: "10px", textTransform: "uppercase" }}>private</span>
                  )}
                  <span style={{ marginLeft: "auto", color: item.status === "failed" ? "#ef4444" : "var(--text-3)", whiteSpace: "nowrap" }}>
                    {statusLabel(item.status)}
                    {item.duration_ms !== null ? ` · ${item.duration_ms} ms` : ""}
                  </span>
                </button>
                {expanded && (
                  <div style={{ padding: "0 8px 6px 18px" }}>
                    {item.debug_input !== null && item.debug_input !== undefined
                      ? renderProjection("Input", item.debug_input)
                      : null}
                    {item.debug_output !== null && item.debug_output !== undefined
                      ? renderProjection("Output", item.debug_output)
                      : null}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
