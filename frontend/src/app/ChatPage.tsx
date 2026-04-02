"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Loader } from "@mantine/core";
import { IconCheck, IconAlertCircle, IconX, IconTrash, IconChevronDown, IconUser, IconLogout } from "@tabler/icons-react";
import { useAuth } from "@/utils/AuthContext";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Chart, extractChartSpecs, ChartSpec } from "@/components/charts";

const EXAMPLE_QUERIES = [
  "What's the current personal allowance?",
  "How much tax would I pay on £50,000?",
  "Show me the income tax bands for 2026",
  "Compare Universal Credit to legacy benefits",
  "What benefits can a single parent claim?",
  "How does the marriage allowance work?",
  "Chart the marginal tax rate from £0 to £150k",
  "What's the national insurance threshold?",
  "How much child benefit for 3 children?",
  "Model a family with 2 kids earning £35k",
  "What happens to benefits at £100k income?",
  "Show me the taper rate for Universal Credit",
  "How has the personal allowance changed over time?",
  "What's the pension annual allowance?",
  "Calculate tax for self-employed earning £80k",
  "Who wins from raising the basic rate threshold?",
  "What's the high income child benefit charge?",
  "Show decile impacts for a flat tax policy",
  "How does Scottish income tax differ?",
  "What's the budgetary cost of raising the personal allowance by £1,000?",
];

interface ConversationSummary {
  id: number;
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface ConversationDetail extends ConversationSummary {
  messages: Array<{ role: string; content: string; events?: StreamEvent[] }>;
}

function formatRelativeTime(isoString: string): string {
  const date = new Date(isoString);
  const diffMins = Math.floor((Date.now() - date.getTime()) / 60000);
  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function useAnimatedPlaceholder(queries: string[], enabled: boolean) {
  const [placeholder, setPlaceholder] = useState("");
  const [queryIndex, setQueryIndex] = useState(0);
  const [charIndex, setCharIndex] = useState(0);
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    setQueryIndex(Math.floor(Math.random() * queries.length));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!enabled) { setPlaceholder(""); return; }
    const currentQuery = queries[queryIndex];
    const pauseTime = isDeleting ? 500 : 2000;
    const typeSpeed = isDeleting ? 30 : 50;

    const timeout = setTimeout(() => {
      if (!isDeleting) {
        if (charIndex < currentQuery.length) { setPlaceholder(currentQuery.slice(0, charIndex + 1)); setCharIndex(charIndex + 1); }
        else setTimeout(() => setIsDeleting(true), pauseTime);
      } else {
        if (charIndex > 0) { setPlaceholder(currentQuery.slice(0, charIndex - 1)); setCharIndex(charIndex - 1); }
        else { setIsDeleting(false); setQueryIndex((queryIndex + 1 + Math.floor(Math.random() * (queries.length - 1))) % queries.length); }
      }
    }, charIndex === currentQuery.length && !isDeleting ? pauseTime : typeSpeed);

    return () => clearTimeout(timeout);
  }, [queries, queryIndex, charIndex, isDeleting, enabled]);

  return placeholder;
}

interface ToolData {
  tool_name: string;
  tool_id: string;
  status: "pending" | "success" | "error";
  input?: Record<string, unknown>;
  result_summary?: string;
}

type StreamEvent = { type: "text"; content: string } | { type: "tool"; data: ToolData };

interface Message {
  role: "user" | "assistant";
  content: string;
  events?: StreamEvent[];
  isComplete?: boolean;
}

async function apiRequest<T>(method: string, endpoint: string, params?: Record<string, string>, body?: unknown): Promise<T> {
  const url = new URL(`/api/proxy/${endpoint}`, window.location.origin);
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const options: RequestInit = { method, headers: { "Content-Type": "application/json" } };
  if (body && ["POST", "PUT", "PATCH"].includes(method)) options.body = JSON.stringify(body);
  const res = await fetch(url.toString(), options);
  if (!res.ok) throw new Error(`API error ${res.status}`);
  if (res.status === 204) return undefined as T;
  return res.json();
}

