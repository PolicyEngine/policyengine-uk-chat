"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Chart, extractChartSpecs } from "@/components/charts";
import { THEME } from "@/components/theme";

interface SharedConversation {
  title: string;
  messages: Array<{
    role: "user" | "assistant";
    content: string;
    events?: Array<
      | { type: "text"; content: string }
      | { type: "tool"; data: { tool_name: string } }
    >;
  }>;
  author: string | null;
  created_at: string;
}

const markdownComponents = {
  code({ className, children }: { className?: string; children?: React.ReactNode; [key: string]: unknown }) {
    const match = /language-(\w+)/.exec(className || "");
    const isInline = !match && !String(children).includes("\n");
    if (!isInline && match) return <SyntaxHighlighter style={oneDark} language={match[1]} customStyle={{ margin: "12px 0", fontSize: "12px", lineHeight: 1.7, background: "#1a1917", border: "none", borderRadius: 0, borderLeft: `3px solid ${THEME.primary}`, padding: "16px 18px" }}>{String(children).replace(/\n$/, "")}</SyntaxHighlighter>;
    if (isInline) return <code style={{ background: "#f0f0f0", padding: "2px 5px", fontSize: "13px" }}>{children}</code>;
    return <pre style={{ display: "block", margin: "12px 0", lineHeight: 1.7, whiteSpace: "pre-wrap", background: "#1a1917", color: "#c9c5bc", padding: "16px 18px", borderLeft: `3px solid ${THEME.primary}`, fontFamily: "'JetBrains Mono', monospace", fontSize: "12px" }}><code>{children}</code></pre>;
  },
  p: ({ children }: { children?: React.ReactNode }) => <p style={{ margin: "0 0 14px 0", lineHeight: 1.75 }}>{children}</p>,
  strong: ({ children }: { children?: React.ReactNode }) => <strong className="highlight-mark" style={{ fontWeight: 600, color: THEME.text, padding: "1px 3px", margin: "0 -3px" }}>{children}</strong>,
  ul: ({ children }: { children?: React.ReactNode }) => <ul style={{ margin: "0 0 14px 0", paddingLeft: "22px", listStyleType: "disc" }}>{children}</ul>,
  ol: ({ children }: { children?: React.ReactNode }) => <ol style={{ margin: "0 0 14px 0", paddingLeft: "22px", listStyleType: "decimal" }}>{children}</ol>,
  li: ({ children }: { children?: React.ReactNode }) => <li style={{ marginBottom: "5px", lineHeight: 1.65, listStyleType: "inherit" }}>{children}</li>,
  h1: ({ children }: { children?: React.ReactNode }) => <h1 style={{ fontSize: "20px", fontWeight: 600, margin: "22px 0 10px", color: "#1c1a17" }}>{children}</h1>,
  h2: ({ children }: { children?: React.ReactNode }) => <h2 style={{ fontSize: "18px", fontWeight: 600, margin: "20px 0 8px", color: "#1c1a17" }}>{children}</h2>,
  h3: ({ children }: { children?: React.ReactNode }) => <h3 style={{ fontSize: "16px", fontWeight: 600, margin: "16px 0 6px", color: "#1c1a17" }}>{children}</h3>,
  table: ({ children }: { children?: React.ReactNode }) => <table style={{ margin: "14px 0", borderCollapse: "collapse", fontSize: "14px", width: "100%" }}>{children}</table>,
  thead: ({ children }: { children?: React.ReactNode }) => <thead>{children}</thead>,
  tbody: ({ children }: { children?: React.ReactNode }) => <tbody>{children}</tbody>,
  tr: ({ children, ...props }: { children?: React.ReactNode }) => {
    const node = props as { node?: { position?: { start?: { line?: number } } } };
    const rowIndex = node?.node?.position?.start?.line ?? 0;
    return <tr style={{ borderBottom: "1px solid #f0f0ee", background: rowIndex % 2 === 0 ? "#f9f8f6" : "transparent" }}>{children}</tr>;
  },
  th: ({ children }: { children?: React.ReactNode }) => <th style={{ padding: "10px 14px", textAlign: "left", fontFamily: "'Newsreader', Georgia, serif", fontSize: "13px", fontWeight: 400, fontStyle: "italic", color: "#9e9a90", borderBottom: "2px solid #1c1a17" }}>{children}</th>,
  td: ({ children }: { children?: React.ReactNode }) => <td style={{ padding: "9px 14px", color: "#3a3835", fontSize: "14px" }}>{children}</td>,
  del: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
};

function renderMarkdown(content: string) {
  const { charts, cleanContent } = extractChartSpecs(content);

  const hasChartPlaceholder = cleanContent.includes("[CHART_PLACEHOLDER_") || cleanContent.includes("[CHART_LOADING]");
  if (!hasChartPlaceholder) {
    return <ReactMarkdown remarkPlugins={[[remarkGfm, { singleTilde: false }]]} components={markdownComponents as never}>{cleanContent}</ReactMarkdown>;
  }

  const segments: Array<{ type: "text" | "chart"; content?: string; chartIdx?: number }> = [];
  let lastIndex = 0;
  const placeholderRegex = /\[CHART_PLACEHOLDER_(\d+)\]/g;
  let match;
  while ((match = placeholderRegex.exec(cleanContent)) !== null) {
    if (match.index > lastIndex) segments.push({ type: "text", content: cleanContent.slice(lastIndex, match.index) });
    segments.push({ type: "chart", chartIdx: parseInt(match[1], 10) });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < cleanContent.length) segments.push({ type: "text", content: cleanContent.slice(lastIndex) });

  return (
    <>
      {segments.map((segment, idx) => {
        if (segment.type === "text") {
          if (!segment.content?.trim()) return null;
          return <ReactMarkdown key={idx} remarkPlugins={[[remarkGfm, { singleTilde: false }]]} components={markdownComponents as never}>{segment.content}</ReactMarkdown>;
        }
        if (segment.chartIdx !== undefined) {
          const chart = charts[segment.chartIdx];
          if (chart) return <div key={idx} style={{ margin: "16px 0" }}><Chart spec={chart} width={680} height={400} /></div>;
        }
        return null;
      })}
    </>
  );
}

