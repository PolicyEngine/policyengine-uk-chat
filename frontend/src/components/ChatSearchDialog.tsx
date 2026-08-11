"use client";

import { useEffect, useRef, useState } from "react";
import { IconMessage, IconSearch, IconX } from "@tabler/icons-react";

export interface ChatSearchResult {
  id: number;
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  snippet: string | null;
}

interface ChatSearchDialogProps {
  open: boolean;
  recent: ChatSearchResult[];
  search: (query: string) => Promise<ChatSearchResult[]>;
  onClose: () => void;
  onSelect: (result: ChatSearchResult) => void;
}

export default function ChatSearchDialog({
  open,
  recent,
  search,
  onClose,
  onSelect,
}: ChatSearchDialogProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ChatSearchResult[]>(recent);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setResults(recent);
      setError(null);
      return;
    }
    inputRef.current?.focus();
  }, [open, recent]);

  useEffect(() => {
    if (!open) return;
    const cleanQuery = query.trim();
    if (!cleanQuery) {
      setResults(recent);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    search(cleanQuery)
      .then((matches) => {
        if (!cancelled) setResults(matches);
      })
      .catch((caught) => {
        if (!cancelled) {
          setResults([]);
          setError(caught instanceof Error ? caught.message : "Search failed");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, query, recent, search]);

  if (!open) return null;

  return (
    <div
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      style={{ position: "fixed", inset: 0, zIndex: 900, background: "rgba(0,0,0,0.46)", backdropFilter: "blur(2px)", display: "flex", justifyContent: "center", alignItems: "flex-start", padding: "min(18vh, 150px) 16px 24px", boxSizing: "border-box" }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search chats"
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
        style={{ width: "min(640px, 100%)", maxHeight: "min(620px, 70vh)", display: "flex", flexDirection: "column", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "16px", boxShadow: "0 24px 70px rgba(0,0,0,0.28)", overflow: "hidden" }}
      >
        <div style={{ position: "relative", display: "flex", alignItems: "center", borderBottom: "1px solid var(--border)" }}>
          <IconSearch size={20} aria-hidden="true" style={{ position: "absolute", left: "18px", color: "var(--muted)" }} />
          <input
            ref={inputRef}
            type="search"
            aria-label="Search chats"
            placeholder="Search chats"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            style={{ width: "100%", border: "none", outline: "none", background: "transparent", color: "var(--text)", padding: "18px 52px", fontSize: "16px", fontFamily: "inherit" }}
          />
          <button type="button" aria-label="Close search" onClick={onClose} style={{ position: "absolute", right: "12px", border: "none", background: "transparent", color: "var(--muted)", cursor: "pointer", padding: "8px", display: "flex" }}>
            <IconX size={18} aria-hidden="true" />
          </button>
        </div>

        <div aria-live="polite" style={{ overflowY: "auto", padding: "8px", minHeight: "120px" }}>
          <div style={{ padding: "7px 10px", color: "var(--muted)", fontSize: "11px", fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase" }}>
            {query.trim() ? "Search results" : "Recent chats"}
          </div>
          {loading ? (
            <div style={{ padding: "18px 12px", color: "var(--muted)", fontSize: "13px" }}>Searching…</div>
          ) : error ? (
            <div role="alert" style={{ padding: "18px 12px", color: "#dc2626", fontSize: "13px" }}>{error}</div>
          ) : results.length === 0 ? (
            <div style={{ padding: "18px 12px", color: "var(--muted)", fontSize: "13px" }}>No matching chats</div>
          ) : (
            results.map((result) => (
              <button
                key={result.id}
                type="button"
                aria-label={result.snippet ? `${result.title} — ${result.snippet}` : result.title}
                onClick={() => onSelect(result)}
                style={{ width: "100%", display: "flex", gap: "11px", alignItems: "flex-start", padding: "11px 12px", border: "none", borderRadius: "10px", background: "transparent", color: "var(--text)", cursor: "pointer", fontFamily: "inherit", textAlign: "left" }}
              >
                <IconMessage size={17} aria-hidden="true" style={{ flexShrink: 0, marginTop: "2px", color: "var(--muted)" }} />
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: "block", fontSize: "14px", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{result.title}</span>
                  {result.snippet && <span style={{ display: "block", marginTop: "3px", color: "var(--muted)", fontSize: "12px", lineHeight: 1.45 }}>{result.snippet}</span>}
                </span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