export default function ChatPage() {
  const { user, loading: authLoading, signIn, signUp, signOut } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isWaiting, setIsWaiting] = useState(false);
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [collapsedWorking, setCollapsedWorking] = useState<Set<number>>(new Set());
  const [historyOpen, setHistoryOpen] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  const [showAuth, setShowAuth] = useState(false);
  const [authMode, setAuthMode] = useState<"signin" | "signup">("signin");
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const sessionId = useRef<string | null>(null);
  const debugLog = useRef<string[]>([]);

  const hasMessages = messages.length > 0;
  const animatedPlaceholder = useAnimatedPlaceholder(EXAMPLE_QUERIES, !hasMessages && !input);

  useEffect(() => {
    inputRef.current?.focus();
    if (!authLoading && user) {
      apiRequest<ConversationSummary[]>("GET", "conversations", { user_id: user.id })
        .then(setConversations)
        .catch(() => {});
    } else if (!authLoading && !user) {
      setConversations([]);
    }
  }, [user, authLoading]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [messages]);

  const loadConversation = async (conv: ConversationSummary) => {
    try {
      const data = await apiRequest<ConversationDetail>("GET", `conversations/${conv.id}`);
      const loaded: Message[] = data.messages.map((m) => ({ role: m.role as "user" | "assistant", content: m.content, isComplete: true, events: m.events }));
      const collapsed = new Set(loaded.map((m, i) => (m.role === "assistant" && m.events?.some((e) => e.type === "tool") ? i : -1)).filter((i) => i >= 0));
      setMessages(loaded);
      sessionId.current = data.session_id;
      setActiveConversationId(data.id);
      setHistoryOpen(true);
      setCollapsedWorking(collapsed);
    } catch (e) { console.error("Failed to load conversation", e); }
  };

  const deleteConversation = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    try {
      await apiRequest("DELETE", `conversations/${id}`);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (activeConversationId === id) setActiveConversationId(null);
    } catch (e) { console.error(e); }
  };

  const saveConversation = useCallback(async (msgs: Message[], sid: string) => {
    const firstUserMsg = msgs.find((m) => m.role === "user");
    if (!firstUserMsg) return;
    const firstAssistantMsg = msgs.find((m) => m.role === "assistant");
    const firstAssistantContent = (() => {
      if (!firstAssistantMsg?.isComplete || !firstAssistantMsg.events?.length) return firstAssistantMsg?.content;
      const lastToolIdx = firstAssistantMsg.events.reduce((acc, e, i) => e.type === "tool" ? i : acc, -1);
      if (lastToolIdx >= 0) return firstAssistantMsg.events.slice(lastToolIdx + 1).filter((e): e is { type: "text"; content: string } => e.type === "text").map((e) => e.content).join("") || firstAssistantMsg.content;
      return firstAssistantMsg.content;
    })();

    let title = firstUserMsg.content.slice(0, 60);
    try {
      const { title: generated } = await apiRequest<{ title: string }>("POST", "chat/title", undefined, { first_user_message: firstUserMsg.content, first_assistant_message: firstAssistantContent });
      title = generated;
    } catch {}

    const apiMessages = msgs.map((m) => {
      if (m.role === "assistant" && m.isComplete && m.events?.length) {
        const savedEvents = m.events.map((e) => e.type === "tool" ? { ...e, data: { ...e.data, result_summary: undefined } } : e);
        return { role: m.role, content: m.content, events: savedEvents };
      }
      return { role: m.role, content: m.content };
    });

    try {
      const saved = await apiRequest<ConversationDetail>("POST", "conversations", undefined, { session_id: sid, title, messages: apiMessages, user_id: user?.id });
      setActiveConversationId(saved.id);
      setConversations((prev) => {
        const filtered = prev.filter((c) => c.session_id !== sid);
        return [{ id: saved.id, session_id: sid, title, created_at: saved.created_at, updated_at: saved.updated_at }, ...filtered];
      });
    } catch (e) { console.error("Failed to save conversation", e); }
  }, [user]);

  const startNewChat = () => {
    setMessages([]);
    sessionId.current = null;
    setActiveConversationId(null);
    setCollapsedWorking(new Set());
    setHistoryOpen(false);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const sendMessage = async () => {
    if (!input.trim() || isStreaming) return;
    const userMessage: Message = { role: "user", content: input };
    const allMessages = [...messages, userMessage];
    setMessages((prev) => [...prev, userMessage]);
    if (messages.length === 0 && user) setHistoryOpen(true);
    setInput("");
    setIsStreaming(true);
    setIsWaiting(true);
    debugLog.current = [];

    const apiMessages = allMessages.map((msg) => {
      let content = msg.content;
      if (msg.role === "assistant" && msg.events) {
        const toolResults = msg.events.filter((e): e is { type: "tool"; data: ToolData } => e.type === "tool" && !!e.data.result_summary).map((e) => `[Tool: ${e.data.tool_name}] ${e.data.result_summary}`).join("\n\n");
        if (toolResults) content += "\n\n---\nTool results:\n" + toolResults;
      }
      return { role: msg.role, content };
    });

    let events: StreamEvent[] = [];
    let currentText = "";
    const toolsMap = new Map<string, ToolData>();

    const updateMessage = () => {
      setMessages((prev) => {
        const newMsgs = [...prev];
        const lastIdx = newMsgs.length - 1;
        if (newMsgs[lastIdx]?.role === "assistant") newMsgs[lastIdx] = { role: "assistant", content: currentText, events: [...events] };
        else newMsgs.push({ role: "assistant", content: currentText, events: [...events] });
        return newMsgs;
      });
    };

    try {
      // Hit the backend directly for SSE to avoid Next.js proxy buffering
      const backendBase = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";
      const response = await fetch(`${backendBase}/chat/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: apiMessages, session_id: sessionId.current }),
      });
      if (!response.ok) throw new Error("Request failed");
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) throw new Error("No body");

      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          debugLog.current.push(line);
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === "chunk") {
              setIsWaiting(false);
              const lastEvent = events[events.length - 1];
              if (lastEvent?.type === "text") lastEvent.content += data.content;
              else events.push({ type: "text", content: data.content });
              currentText += data.content;
              updateMessage();
            } else if (data.type === "tool_start") {
              setIsWaiting(false);
              const toolData: ToolData = { tool_name: data.tool_name, tool_id: data.tool_id, status: "pending" };
              toolsMap.set(data.tool_id, toolData);
              events.push({ type: "tool", data: toolData });
              updateMessage();
            } else if (data.type === "tool_use") {
              const existing = toolsMap.get(data.tool_id);
              if (existing) existing.input = data.tool_input;
              else { const td: ToolData = { tool_name: data.tool_name, tool_id: data.tool_id, status: "pending", input: data.tool_input }; toolsMap.set(data.tool_id, td); events.push({ type: "tool", data: td }); }
              updateMessage();
            } else if (data.type === "tool_result") {
              const tool = toolsMap.get(data.tool_id);
              if (tool) { tool.status = data.status; tool.result_summary = data.result_summary; }
              updateMessage();
              setIsWaiting(true);
            } else if (data.type === "done") {
              setIsWaiting(false);
              if (data.session_id) sessionId.current = data.session_id;
              const hasTools = events.some((e) => e.type === "tool");
              if (hasTools) {
                setMessages((prev) => {
                  const newMsgs = [...prev];
                  const lastIdx = newMsgs.length - 1;
                  if (newMsgs[lastIdx]?.role === "assistant") newMsgs[lastIdx] = { ...newMsgs[lastIdx], isComplete: true };
                  setCollapsedWorking((c) => new Set(c).add(lastIdx));
                  return newMsgs;
                });
              }
              if (data.session_id) {
                const finalMsgs = [...allMessages, { role: "assistant" as const, content: currentText, isComplete: true, events: [...events] }];
                saveConversation(finalMsgs, data.session_id);
              }
              setTimeout(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }), 100);
            } else if (data.type === "error") {
              const errorText = `Error: ${data.content || "Something went wrong"}`;
              const lastEvent = events[events.length - 1];
              if (lastEvent?.type === "text") lastEvent.content += "\n\n" + errorText;
              else events.push({ type: "text", content: errorText });
              currentText += errorText;
              updateMessage();
            }
          } catch {}
        }
      }
    } catch (error) {
      setMessages((prev) => [...prev, { role: "assistant", content: `Something went wrong: ${error instanceof Error ? error.message : "Unknown error"}` }]);
    } finally {
      setIsStreaming(false);
      setIsWaiting(false);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  const autoResize = (el: HTMLTextAreaElement) => { el.style.height = "auto"; el.style.height = el.scrollHeight + "px"; };

  const toggleTool = (toolId: string) => {
    setExpandedTools((prev) => { const next = new Set(prev); if (next.has(toolId)) next.delete(toolId); else next.add(toolId); return next; });
  };

  const renderTool = (t: ToolData) => {
    const isExpanded = expandedTools.has(t.tool_id);
    return (
      <div key={t.tool_id} style={{ margin: "4px 0" }}>
        <div onClick={() => t.status !== "pending" && toggleTool(t.tool_id)} style={{ display: "inline-flex", alignItems: "center", gap: "6px", color: "#9e9a90", fontSize: "14px", cursor: t.status !== "pending" ? "pointer" : "default", userSelect: "none", padding: "3px 0" }}>
          {t.status === "pending" && <Loader size={11} color="#228be6" />}
          {t.status === "success" && <IconCheck size={12} color="#228be6" />}
          {t.status === "error" && <IconAlertCircle size={12} color="#b91c1c" />}
          <span>{t.tool_name.replace(/_/g, " ")}</span>
          {t.status !== "pending" && <IconChevronDown size={12} style={{ opacity: 0.4, transform: isExpanded ? "rotate(180deg)" : "none", transition: "transform 0.15s" }} />}
        </div>
        {isExpanded && (
          <div style={{ marginTop: "6px", marginLeft: "18px", padding: "10px 12px", background: "#f8f9fa", border: "1px solid #e2e8f0", fontSize: "12px", lineHeight: 1.5, color: "#6b6860" }}>
            {t.input && Object.keys(t.input).length > 0 && (
              <div style={{ marginBottom: "8px" }}>
                <div style={{ fontWeight: 600, marginBottom: "4px", color: "#1c1a17", fontSize: "11px", textTransform: "uppercase", letterSpacing: "0.04em" }}>Input</div>
                <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: "11px" }}>{JSON.stringify(t.input, null, 2)}</pre>
              </div>
            )}
            {t.result_summary && (
              <div>
                <div style={{ fontWeight: 600, marginBottom: "4px", color: "#1c1a17", fontSize: "11px", textTransform: "uppercase", letterSpacing: "0.04em" }}>Result</div>
                <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: "200px", overflow: "auto", fontSize: "11px" }}>{t.result_summary}</pre>
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  const renderMarkdown = (content: string) => {
    const { charts, cleanContent } = extractChartSpecs(content);

    const markdownComponents = {
      code({ inline, className, children, ...props }: { inline?: boolean; className?: string; children?: React.ReactNode }) {
        const match = /language-(\w+)/.exec(className || "");
        if (!inline && match) return <SyntaxHighlighter style={oneLight} language={match[1]} customStyle={{ margin: "12px 0", fontSize: "13px", background: "#f6f6f6", border: "none", borderRadius: 0 }}>{String(children).replace(/\n$/, "")}</SyntaxHighlighter>;
        if (!inline && !match) return <span style={{ display: "block", margin: "0 0 14px 0", lineHeight: 1.75 }}>{children}</span>;
        return <code style={{ background: "#f0f0f0", padding: "2px 5px", fontSize: "13px" }}>{children}</code>;
      },
      p: ({ children }: { children?: React.ReactNode }) => <p style={{ margin: "0 0 14px 0", lineHeight: 1.75 }}>{children}</p>,
      strong: ({ children }: { children?: React.ReactNode }) => <strong style={{ fontWeight: 600, color: "#1c1a17" }}>{children}</strong>,
      ul: ({ children }: { children?: React.ReactNode }) => <ul style={{ margin: "0 0 14px 0", paddingLeft: "22px", listStyleType: "disc" }}>{children}</ul>,
      ol: ({ children }: { children?: React.ReactNode }) => <ol style={{ margin: "0 0 14px 0", paddingLeft: "22px", listStyleType: "decimal" }}>{children}</ol>,
      li: ({ children }: { children?: React.ReactNode }) => <li style={{ marginBottom: "5px", lineHeight: 1.65, listStyleType: "inherit" }}>{children}</li>,
      h1: ({ children }: { children?: React.ReactNode }) => <h1 style={{ fontSize: "20px", fontWeight: 600, margin: "22px 0 10px", color: "#1c1a17" }}>{children}</h1>,
      h2: ({ children }: { children?: React.ReactNode }) => <h2 style={{ fontSize: "18px", fontWeight: 600, margin: "20px 0 8px", color: "#1c1a17" }}>{children}</h2>,
      h3: ({ children }: { children?: React.ReactNode }) => <h3 style={{ fontSize: "16px", fontWeight: 600, margin: "16px 0 6px", color: "#1c1a17" }}>{children}</h3>,
      table: ({ children }: { children?: React.ReactNode }) => <table style={{ margin: "14px 0", borderCollapse: "collapse", fontSize: "15px", width: "100%" }}>{children}</table>,
      thead: ({ children }: { children?: React.ReactNode }) => <thead style={{ background: "#f8f9fa" }}>{children}</thead>,
      tbody: ({ children }: { children?: React.ReactNode }) => <tbody>{children}</tbody>,
      tr: ({ children }: { children?: React.ReactNode }) => <tr style={{ borderBottom: "1px solid #e2e8f0" }}>{children}</tr>,
      th: ({ children }: { children?: React.ReactNode }) => <th style={{ padding: "9px 14px", textAlign: "left", fontWeight: 600, color: "#1c1a17" }}>{children}</th>,
      td: ({ children }: { children?: React.ReactNode }) => <td style={{ padding: "9px 14px", color: "#4b4843" }}>{children}</td>,
    };

    const hasChartPlaceholder = cleanContent.includes("[CHART_PLACEHOLDER_") || cleanContent.includes("[CHART_LOADING]");
    if (!hasChartPlaceholder) {
      return <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents as never}>{cleanContent}</ReactMarkdown>;
    }

    const segments: Array<{ type: "text" | "chart" | "loading"; content?: string; chartIdx?: number }> = [];
    let lastIndex = 0;
    const placeholderRegex = /\[CHART_PLACEHOLDER_(\d+)\]|\[CHART_LOADING\]/g;
    let match;
    while ((match = placeholderRegex.exec(cleanContent)) !== null) {
      if (match.index > lastIndex) segments.push({ type: "text", content: cleanContent.slice(lastIndex, match.index) });
      if (match[0] === "[CHART_LOADING]") segments.push({ type: "loading" });
      else segments.push({ type: "chart", chartIdx: parseInt(match[1], 10) });
      lastIndex = match.index + match[0].length;
    }
    if (lastIndex < cleanContent.length) segments.push({ type: "text", content: cleanContent.slice(lastIndex) });

    return (
      <>
        {segments.map((segment, idx) => {
          if (segment.type === "text") {
            if (!segment.content?.trim()) return null;
            return <ReactMarkdown key={idx} remarkPlugins={[remarkGfm]} components={markdownComponents as never}>{segment.content}</ReactMarkdown>;
          }
          if (segment.type === "loading") return <div key={idx} style={{ margin: "16px 0", padding: "40px", background: "#f9fafb", border: "1px solid #e5e7eb", display: "flex", alignItems: "center", justifyContent: "center", gap: "10px", color: "#9ca3af", fontSize: "13px" }}><Loader size={14} color="#228be6" /><span>Generating chart…</span></div>;
          if (segment.type === "chart" && segment.chartIdx !== undefined) {
            const chart = charts[segment.chartIdx];
            if (chart) return <div key={idx} style={{ margin: "16px 0" }}><Chart spec={chart} width={680} height={400} /></div>;
          }
          return null;
        })}
      </>
    );
  };

  const renderAssistantMessage = (msg: Message, msgIdx: number) => {
    if (!msg.events?.length) return renderMarkdown(msg.content);

    const lastToolIdx = msg.events.reduce((acc, e, idx) => e.type === "tool" ? idx : acc, -1);
    const hasTools = lastToolIdx >= 0;
    const isWorkingCollapsed = collapsedWorking.has(msgIdx);

    if (msg.isComplete && hasTools) {
      const workingEvents = msg.events.slice(0, lastToolIdx + 1);
      const finalEvents = msg.events.slice(lastToolIdx + 1);
      const toggleWorking = () => setCollapsedWorking((prev) => { const next = new Set(prev); if (next.has(msgIdx)) next.delete(msgIdx); else next.add(msgIdx); return next; });

      return (
        <>
          <div onClick={toggleWorking} style={{ display: "inline-flex", alignItems: "center", gap: "6px", color: "#9e9a90", fontSize: "14px", cursor: "pointer", userSelect: "none", marginBottom: isWorkingCollapsed ? "18px" : "12px", padding: "3px 0" }}>
            <IconChevronDown size={14} style={{ opacity: 0.5, transform: isWorkingCollapsed ? "rotate(-90deg)" : "none", transition: "transform 0.15s" }} />
            <span>{isWorkingCollapsed ? "Show working" : "Hide working"}</span>
            <span style={{ opacity: 0.5 }}>· {workingEvents.filter((e) => e.type === "tool").length} tool calls</span>
          </div>
          {!isWorkingCollapsed && (
            <div style={{ marginBottom: "16px", paddingLeft: "4px", borderLeft: "2px solid #e2e8f0" }}>
              <div style={{ paddingLeft: "14px" }}>
                {workingEvents.map((event, idx) =>
                  event.type === "text"
                    ? <div key={idx} style={{ opacity: 0.6, fontSize: "13px" }}>{renderMarkdown(event.content)}</div>
                    : renderTool(event.data)
                )}
              </div>
            </div>
          )}
          {finalEvents.map((event, idx) =>
            event.type === "text" ? <div key={idx}>{renderMarkdown(event.content)}</div> : renderTool(event.data)
          )}
        </>
      );
    }

    return msg.events.map((event, idx) =>
      event.type === "text" ? <div key={idx}>{renderMarkdown(event.content)}</div> : renderTool(event.data)
    );
  };

  const isEmbed = typeof window !== "undefined" && new URLSearchParams(window.location.search).has("embed");

  return (
    <div style={{ minHeight: "100vh", background: "#fafaf9", fontFamily: "system-ui, sans-serif" }}>
      {/* Header */}
      {!isEmbed && (
        <div style={{ borderBottom: "1px solid #e5e7eb", background: "#fff", padding: "0 40px", height: "56px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <img src="/policyengine-logo.svg" alt="PolicyEngine" style={{ height: "24px" }} />
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            {hasMessages && (
              <button onClick={() => { navigator.clipboard.writeText(debugLog.current.join("\n")); }} style={{ fontSize: "12px", color: "#9ca3af", cursor: "pointer", padding: "4px 10px", border: "1px solid #e5e7eb", background: "transparent", fontFamily: "inherit" }}>
                Copy debug
              </button>
            )}
            {hasMessages && (
              <button onClick={startNewChat} style={{ fontSize: "13px", color: "#228be6", cursor: "pointer", padding: "5px 12px", border: "1px solid #228be6", background: "transparent", fontFamily: "inherit" }}>
                New chat
              </button>
            )}
            {user ? (
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span style={{ fontSize: "13px", color: "#6b7280" }}>{user.email}</span>
                <button onClick={signOut} title="Sign out" style={{ background: "none", border: "none", cursor: "pointer", color: "#9ca3af", display: "flex", padding: "4px" }}>
                  <IconLogout size={16} />
                </button>
              </div>
            ) : (
              <button onClick={() => { setShowAuth(true); setAuthError(null); }} style={{ fontSize: "13px", color: "#6b7280", cursor: "pointer", padding: "5px 12px", border: "1px solid #e5e7eb", background: "transparent", fontFamily: "inherit", display: "flex", alignItems: "center", gap: "6px" }}>
                <IconUser size={14} /> Sign in
              </button>
            )}
          </div>
        </div>
      )}

      {/* Body */}
      <div style={{ display: "flex", maxWidth: "1200px", margin: "0 auto", padding: "0 40px", gap: "0" }}>
        {/* Sidebar */}
        {user && (!hasMessages || historyOpen) && (
          <div style={{ width: "280px", flexShrink: 0, borderRight: "1px solid #e5e7eb", paddingRight: "24px", paddingTop: "32px", position: "sticky", top: 0, height: "calc(100vh - 57px)", overflowY: "auto", alignSelf: "flex-start" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
              <div style={{ fontSize: "11px", color: "#9e9a90", letterSpacing: "0.05em", textTransform: "uppercase", fontWeight: 500 }}>Previous chats</div>
              {hasMessages && (
                <button onClick={() => setHistoryOpen(false)} style={{ background: "none", border: "none", cursor: "pointer", color: "#9ca3af", display: "flex", padding: 0 }}>
                  <IconX size={14} />
                </button>
              )}
            </div>
            {conversations.length === 0
              ? <div style={{ fontSize: "12px", color: "#9ca3af", fontStyle: "italic" }}>No previous chats</div>
              : <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                  {conversations.map((conv) => (
                    <div key={conv.id} onClick={() => loadConversation(conv)} style={{ padding: "10px 12px", cursor: "pointer", background: activeConversationId === conv.id ? "#f0f9ff" : "transparent", borderLeft: activeConversationId === conv.id ? "2px solid #228be6" : "2px solid transparent", display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "8px" }}
                      onMouseEnter={(e) => { if (activeConversationId !== conv.id) (e.currentTarget as HTMLElement).style.background = "#f9fafb"; }}
                      onMouseLeave={(e) => { if (activeConversationId !== conv.id) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: "14px", color: "#1c1a17", lineHeight: 1.45 }}>{conv.title}</div>
                        <div style={{ fontSize: "12px", color: "#9e9a90", marginTop: "4px" }}>{formatRelativeTime(conv.updated_at)}</div>
                      </div>
                      <button onClick={(e) => deleteConversation(e, conv.id)} style={{ flexShrink: 0, background: "none", border: "none", color: "#d1d5db", cursor: "pointer", display: "flex", padding: "2px" }}
                        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = "#b91c1c"; }}
                        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = "#d1d5db"; }}
                      >
                        <IconTrash size={12} />
                      </button>
                    </div>
                  ))}
                </div>
            }
          </div>
        )}

        {/* Chat area */}
        <div style={{ flex: 1, paddingLeft: "40px", paddingTop: "32px", maxWidth: "840px", minWidth: 0, minHeight: hasMessages ? "auto" : "calc(100vh - 120px)", display: "flex", flexDirection: "column", justifyContent: hasMessages ? "flex-start" : "center" }}>
          {hasMessages && (
            <div style={{ marginBottom: "8px", display: "flex", alignItems: "center", gap: "8px" }}>
              {user && !historyOpen && (
                <button onClick={() => setHistoryOpen(true)} style={{ fontSize: "12px", color: "#9e9a90", background: "none", border: "none", cursor: "pointer", padding: "0", fontFamily: "inherit" }}>
                  ← History
                </button>
              )}
            </div>
          )}

          {hasMessages && (
            <div ref={scrollRef} style={{ marginBottom: "20px" }}>
              {messages.map((msg, idx) => (
                <div key={idx} style={{ marginBottom: "18px" }}>
                  {msg.role === "user" ? (
                    <div style={{ display: "flex", gap: "12px", background: "#f1f5f9", padding: "16px 18px", marginLeft: "-18px", marginRight: "-18px", borderLeft: "3px solid #228be6" }}>
                      <div style={{ color: "#228be6", fontWeight: 600, flexShrink: 0, fontSize: "16px" }}>&gt;</div>
                      <div style={{ color: "#1c1a17", fontSize: "16px", lineHeight: 1.65, whiteSpace: "pre-wrap", fontWeight: 500 }}>{msg.content}</div>
                    </div>
                  ) : (
                    <div style={{ display: "flex", gap: "12px", paddingLeft: "18px" }}>
                      <div style={{ color: "#b5b1a9", fontWeight: 400, flexShrink: 0, fontSize: "16px" }}>~</div>
                      <div style={{ color: "#3a3835", fontSize: "16px", lineHeight: 1.75, minWidth: 0 }}>
                        {renderAssistantMessage(msg, idx)}
                      </div>
                    </div>
                  )}
                </div>
              ))}
              {isWaiting && (
                <div style={{ display: "flex", gap: "10px", paddingLeft: "14px", marginBottom: "18px" }}>
                  <div style={{ color: "#9ca3af", fontWeight: 400, flexShrink: 0, fontSize: "14px" }}>~</div>
                  <div style={{ display: "flex", alignItems: "center", gap: "4px", paddingTop: "4px" }}>
                    <div style={{ width: "5px", height: "5px", borderRadius: "50%", background: "#9ca3af", animation: "thinking-dot 1.2s ease-in-out 0s infinite" }} />
                    <div style={{ width: "5px", height: "5px", borderRadius: "50%", background: "#9ca3af", animation: "thinking-dot 1.2s ease-in-out 0.2s infinite" }} />
                    <div style={{ width: "5px", height: "5px", borderRadius: "50%", background: "#9ca3af", animation: "thinking-dot 1.2s ease-in-out 0.4s infinite" }} />
                    <style>{`@keyframes thinking-dot { 0%,80%,100%{opacity:.2;transform:scale(.8)}40%{opacity:1;transform:scale(1)} }`}</style>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Input */}
          <div>
            <div style={{ display: "flex", gap: "12px", alignItems: "flex-start" }}>
              <div style={{ color: isStreaming ? "#d1d5db" : "#228be6", fontWeight: 500, fontSize: "16px", lineHeight: 1.65 }}>&gt;</div>
              <div style={{ flex: 1, position: "relative" }}>
                {!input && !hasMessages && (
                  <div style={{ position: "absolute", top: 0, left: 0, fontSize: "16px", lineHeight: 1.65, color: "#b5b1a9", pointerEvents: "none", fontStyle: "italic" }}>
                    {animatedPlaceholder}
                    <span style={{ display: "inline-block", width: "2px", height: "1em", background: "#9ca3af", marginLeft: "1px", verticalAlign: "text-bottom", animation: "blink 1s step-end infinite" }} />
                    <style>{`@keyframes blink{50%{opacity:0}}`}</style>
                  </div>
                )}
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => { setInput(e.target.value); autoResize(e.target); }}
                  onKeyDown={handleKeyDown}
                  disabled={isStreaming}
                  rows={1}
                  style={{ width: "100%", background: "transparent", border: "none", outline: "none", fontSize: "16px", lineHeight: 1.65, color: "#1c1a17", fontFamily: "inherit", resize: "none", padding: 0, opacity: isStreaming ? 0.5 : 1, overflow: "hidden", caretColor: (!input && !hasMessages) ? "transparent" : "#1c1a17" }}
                />
              </div>
            </div>
            {!hasMessages && <div style={{ marginTop: "14px", color: "#b5b1a9", fontSize: "12px" }}>Press Enter to send · Shift+Enter for new line</div>}
          </div>
        </div>
      </div>

      {/* Auth modal */}
      {showAuth && (
        <div onClick={() => setShowAuth(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.3)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ background: "#fff", padding: "32px", width: "360px", maxWidth: "90vw" }}>
            <h2 style={{ margin: "0 0 20px", fontSize: "18px", fontWeight: 600, color: "#1c1a17" }}>
              {authMode === "signin" ? "Sign in" : "Create account"}
            </h2>
            {authError && <div style={{ padding: "8px 12px", background: "#fef2f2", color: "#b91c1c", fontSize: "13px", marginBottom: "16px" }}>{authError}</div>}
            <form onSubmit={async (e) => {
              e.preventDefault();
              setAuthSubmitting(true);
              setAuthError(null);
              const { error } = authMode === "signin" ? await signIn(authEmail, authPassword) : await signUp(authEmail, authPassword);
              setAuthSubmitting(false);
              if (error) setAuthError(error);
              else { setShowAuth(false); setAuthEmail(""); setAuthPassword(""); }
            }}>
              <input type="email" placeholder="Email" value={authEmail} onChange={(e) => setAuthEmail(e.target.value)} required style={{ width: "100%", padding: "10px 12px", fontSize: "14px", border: "1px solid #e5e7eb", marginBottom: "10px", fontFamily: "inherit", boxSizing: "border-box" }} />
              <input type="password" placeholder="Password" value={authPassword} onChange={(e) => setAuthPassword(e.target.value)} required minLength={6} style={{ width: "100%", padding: "10px 12px", fontSize: "14px", border: "1px solid #e5e7eb", marginBottom: "16px", fontFamily: "inherit", boxSizing: "border-box" }} />
              <button type="submit" disabled={authSubmitting} style={{ width: "100%", padding: "10px", fontSize: "14px", background: "#228be6", color: "#fff", border: "none", cursor: authSubmitting ? "not-allowed" : "pointer", fontFamily: "inherit", opacity: authSubmitting ? 0.7 : 1 }}>
                {authSubmitting ? "..." : authMode === "signin" ? "Sign in" : "Create account"}
              </button>
            </form>
            <div style={{ marginTop: "16px", textAlign: "center", fontSize: "13px", color: "#6b7280" }}>
              {authMode === "signin" ? (
                <>No account? <button onClick={() => { setAuthMode("signup"); setAuthError(null); }} style={{ color: "#228be6", background: "none", border: "none", cursor: "pointer", fontFamily: "inherit", fontSize: "13px" }}>Create one</button></>
              ) : (
                <>Have an account? <button onClick={() => { setAuthMode("signin"); setAuthError(null); }} style={{ color: "#228be6", background: "none", border: "none", cursor: "pointer", fontFamily: "inherit", fontSize: "13px" }}>Sign in</button></>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