function renderAssistantContent(msg: SharedConversation["messages"][number]) {
  if (!msg.events?.length) return renderMarkdown(msg.content);

  // Only show final text events (skip tool calls and working text)
  const lastToolIdx = msg.events.reduce((acc, e, idx) => e.type === "tool" ? idx : acc, -1);
  const finalEvents = lastToolIdx >= 0 ? msg.events.slice(lastToolIdx + 1) : msg.events;

  return finalEvents
    .filter((e) => e.type === "text")
    .map((event, idx) => (
      <div key={idx}>{renderMarkdown((event as { type: "text"; content: string }).content)}</div>
    ));
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

export default function SharedConversationPage() {
  const params = useParams();
  const token = params.token as string;
  const [data, setData] = useState<SharedConversation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/proxy/conversations/shared/${token}`)
      .then((res) => {
        if (!res.ok) throw new Error(res.status === 404 ? "Conversation not found" : "Failed to load");
        return res.json();
      })
      .then(setData)
      .catch((err) => setError(err.message));
  }, [token]);

  if (error) {
    return (
      <div style={{ minHeight: "100vh", background: THEME.bg, fontFamily: "system-ui, sans-serif", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ textAlign: "center", color: THEME.text3 }}>
          <div style={{ fontSize: "48px", marginBottom: "16px" }}>404</div>
          <div style={{ fontSize: "15px" }}>{error}</div>
          <a href="/" style={{ display: "inline-block", marginTop: "20px", fontSize: "13px", color: THEME.primary }}>Go to PolicyEngine chat</a>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div style={{ minHeight: "100vh", background: THEME.bg, fontFamily: "system-ui, sans-serif", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ color: THEME.muted, fontSize: "14px" }}>Loading...</div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", background: THEME.bg, fontFamily: "system-ui, sans-serif" }}>
      <style>{`
@keyframes highlightSweep { from{background-size:0% 100%}to{background-size:100% 100%} }
.highlight-mark { background: linear-gradient(to right, rgba(44,122,123,0.12), rgba(44,122,123,0.12)); background-size: 0% 100%; background-repeat: no-repeat; background-position: left; animation: highlightSweep 0.6s cubic-bezier(0.16,1,0.3,1) 0.15s forwards; }
table .highlight-mark { animation: none; background: none; padding: 0; margin: 0; }
      `}</style>

      {/* Header */}
      <div style={{ borderBottom: `1px solid ${THEME.border}`, background: THEME.surface, padding: "0 40px", height: "56px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <a href="/" style={{ display: "flex", alignItems: "center" }}>
          <img src="/policyengine-logo.svg" alt="PolicyEngine" style={{ height: "24px" }} />
        </a>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", fontSize: "13px", color: THEME.text3 }}>
          {data.author && <span>Shared by {data.author}</span>}
          <span style={{ color: THEME.muted }}>{formatDate(data.created_at)}</span>
        </div>
      </div>

      {/* Conversation */}
      <div style={{ maxWidth: "760px", margin: "0 auto", padding: "0 40px 80px" }}>
        <div style={{ padding: "28px 0 20px", borderBottom: `1px solid ${THEME.border}`, marginBottom: "8px" }}>
          <h1 style={{ fontSize: "20px", fontWeight: 600, color: THEME.text, margin: 0 }}>{data.title}</h1>
        </div>

        {data.messages.map((msg, idx) => (
          <div key={idx} style={{ marginBottom: "18px" }}>
            {msg.role === "user" ? (
              <div style={{ display: "flex", gap: "14px", padding: "14px 0", borderBottom: `1px solid ${THEME.border}` }}>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "12px", fontWeight: 500, color: "#fff", background: THEME.primary, width: "24px", height: "24px", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>&gt;</div>
                <div style={{ color: THEME.text, fontSize: "15px", lineHeight: 1.6, whiteSpace: "pre-wrap", fontWeight: 500, letterSpacing: "-0.01em" }}>{msg.content}</div>
              </div>
            ) : (
              <div style={{ padding: "18px 0 14px" }}>
                <div style={{ fontFamily: "'Source Serif 4', Georgia, serif", color: THEME.text2, fontSize: "15.5px", lineHeight: 1.8, minWidth: 0 }}>
                  {renderAssistantContent(msg)}
                </div>
              </div>
            )}
          </div>
        ))}

        <div style={{ borderTop: `1px solid ${THEME.border}`, paddingTop: "20px", marginTop: "20px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <a href="/" style={{ fontSize: "13px", color: THEME.primary, textDecoration: "none" }}>Try PolicyEngine chat</a>
          <span style={{ fontSize: "12px", color: THEME.muted }}>policyengine.org</span>
        </div>
      </div>
    </div>
  );
}
