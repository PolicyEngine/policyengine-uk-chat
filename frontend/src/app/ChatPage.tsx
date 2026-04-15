"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Loader } from "@mantine/core";
import { IconX, IconTrash, IconChevronDown, IconUser, IconLogout, IconShare, IconBug } from "@tabler/icons-react";
import { useAuth } from "@/utils/AuthContext";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Chart, extractChartSpecs, ChartSpec } from "@/components/charts";
import { THEME } from "@/components/theme";

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

interface ReportConversationResponse {
  share_token: string;
  share_url: string | null;
  issue_title: string;
  issue_body: string;
  issue_url: string;
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

type StreamEvent = { type: "text"; content: string; thinking?: boolean } | { type: "tool"; data: ToolData };

interface Message {
  role: "user" | "assistant";
  content: string;
  events?: StreamEvent[];
  isComplete?: boolean;
  cost_gbp?: number;
}

interface BalanceSummary {
  balance_gbp: number;
  free_tier_used_gbp: number;
  free_tier_remaining_gbp: number;
  spent_this_month_gbp: number;
  total_available_gbp: number;
}

async function apiRequest<T>(method: string, endpoint: string, params?: Record<string, string>, body?: unknown): Promise<T> {
  const backendBase = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";
  const url = new URL(`${backendBase}/${endpoint}`);
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const options: RequestInit = { method, headers: { "Content-Type": "application/json" } };
  if (body && ["POST", "PUT", "PATCH"].includes(method)) options.body = JSON.stringify(body);
  const res = await fetch(url.toString(), options);
  if (!res.ok) {
    let detail = "";
    try { const body = await res.json(); detail = body.details || body.error || ""; } catch {}
    throw new Error(`API error ${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export default function ChatPage() {
  const { user, loading: authLoading, signIn, signUp, signOut } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isWaiting, setIsWaiting] = useState(false);
  const [collapsedWorking, setCollapsedWorking] = useState<Set<number>>(new Set());
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [copiedSnippetId, setCopiedSnippetId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  const conversationCache = useRef<Map<number, ConversationDetail>>(new Map());
  const [showAuth, setShowAuth] = useState(false);
  const [authMode, setAuthMode] = useState<"signin" | "signup">("signin");
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportNote, setReportNote] = useState("");
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportSubmitting, setReportSubmitting] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const sessionId = useRef<string | null>(null);
  const debugLog = useRef<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const [modelVersion, setModelVersion] = useState<string | null>(null);
  const [balance, setBalance] = useState<BalanceSummary | null>(null);
  const [topUpLoading, setTopUpLoading] = useState(false);
  const hasMessages = messages.length > 0;
  const animatedPlaceholder = useAnimatedPlaceholder(EXAMPLE_QUERIES, !hasMessages && !input);

  const fetchBalance = useCallback(async () => {
    if (!user) return;
    const data = await apiRequest<BalanceSummary>("GET", "billing/balance", { user_id: user.id });
    setBalance(data);
  }, [user]);

  const handleTopUp = async (amount: number = 5) => {
    if (!user) return;
    setTopUpLoading(true);
    try {
      const { url } = await apiRequest<{ url: string }>("POST", "billing/checkout", undefined, { user_id: user.id, amount_gbp: amount });
      if (url) window.location.href = url;
    } catch (e) { console.error("Checkout failed", e); }
    finally { setTopUpLoading(false); }
  };

  useEffect(() => {
    apiRequest<{ policyengine_uk_compiled: string }>("GET", "version")
      .then((v) => setModelVersion(v.policyengine_uk_compiled))
      .catch(() => {});
    // Refresh balance after Stripe redirect
    if (typeof window !== "undefined" && new URLSearchParams(window.location.search).get("topup") === "success") {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  useEffect(() => { fetchBalance(); }, [fetchBalance]);

  useEffect(() => {
    inputRef.current?.focus();
    if (!authLoading && user) {
      apiRequest<ConversationSummary[]>("GET", "conversations", { user_id: user.id })
        .then((convs) => {
          setConversations(convs);
          // Preload conversation details in background
          convs.slice(0, 50).forEach((conv) => {
            apiRequest<ConversationDetail>("GET", `conversations/${conv.id}`)
              .then((data) => { conversationCache.current.set(conv.id, data); })
              .catch(() => {});
          });
        })
        .catch(() => {});
    } else if (!authLoading && !user) {
      setConversations([]);
      conversationCache.current.clear();
    }
  }, [user, authLoading]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [messages]);

  const loadConversation = async (conv: ConversationSummary) => {
    try {
      const data = conversationCache.current.get(conv.id) || await apiRequest<ConversationDetail>("GET", `conversations/${conv.id}`);
      if (!data?.messages?.length) { console.error("No messages in conversation", data); return; }
      const loaded: Message[] = data.messages.map((m) => ({ role: m.role as "user" | "assistant", content: m.content || "", isComplete: true, events: m.events }));
      const collapsed = new Set(loaded.map((m, i) => (m.role === "assistant" && m.events?.some((e) => e.type === "tool") ? i : -1)).filter((i) => i >= 0));
      sessionId.current = data.session_id;
      setActiveConversationId(data.id);
      setCollapsedWorking(collapsed);
      setMessages(loaded);
      setHistoryOpen(true);
    } catch (e) {
      console.error("Failed to load conversation", e);
      setMessages([{ role: "assistant", content: `Failed to load conversation: ${e instanceof Error ? e.message : "Unknown error"}` }]);
    }
  };

  const [copiedShareId, setCopiedShareId] = useState<number | null>(null);

  const shareConversation = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    try {
      const { share_token } = await apiRequest<{ share_token: string }>("POST", `conversations/${id}/share`, user?.id ? { user_id: user.id } : undefined);
      const url = `${window.location.origin}/s/${share_token}`;
      await navigator.clipboard.writeText(url);
      setCopiedShareId(id);
      setTimeout(() => setCopiedShareId(null), 2000);
    } catch (e) { console.error("Failed to share", e); }
  };

  const deleteConversation = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    try {
      await apiRequest("DELETE", `conversations/${id}`);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (activeConversationId === id) setActiveConversationId(null);
    } catch (e) { console.error(e); }
  };

  const saveConversation = useCallback(async (msgs: Message[], sid: string): Promise<ConversationDetail | null> => {
    const firstUserMsg = msgs.find((m) => m.role === "user");
    if (!firstUserMsg) return null;
    const firstAssistantMsg = msgs.find((m) => m.role === "assistant");
    const firstAssistantContent = (() => {
      if (!firstAssistantMsg?.isComplete || !firstAssistantMsg.events?.length) return firstAssistantMsg?.content;
      const lastToolIdx = firstAssistantMsg.events.reduce((acc, e, i) => e.type === "tool" ? i : acc, -1);
      if (lastToolIdx >= 0) return firstAssistantMsg.events.slice(lastToolIdx + 1).filter((e): e is { type: "text"; content: string } => e.type === "text").map((e) => e.content).join("") || firstAssistantMsg.content;
      return firstAssistantMsg.content;
    })();

    let title = firstUserMsg.content.slice(0, 60);
    try {
      const { title: generated } = await apiRequest<{ title: string }>("POST", "chat/title", undefined, { first_user_message: firstUserMsg.content, first_assistant_message: firstAssistantContent || "" });
      if (generated) title = generated;
    } catch (e) { console.error("Title generation failed", e); }

    const apiMessages = msgs.map((m) => {
      if (m.role === "assistant" && m.isComplete && m.events?.length) {
        const savedEvents = m.events.map((e) => e.type === "tool" ? { ...e, data: { ...e.data, result_summary: undefined } } : e);
        return { role: m.role, content: m.content, events: savedEvents };
      }
      return { role: m.role, content: m.content };
    });

    try {
      const saved = await apiRequest<ConversationDetail>("POST", "conversations", undefined, { session_id: sid, title, messages: apiMessages, user_id: user?.id, user_email: user?.email });
      setActiveConversationId(saved.id);
      conversationCache.current.set(saved.id, saved);
      setConversations((prev) => {
        const filtered = prev.filter((c) => c.session_id !== sid);
        return [{ id: saved.id, session_id: sid, title, created_at: saved.created_at, updated_at: saved.updated_at }, ...filtered];
      });
      return saved;
    } catch (e) { console.error("Failed to save conversation", e); }
    return null;
  }, [user]);

  const ensureConversationForReport = useCallback(async (): Promise<number | null> => {
    if (activeConversationId) return activeConversationId;
    if (!messages.length) return null;
    const sid = sessionId.current || crypto.randomUUID();
    sessionId.current = sid;
    const saved = await saveConversation(messages.map((m) => ({ ...m, isComplete: m.isComplete ?? true })), sid);
    return saved?.id ?? null;
  }, [activeConversationId, messages, saveConversation]);

  const submitReport = useCallback(async () => {
    setReportSubmitting(true);
    setReportError(null);
    try {
      const conversationId = await ensureConversationForReport();
      if (!conversationId) throw new Error("Could not save this thread for reporting.");
      const data = await apiRequest<ReportConversationResponse>("POST", `conversations/${conversationId}/report`, undefined, {
        user_id: user?.id,
        note: reportNote.trim() || null,
        app_url: window.location.origin,
      });
      window.open(data.issue_url, "_blank", "noopener,noreferrer");
      setReportOpen(false);
      setReportNote("");
    } catch (e) {
      setReportError(e instanceof Error ? e.message : "Failed to prepare issue");
    } finally {
      setReportSubmitting(false);
    }
  }, [ensureConversationForReport, reportNote, user]);

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
    let displayedText = "";
    let drainTimer: ReturnType<typeof setInterval> | null = null;
    const toolsMap = new Map<string, ToolData>();

    const updateMessage = () => {
      setMessages((prev) => {
        const newMsgs = [...prev];
        const lastIdx = newMsgs.length - 1;
        if (newMsgs[lastIdx]?.role === "assistant") newMsgs[lastIdx] = { role: "assistant", content: displayedText, events: [...events] };
        else newMsgs.push({ role: "assistant", content: displayedText, events: [...events] });
        return newMsgs;
      });
    };

    const startDrain = () => {
      if (drainTimer) return;
      drainTimer = setInterval(() => {
        if (displayedText.length >= currentText.length) {
          if (drainTimer) { clearInterval(drainTimer); drainTimer = null; }
          return;
        }
        const remaining = currentText.slice(displayedText.length);
        const match = remaining.match(/^(\s*\S+|\s+)/);
        const chunk = match ? match[0] : remaining[0];
        displayedText += chunk;
        // Rebuild displayed events: split displayedText across text events
        let charBudget = displayedText.length;
        const displayEvents: StreamEvent[] = events.map((e) => {
          if (e.type !== "text") return e;
          if (charBudget <= 0) return { ...e, content: "" };
          const shown = e.content.slice(0, charBudget);
          charBudget -= e.content.length;
          return { ...e, content: shown };
        }).filter((e) => e.type !== "text" || (e as { content: string }).content.length > 0);
        setMessages((prev) => {
          const newMsgs = [...prev];
          const lastIdx = newMsgs.length - 1;
          if (newMsgs[lastIdx]?.role === "assistant") newMsgs[lastIdx] = { role: "assistant", content: displayedText, events: [...displayEvents] };
          else newMsgs.push({ role: "assistant", content: displayedText, events: [...displayEvents] });
          return newMsgs;
        });
      }, 20);
    };

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // Hit the backend directly for SSE to avoid Next.js proxy buffering
      const backendBase = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8001";
      const response = await fetch(`${backendBase}/chat/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: apiMessages, session_id: sessionId.current, user_id: user?.id || null }),
        signal: controller.signal,
      });
      if (response.status === 402) {
        const err = await response.json().catch(() => ({ error: "No credit remaining" }));
        setMessages((prev) => [...prev, { role: "assistant", content: err.error || "No credit remaining. Please top up to continue." }]);
        setIsStreaming(false); setIsWaiting(false);
        return;
      }
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
              if (lastEvent?.type === "text" && !lastEvent.thinking) lastEvent.content += data.content;
              else events.push({ type: "text", content: data.content });
              currentText += data.content;
              startDrain();
            } else if (data.type === "thinking_done") {
              // No-op: position-based split handles CoT placement
            } else if (data.type === "tool_start") {
              setIsWaiting(false);
              // Flush any pending text before showing tool
              if (drainTimer) { clearInterval(drainTimer); drainTimer = null; }
              displayedText = currentText;
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
              // Flush remaining text
              if (drainTimer) { clearInterval(drainTimer); drainTimer = null; }
              displayedText = currentText;
              updateMessage();
              if (data.session_id) sessionId.current = data.session_id;
              const msgCost = typeof data.cost_gbp === "number" ? data.cost_gbp : undefined;
              const hasTools = events.some((e) => e.type === "tool");
              if (hasTools) {
                setMessages((prev) => {
                  const newMsgs = [...prev];
                  const lastIdx = newMsgs.length - 1;
                  if (newMsgs[lastIdx]?.role === "assistant") newMsgs[lastIdx] = { ...newMsgs[lastIdx], isComplete: true, cost_gbp: msgCost };
                  setCollapsedWorking((c) => new Set(c).add(lastIdx));
                  return newMsgs;
                });
              } else {
                setMessages((prev) => {
                  const newMsgs = [...prev];
                  const lastIdx = newMsgs.length - 1;
                  if (newMsgs[lastIdx]?.role === "assistant") newMsgs[lastIdx] = { ...newMsgs[lastIdx], cost_gbp: msgCost };
                  return newMsgs;
                });
              }
              if (data.session_id) {
                const finalMsgs = [...allMessages, { role: "assistant" as const, content: currentText, isComplete: true, events: [...events] }];
                saveConversation(finalMsgs, data.session_id);
              }
              if (data.balance) setBalance(data.balance);
              else fetchBalance();
              setTimeout(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }), 100);
            } else if (data.type === "error") {
              const errorText = `Error: ${data.content || "Something went wrong"}`;
              const lastEvent = events[events.length - 1];
              if (lastEvent?.type === "text") lastEvent.content += "\n\n" + errorText;
              else events.push({ type: "text", content: errorText });
              currentText += errorText;
              if (drainTimer) { clearInterval(drainTimer); drainTimer = null; }
              displayedText = currentText;
              updateMessage();
            }
          } catch {}
        }
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        // User stopped the stream — flush what we have
        if (drainTimer) { clearInterval(drainTimer); drainTimer = null; }
        displayedText = currentText;
        updateMessage();
        setMessages((prev) => {
          const newMsgs = [...prev];
          const lastIdx = newMsgs.length - 1;
          if (newMsgs[lastIdx]?.role === "assistant") newMsgs[lastIdx] = { ...newMsgs[lastIdx], isComplete: true };
          return newMsgs;
        });
      } else {
        setMessages((prev) => [...prev, { role: "assistant", content: `Something went wrong: ${error instanceof Error ? error.message : "Unknown error"}` }]);
      }
    } finally {
      abortRef.current = null;
      if (drainTimer) { clearInterval(drainTimer); drainTimer = null; }
      setIsStreaming(false);
      setIsWaiting(false);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  };

  const stopStreaming = () => { abortRef.current?.abort(); };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  const autoResize = (el: HTMLTextAreaElement) => { el.style.height = "auto"; el.style.height = el.scrollHeight + "px"; };

  const formatToolSummary = (summary: string): string => {
    // If it looks like raw JSON, just show the tool completed
    if (summary.startsWith("{") || summary.startsWith("[") || summary.startsWith("'")) return "done";
    // Otherwise truncate to something readable
    return summary.length > 50 ? summary.slice(0, 50) + "…" : summary;
  };

  const toggleTool = (toolId: string) => {
    setExpandedTools((prev) => { const next = new Set(prev); if (next.has(toolId)) next.delete(toolId); else next.add(toolId); return next; });
  };

  const copySnippet = async (snippetId: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedSnippetId(snippetId);
      setTimeout(() => setCopiedSnippetId((current) => current === snippetId ? null : current), 2000);
    } catch (error) {
      console.error("Failed to copy snippet", error);
    }
  };

  const renderToolDetails = (t: ToolData) => {
    const isPython = t.tool_name === "run_python";
    const codeStyle = { margin: 0, padding: "8px 10px", background: "#1a1917", color: "#c9c5bc", whiteSpace: "pre-wrap" as const, wordBreak: "break-word" as const, maxHeight: "300px", overflow: "auto" as const, fontSize: "11px", lineHeight: 1.7, fontFamily: "'JetBrains Mono', monospace" };
    const copyButtonStyle = { fontSize: "10px", color: THEME.primary, background: "none", border: "none", cursor: "pointer", padding: 0, fontFamily: "'JetBrains Mono', monospace" } as const;

    // For run_python: show code as Python, parse result from summary
    if (isPython) {
      const code = (t.input as Record<string, string>)?.code || "";
      let output = "";
      if (t.result_summary) {
        try {
          const parsed = JSON.parse(t.result_summary);
          const parts: string[] = [];
          if (parsed.output) parts.push(parsed.output);
          if (parsed.result !== undefined && parsed.result !== null) parts.push(`result = ${JSON.stringify(parsed.result, null, 2)}`);
          if (parsed.error) parts.push(`Error: ${parsed.error}`);
          output = parts.join("\n") || t.result_summary;
        } catch { output = t.result_summary; }
      }
      return (
        <div style={{ marginLeft: "18px", marginTop: "4px" }}>
          {code && (
            <div style={{ marginBottom: "6px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                <div style={{ color: THEME.muted, fontSize: "10px", fontFamily: "'JetBrains Mono', monospace" }}>python</div>
                <button onClick={() => copySnippet(`${t.tool_id}-code`, code)} style={copyButtonStyle}>
                  {copiedSnippetId === `${t.tool_id}-code` ? "copied" : "copy code"}
                </button>
              </div>
              <pre style={{ ...codeStyle, borderLeft: `2px solid ${THEME.border}` }}>{code}</pre>
            </div>
          )}
          {output && (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                <div style={{ color: THEME.muted, fontSize: "10px", fontFamily: "'JetBrains Mono', monospace" }}>output</div>
                <button onClick={() => copySnippet(`${t.tool_id}-output`, output)} style={copyButtonStyle}>
                  {copiedSnippetId === `${t.tool_id}-output` ? "copied" : "copy output"}
                </button>
              </div>
              <pre style={{ ...codeStyle, borderLeft: `2px solid ${THEME.primary}` }}>{output.length > 2000 ? output.slice(0, 2000) + "…" : output}</pre>
            </div>
          )}
        </div>
      );
    }

    // For other tools: show JSON input/output
    const inputStr = t.input ? JSON.stringify(t.input, null, 2) : "";
    const outputStr = t.result_summary || "";
    return (
      <div style={{ marginLeft: "18px", marginTop: "4px" }}>
        {inputStr && (
          <div style={{ marginBottom: "6px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "2px" }}>
              <div style={{ color: THEME.muted, fontSize: "10px", fontFamily: "'JetBrains Mono', monospace" }}>input</div>
              <button onClick={() => copySnippet(`${t.tool_id}-input`, inputStr)} style={copyButtonStyle}>
                {copiedSnippetId === `${t.tool_id}-input` ? "copied" : "copy input"}
              </button>
            </div>
            <pre style={{ ...codeStyle, background: "#f5f4f2", color: THEME.text2, borderLeft: `2px solid ${THEME.border}` }}>{inputStr.length > 2000 ? inputStr.slice(0, 2000) + "…" : inputStr}</pre>
          </div>
        )}
        {outputStr && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "2px" }}>
              <div style={{ color: THEME.muted, fontSize: "10px", fontFamily: "'JetBrains Mono', monospace" }}>output</div>
              <button onClick={() => copySnippet(`${t.tool_id}-output`, outputStr)} style={copyButtonStyle}>
                {copiedSnippetId === `${t.tool_id}-output` ? "copied" : "copy output"}
              </button>
            </div>
            <pre style={{ ...codeStyle, background: "#f5f4f2", color: THEME.text2, borderLeft: `2px solid ${THEME.primary}` }}>{outputStr.length > 2000 ? outputStr.slice(0, 2000) + "…" : outputStr}</pre>
          </div>
        )}
      </div>
    );
  };

  const renderTool = (t: ToolData) => {
    const isExpanded = expandedTools.has(t.tool_id);
    const hasDetails = t.input || t.result_summary;
    return (
      <div key={t.tool_id} style={{ margin: "2px 0" }}>
        <div
          onClick={hasDetails ? () => toggleTool(t.tool_id) : undefined}
          style={{ display: "inline-flex", alignItems: "center", gap: "5px", fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: "11px", color: THEME.muted, padding: "2px 0", cursor: hasDetails ? "pointer" : "default" }}
        >
          {t.status === "pending" && <Loader size={10} color={THEME.primary} />}
          {hasDetails && <IconChevronDown size={10} style={{ opacity: 0.4, transform: isExpanded ? "none" : "rotate(-90deg)", transition: "transform 0.15s" }} />}
          <span style={{ color: THEME.text3 }}>{t.tool_name === "run_python" ? "python" : t.tool_name}</span>
          {t.status !== "pending" && <span style={{ color: THEME.muted }}>✓</span>}
        </div>
        {isExpanded && hasDetails && renderToolDetails(t)}
      </div>
    );
  };

  const renderMarkdown = (content: string) => {
    const { charts, cleanContent } = extractChartSpecs(content);

    const markdownComponents = {
      code({ className, children, ...props }: { className?: string; children?: React.ReactNode; [key: string]: unknown }) {
        const match = /language-(\w+)/.exec(className || "");
        const isInline = !match && !String(children).includes("\n");
        if (!isInline && match) return <SyntaxHighlighter style={oneDark} language={match[1]} customStyle={{ margin: "12px 0", fontSize: "12px", lineHeight: 1.7, background: "#1a1917", border: "none", borderRadius: 0, borderLeft: `3px solid ${THEME.primary}`, padding: "16px 18px" }}>{String(children).replace(/\n$/, "")}</SyntaxHighlighter>;
        if (isInline) return <code style={{ background: "#f0f0f0", padding: "2px 5px", fontSize: "13px" }}>{children}</code>;
        return <pre style={{ display: "block", margin: "12px 0", lineHeight: 1.7, whiteSpace: "pre-wrap", background: "#1a1917", color: "#c9c5bc", padding: "16px 18px", borderLeft: `3px solid ${THEME.primary}`, fontFamily: "'JetBrains Mono', monospace", fontSize: "12px" }}><code>{children}</code></pre>;
      },
      p: ({ children }: { children?: React.ReactNode }) => <p style={{ margin: "0 0 14px 0", lineHeight: 1.75 }}>{children}</p>,
      strong: ({ children }: { children?: React.ReactNode }) => <strong style={{ fontWeight: 600, color: THEME.text }}>{children}</strong>,
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

    const hasChartPlaceholder = cleanContent.includes("[CHART_PLACEHOLDER_") || cleanContent.includes("[CHART_LOADING]");
    if (!hasChartPlaceholder) {
      return <ReactMarkdown remarkPlugins={[[remarkGfm, { singleTilde: false }]]} components={markdownComponents as never}>{cleanContent}</ReactMarkdown>;
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
            return <ReactMarkdown key={idx} remarkPlugins={[[remarkGfm, { singleTilde: false }]]} components={markdownComponents as never}>{segment.content}</ReactMarkdown>;
          }
          if (segment.type === "loading") return <div key={idx} style={{ margin: "16px 0", padding: "40px", background: "#f9fafb", border: "1px solid #e5e7eb", display: "flex", alignItems: "center", justifyContent: "center", gap: "10px", color: "#9ca3af", fontSize: "13px" }}><Loader size={14} color={THEME.primary} /><span>Generating chart…</span></div>;
          if (segment.type === "chart" && segment.chartIdx !== undefined) {
            const chart = charts[segment.chartIdx];
            if (chart) return <div key={idx} style={{ margin: "16px 0" }}><Chart spec={chart} width={680} height={400} /></div>;
          }
          return null;
        })}
      </>
    );
  };

  /** Return a fixed label for the collapsed working section. */
  const getWorkingSummary = (_events: StreamEvent[]): string => "Worked through the problem";

  /** Check if a text event is transitional CoT that shouldn't appear in final output. */
  const isTransitionalText = (text: string): boolean => {
    const trimmed = text.trim();
    if (!trimmed || trimmed.length > 200) return false;
    // Short sentences starting with transitional phrases
    return /^(let me|now I|I'll|I need to|I can|I should|I want to|good\.|great\.|ok\b|alright|perfect|right|so |now let|let's)/i.test(trimmed);
  };

  const renderAssistantMessage = (msg: Message, msgIdx: number) => {
    if (!msg.events?.length) return renderMarkdown(msg.content);

    const lastToolIdx = msg.events.reduce((acc, e, idx) => e.type === "tool" ? idx : acc, -1);
    const hasTools = lastToolIdx >= 0;
    const isWorkingCollapsed = collapsedWorking.has(msgIdx);

    // During streaming: if tools exist, ALL events go into the working section
    // so text doesn't jump between output→working when new tool calls arrive.
    // On completion: position-based split — everything up to last tool = working,
    // everything after = final output.
    let workingEvents: StreamEvent[];
    let finalEvents: StreamEvent[];

    if (msg.isComplete && hasTools) {
      workingEvents = msg.events.slice(0, lastToolIdx + 1);
      const rawFinal = msg.events.slice(lastToolIdx + 1);
      finalEvents = rawFinal.filter((e) => e.type === "text" && !isTransitionalText(e.content));
    } else if (!msg.isComplete && hasTools) {
      // Streaming with tools: everything in working, nothing in output yet
      workingEvents = [...msg.events];
      finalEvents = [];
    } else {
      workingEvents = [];
      finalEvents = [...msg.events];
    }

    const toggleWorking = () => setCollapsedWorking((prev) => { const next = new Set(prev); if (next.has(msgIdx)) next.delete(msgIdx); else next.add(msgIdx); return next; });
    const summary = hasTools ? getWorkingSummary(workingEvents) : "";

    return (
      <>
        {hasTools && (
          <>
            <div onClick={toggleWorking} style={{ display: "flex", alignItems: "baseline", gap: "6px", color: THEME.muted, fontSize: "12px", cursor: "pointer", userSelect: "none", margin: "6px 0", padding: "2px 0" }}>
              <IconChevronDown size={12} style={{ opacity: 0.5, transform: isWorkingCollapsed ? "rotate(-90deg)" : "none", transition: "transform 0.15s", flexShrink: 0, position: "relative", top: "1px" }} />
              <span style={{ color: THEME.text3, fontStyle: "italic" }}>{summary || "Working\u2026"}</span>
            </div>
            {!isWorkingCollapsed && (
              <div style={{ margin: "8px 0 16px", paddingLeft: "4px", borderLeft: `2px solid ${THEME.border}` }}>
                <div style={{ paddingLeft: "14px" }}>
                  {workingEvents.map((event, idx) =>
                    event.type === "text"
                      ? <div key={idx} style={{ fontStyle: "italic", opacity: 0.6, fontSize: "13px", margin: "6px 0" }}>{renderMarkdown(event.content)}</div>
                      : <div key={idx} style={{ margin: "6px 0" }}>{renderTool(event.data)}</div>
                  )}
                </div>
              </div>
            )}
          </>
        )}
        {finalEvents.map((event, idx) =>
          event.type === "text"
            ? <div key={idx} style={{ margin: "6px 0" }}>{renderMarkdown(event.content)}</div>
            : <div key={idx} style={{ margin: "6px 0" }}>{renderTool(event.data)}</div>
        )}
      </>
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
              <button onClick={startNewChat} style={{ fontSize: "13px", color: THEME.primary, cursor: "pointer", padding: "5px 12px", border: `1px solid ${THEME.primary}`, background: "transparent", fontFamily: "inherit" }}>
                New chat
              </button>
            )}
            {user ? (
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                {balance && (
                  <span style={{ fontSize: "12px", color: balance.total_available_gbp > 0.5 ? "#6b7280" : "#b91c1c", fontVariantNumeric: "tabular-nums" }}>
                    {balance.total_available_gbp <= 0 ? "No credit" : `£${balance.total_available_gbp.toFixed(3)} remaining`}
                  </span>
                )}
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
                    <div key={conv.id} onClick={() => loadConversation(conv)} style={{ padding: "10px 12px", cursor: "pointer", background: activeConversationId === conv.id ? THEME.primaryLight : "transparent", borderLeft: activeConversationId === conv.id ? `2px solid ${THEME.primary}` : "2px solid transparent", display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "8px" }}
                      onMouseEnter={(e) => { if (activeConversationId !== conv.id) (e.currentTarget as HTMLElement).style.background = "#f9fafb"; }}
                      onMouseLeave={(e) => { if (activeConversationId !== conv.id) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: "14px", color: "#1c1a17", lineHeight: 1.45 }}>{conv.title}</div>
                        <div style={{ fontSize: "12px", color: "#9e9a90", marginTop: "4px" }}>{formatRelativeTime(conv.updated_at)}</div>
                      </div>
                      <div style={{ display: "flex", gap: "4px", flexShrink: 0 }}>
                        <button onClick={(e) => shareConversation(e, conv.id)} title={copiedShareId === conv.id ? "Link copied" : "Share"} style={{ background: "none", border: "none", color: copiedShareId === conv.id ? THEME.primary : "#d1d5db", cursor: "pointer", display: "flex", padding: "2px" }}
                          onMouseEnter={(e) => { if (copiedShareId !== conv.id) (e.currentTarget as HTMLElement).style.color = THEME.primary; }}
                          onMouseLeave={(e) => { if (copiedShareId !== conv.id) (e.currentTarget as HTMLElement).style.color = "#d1d5db"; }}
                        >
                          <IconShare size={12} />
                        </button>
                        <button onClick={(e) => deleteConversation(e, conv.id)} title="Delete" style={{ background: "none", border: "none", color: "#d1d5db", cursor: "pointer", display: "flex", padding: "2px" }}
                          onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = "#b91c1c"; }}
                          onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = "#d1d5db"; }}
                        >
                          <IconTrash size={12} />
                        </button>
                      </div>
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
              <button
                onClick={() => { setReportError(null); setReportOpen(true); }}
                disabled={isStreaming}
                style={{ fontSize: "12px", color: isStreaming ? "#d1d5db" : "#9e9a90", background: "none", border: "none", cursor: isStreaming ? "not-allowed" : "pointer", padding: "0", fontFamily: "inherit", display: "inline-flex", alignItems: "center", gap: "5px" }}
                title="Report this thread"
              >
                <IconBug size={12} />
                Report issue
              </button>
            </div>
          )}

          {hasMessages && (
            <div ref={scrollRef} style={{ marginBottom: "20px" }}>
              {messages.map((msg, idx) => (
                <div key={idx} style={{ marginBottom: "18px" }}>
                  {msg.role === "user" ? (
                    <div style={{ display: "flex", gap: "14px", padding: "14px 0", borderBottom: "1px solid #e5e7eb" }}>
                      <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: "12px", fontWeight: 500, color: "#fff", background: THEME.primary, width: "24px", height: "24px", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>&gt;</div>
                      <div style={{ color: "#1c1a17", fontSize: "15px", lineHeight: 1.6, whiteSpace: "pre-wrap", fontWeight: 500, letterSpacing: "-0.01em" }}>{msg.content}</div>
                    </div>
                  ) : (
                    <div style={{ padding: "18px 0 14px" }}>
                      <div className={!msg.isComplete ? "streaming-text" : undefined} style={{ fontFamily: "'Source Serif 4', Georgia, serif", color: "#3a3835", fontSize: "15.5px", lineHeight: 1.8, minWidth: 0 }}>
                        {renderAssistantMessage(msg, idx)}
                      </div>
                      {msg.cost_gbp !== undefined && (
                        <div style={{ fontSize: "11px", color: "#d1cdc4", marginTop: "4px", fontVariantNumeric: "tabular-nums" }}>
                          {msg.cost_gbp < 0.01 ? `${(msg.cost_gbp * 100).toFixed(2)}p` : `£${msg.cost_gbp.toFixed(3)}`}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
              {isWaiting && (
                <div style={{ padding: "18px 0 14px", marginBottom: "18px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "5px" }}>
                    <div style={{ width: "4px", height: "4px", borderRadius: "50%", background: "#9e9a90", animation: "thinking-dot 1.2s ease-in-out 0s infinite" }} />
                    <div style={{ width: "4px", height: "4px", borderRadius: "50%", background: "#9e9a90", animation: "thinking-dot 1.2s ease-in-out 0.2s infinite" }} />
                    <div style={{ width: "4px", height: "4px", borderRadius: "50%", background: "#9e9a90", animation: "thinking-dot 1.2s ease-in-out 0.4s infinite" }} />
                    <style>{`@keyframes thinking-dot { 0%,80%,100%{opacity:.2;transform:scale(.8)}40%{opacity:1;transform:scale(1)} }
@keyframes blurIn { from{opacity:0;filter:blur(3px)}to{opacity:1;filter:blur(0)} }
.streaming-text > div:last-child > :last-child { animation: blurIn 400ms both; }
.streaming-text > div:last-child > :last-child > :last-child { animation: blurIn 400ms both; }
`}</style>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Input */}
          <div>
            {isStreaming && (
              <div style={{ display: "flex", justifyContent: "center", marginBottom: "12px" }}>
                <button onClick={stopStreaming} style={{ fontSize: "12px", color: THEME.muted, cursor: "pointer", padding: "5px 14px", border: `1px solid ${THEME.border}`, background: "#fff", fontFamily: "inherit", display: "flex", alignItems: "center", gap: "6px" }}>
                  <IconX size={12} /> Stop
                </button>
              </div>
            )}
            <div style={{ display: "flex", gap: "12px", alignItems: "flex-start" }}>
              <div style={{ color: isStreaming ? "#d1d5db" : THEME.primary, fontWeight: 500, fontSize: "16px", lineHeight: 1.65 }}>&gt;</div>
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
            {!hasMessages && (
              <div style={{ marginTop: "14px", color: "#b5b1a9", fontSize: "12px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span>Press Enter to send · Shift+Enter for new line</span>
                {modelVersion && <span style={{ fontSize: "11px", color: "#d1cdc4" }}>policyengine-uk v{modelVersion}</span>}
              </div>
            )}
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
              <button type="submit" disabled={authSubmitting} style={{ width: "100%", padding: "10px", fontSize: "14px", background: THEME.primaryGradient, color: "#fff", border: "none", cursor: authSubmitting ? "not-allowed" : "pointer", fontFamily: "inherit", opacity: authSubmitting ? 0.7 : 1 }}>
                {authSubmitting ? "..." : authMode === "signin" ? "Sign in" : "Create account"}
              </button>
            </form>
            <div style={{ marginTop: "16px", textAlign: "center", fontSize: "13px", color: "#6b7280" }}>
              {authMode === "signin" ? (
                <>No account? <button onClick={() => { setAuthMode("signup"); setAuthError(null); }} style={{ color: THEME.primary, background: "none", border: "none", cursor: "pointer", fontFamily: "inherit", fontSize: "13px" }}>Create one</button></>
              ) : (
                <>Have an account? <button onClick={() => { setAuthMode("signin"); setAuthError(null); }} style={{ color: THEME.primary, background: "none", border: "none", cursor: "pointer", fontFamily: "inherit", fontSize: "13px" }}>Sign in</button></>
              )}
            </div>
          </div>
        </div>
      )}

      {reportOpen && (
        <div onClick={() => !reportSubmitting && setReportOpen(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.25)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ background: "#fff", padding: "28px", width: "520px", maxWidth: "92vw", border: `1px solid ${THEME.border}` }}>
            <h2 style={{ margin: "0 0 10px", fontSize: "18px", fontWeight: 600, color: THEME.text }}>Report this thread</h2>
            <p style={{ margin: "0 0 14px", fontSize: "14px", lineHeight: 1.6, color: THEME.text3 }}>
              This will open a prefilled GitHub issue with a link to the shared thread and the most relevant parts of the conversation so we can debug it later.
            </p>
            <textarea
              value={reportNote}
              onChange={(e) => setReportNote(e.target.value)}
              placeholder="What looks off? For example: the budget impact seems too high, the answer ignored Scotland, or the explanation contradicts the chart."
              rows={5}
              style={{ width: "100%", padding: "12px", fontSize: "14px", border: `1px solid ${THEME.border}`, fontFamily: "inherit", boxSizing: "border-box", resize: "vertical", color: THEME.text, lineHeight: 1.5 }}
            />
            {reportError && (
              <div style={{ marginTop: "10px", fontSize: "13px", color: "#b91c1c" }}>{reportError}</div>
            )}
            <div style={{ marginTop: "16px", display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button
                onClick={() => setReportOpen(false)}
                disabled={reportSubmitting}
                style={{ fontSize: "13px", padding: "8px 12px", border: `1px solid ${THEME.border}`, background: "#fff", color: THEME.text3, cursor: reportSubmitting ? "not-allowed" : "pointer", fontFamily: "inherit" }}
              >
                Cancel
              </button>
              <button
                onClick={submitReport}
                disabled={reportSubmitting}
                style={{ fontSize: "13px", padding: "8px 12px", border: "none", background: THEME.primaryGradient, color: "#fff", cursor: reportSubmitting ? "not-allowed" : "pointer", fontFamily: "inherit", display: "inline-flex", alignItems: "center", gap: "6px", opacity: reportSubmitting ? 0.7 : 1 }}
              >
                {reportSubmitting ? <Loader size={12} color="#fff" /> : <IconBug size={13} />}
                Open GitHub issue
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
