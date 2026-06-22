"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Loader } from "@mantine/core";
import { IconX, IconTrash, IconChevronDown, IconUser, IconLogout, IconShare, IconBug, IconSun, IconMoon, IconArrowUp, IconPlus, IconMessage, IconEdit, IconCopy, IconDownload, IconChartBar, IconPaperclip } from "@tabler/icons-react";
import { useAuth } from "@/utils/AuthContext";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Chart, extractChartSpecs, ChartSpec } from "@/components/charts";
import { THEME } from "@/components/theme";
import { getBackendEndpoint } from "@/utils/backend";

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

type SlashCommand = {
  name: string;        // without leading slash, e.g. "plan"
  description: string; // one-line description for the menu
  kind: "action" | "fill";
  // For "fill" commands, the textarea value to set on select.
  fillText?: string;
};

// v1 slash commands.
const SLASH_COMMANDS: SlashCommand[] = [
  { name: "charts", description: "Toggle Charts mode on/off",         kind: "action" },
  { name: "new",   description: "Start a new chat",                  kind: "action" },
  { name: "clear", description: "Start a new chat (alias for /new)", kind: "action" },
  { name: "help",  description: "Insert a starter prompt",           kind: "fill", fillText: "Help me understand " },
];

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
  /** Anthropic stop_reason on the final iteration. "max_tokens" means truncated. */
  stop_reason?: string;
  /** True when the user clicked Stop and the stream was aborted mid-flight. */
  stopped?: boolean;
  /** Optional 2–3 follow-up question suggestions generated after the turn. */
  suggestions?: string[];
}

interface BalanceSummary {
  balance_gbp: number;
  free_tier_used_gbp: number;
  free_tier_remaining_gbp: number;
  spent_this_month_gbp: number;
  total_available_gbp: number;
}

// ---- Export helpers ---------------------------------------------------------

const stripChartPlaceholders = (text: string): string =>
  text.replace(/\[CHART_PLACEHOLDER_\d+\]/g, "[Chart]").replace(/\[CHART_LOADING\]/g, "[Chart]");

const formatPythonOutput = (summary?: string): string => {
  if (!summary) return "";
  try {
    const parsed = JSON.parse(summary);
    const parts: string[] = [];
    if (parsed.output) parts.push(parsed.output);
    if (parsed.result !== undefined && parsed.result !== null) parts.push(`result = ${JSON.stringify(parsed.result, null, 2)}`);
    if (parsed.error) parts.push(`Error: ${parsed.error}`);
    return parts.join("\n") || summary;
  } catch {
    return summary;
  }
};

/** Check if a text event is transitional CoT that shouldn't appear in final output. */
const isTransitionalText = (text: string): boolean => {
  const trimmed = text.trim();
  if (!trimmed || trimmed.length > 200) return false;
  // Short sentences starting with transitional phrases
  return /^(let me|now I|I'll|I need to|I can|I should|I want to|good\.|great\.|ok\b|alright|perfect|right|so |now let|let's)/i.test(trimmed);
};

/**
 * Text events the user actually sees as the final answer, matching renderAssistantMessage's
 * split: no-tool messages keep all text; tool-using messages keep only post-last-tool text
 * with transitional filler dropped. Single source of truth for render, copy, and export.
 */
const visibleFinalEvents = (msg: Message): { type: "text"; content: string }[] => {
  if (!msg.events?.length) return [];
  const lastToolIdx = msg.events.reduce((acc, e, idx) => e.type === "tool" ? idx : acc, -1);
  const textEvents = (evs: StreamEvent[]) =>
    evs.filter((e): e is { type: "text"; content: string } => e.type === "text");
  if (lastToolIdx < 0) return textEvents(msg.events);
  return textEvents(msg.events.slice(lastToolIdx + 1)).filter((e) => !isTransitionalText(e.content));
};

/** Final user-facing prose of an assistant message — what the user reads, charts as [Chart]. */
const extractFinalProse = (msg: Message): string => {
  if (msg.role === "user") return msg.content;
  if (!msg.events?.length) return stripChartPlaceholders(msg.content);
  return stripChartPlaceholders(visibleFinalEvents(msg).map((e) => e.content).join(""));
};

/** Full record of a single message — includes working section and tool blocks for assistants. */
const messageToMarkdown = (msg: Message): string => {
  if (msg.role === "user") return `## You\n\n${msg.content}`;
  const parts: string[] = ["## Assistant\n"];
  if (msg.events?.length) {
    const lastToolIdx = msg.events.reduce((acc, e, idx) => e.type === "tool" ? idx : acc, -1);
    if (lastToolIdx >= 0) {
      parts.push("\n<details>\n<summary>Worked through the problem</summary>\n");
      for (const event of msg.events.slice(0, lastToolIdx + 1)) {
        if (event.type === "text" && event.content.trim()) {
          parts.push(`\n${stripChartPlaceholders(event.content)}\n`);
        } else if (event.type === "tool") {
          const t = event.data;
          if (t.tool_name === "run_python") {
            const code = (t.input as Record<string, string>)?.code || "";
            const output = formatPythonOutput(t.result_summary);
            if (code) parts.push(`\n\`\`\`python\n${code}\n\`\`\`\n`);
            if (output) parts.push(`\n\`\`\`\n${output}\n\`\`\`\n`);
          } else {
            const inputStr = t.input ? JSON.stringify(t.input, null, 2) : "";
            if (inputStr) parts.push(`\n**${t.tool_name} input**\n\`\`\`json\n${inputStr}\n\`\`\`\n`);
            if (t.result_summary) parts.push(`\n**${t.tool_name} output**\n\`\`\`\n${t.result_summary}\n\`\`\`\n`);
          }
        }
      }
      parts.push("\n</details>\n");
    }
    const finalText = visibleFinalEvents(msg).map((e) => e.content).join("");
    if (finalText.trim()) parts.push(`\n${stripChartPlaceholders(finalText)}\n`);
  } else if (msg.content) {
    parts.push(`\n${stripChartPlaceholders(msg.content)}\n`);
  }
  return parts.join("");
};

const conversationToMarkdown = (msgs: Message[], title?: string): string => {
  const header = title ? `# ${title}\n\n` : "";
  return header + msgs.map(messageToMarkdown).join("\n\n---\n\n") + "\n";
};

const slugify = (s: string): string =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "chat";

// ---- /Export helpers --------------------------------------------------------

async function apiRequest<T>(method: string, endpoint: string, params?: Record<string, string>, body?: unknown): Promise<T> {
  const url = new URL(getBackendEndpoint(endpoint), window.location.origin);
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
  const [copiedMessageIdx, setCopiedMessageIdx] = useState<number | null>(null);
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
  const [chartsMode, setChartsMode] = useState(false);
  const [slashIndex, setSlashIndex] = useState(0);
  const slashMenuRef = useRef<HTMLDivElement>(null);
  // Image attachment for the next user message. Stored as the full data URL
  // (`data:image/png;base64,...`) so the thumbnail can be rendered directly
  // via <img src>. We split off the prefix before sending to the backend.
  const [attachedImage, setAttachedImage] = useState<{ dataUrl: string; mediaType: string; name: string } | null>(null);
  const [attachError, setAttachError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  useEffect(() => {
    const stored = typeof window !== "undefined" ? localStorage.getItem("theme") : null;
    const initial: "light" | "dark" = stored === "dark" ? "dark" : "light";
    setTheme(initial);
    if (typeof document !== "undefined") {
      if (initial === "dark") document.documentElement.setAttribute("data-theme", "dark");
      else document.documentElement.removeAttribute("data-theme");
    }
  }, []);
  const toggleTheme = () => {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    try { localStorage.setItem("theme", next); } catch {}
    if (next === "dark") document.documentElement.setAttribute("data-theme", "dark");
    else document.documentElement.removeAttribute("data-theme");
  };
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const sessionId = useRef<string | null>(null);
  const debugLog = useRef<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const draftRestoredRef = useRef(false);

  // Restore draft from localStorage on initial mount only. Runs once, so it
  // can't interfere with later state changes (streaming, conversation loads).
  useEffect(() => {
    if (draftRestoredRef.current) return;
    draftRestoredRef.current = true;
    if (typeof window === "undefined") return;
    try {
      const saved = localStorage.getItem("policyengine-uk-chat:draft");
      if (saved) {
        setInput(saved);
        // Defer autoResize until after React commits the value so scrollHeight
        // reflects the restored content.
        setTimeout(() => {
          const el = inputRef.current;
          if (el) { el.style.height = "auto"; el.style.height = el.scrollHeight + "px"; }
        }, 0);
      }
    } catch {}
  }, []);

  // Persist draft on every change. Only writes after the initial restore has
  // run so we don't overwrite a saved draft with the empty initial state.
  useEffect(() => {
    if (!draftRestoredRef.current) return;
    if (typeof window === "undefined") return;
    try {
      if (input) localStorage.setItem("policyengine-uk-chat:draft", input);
      else localStorage.removeItem("policyengine-uk-chat:draft");
    } catch {}
  }, [input]);

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

  // iOS Safari does not honor `interactive-widget=resizes-content`; it uses the
  // visual-viewport API instead. When the soft keyboard slides up, the visual
  // viewport shrinks. If the textarea is currently focused, nudge it back into
  // view so the user can see what they're typing.
  useEffect(() => {
    if (typeof window === "undefined" || !window.visualViewport) return;
    const vv = window.visualViewport;
    const onResize = () => {
      if (document.activeElement === inputRef.current) {
        inputRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    };
    vv.addEventListener("resize", onResize);
    return () => vv.removeEventListener("resize", onResize);
  }, []);

  const loadConversation = async (conv: ConversationSummary) => {
    try {
      const data = conversationCache.current.get(conv.id) || await apiRequest<ConversationDetail>("GET", `conversations/${conv.id}`);
      if (!data?.messages?.length) { console.error("No messages in conversation", data); return; }
      const loaded: Message[] = data.messages.map((m) => {
        const raw = m as { role: string; content: string; events?: StreamEvent[]; stop_reason?: string; stopped?: boolean; cost_gbp?: number; suggestions?: string[] };
        return {
          role: raw.role as "user" | "assistant",
          content: raw.content || "",
          isComplete: true,
          events: raw.events,
          stop_reason: raw.stop_reason,
          stopped: raw.stopped,
          cost_gbp: raw.cost_gbp,
          suggestions: Array.isArray(raw.suggestions) ? raw.suggestions : undefined,
        };
      });
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
      const base: Record<string, unknown> = { role: m.role, content: m.content };
      if (m.role === "assistant") {
        if (m.isComplete && m.events?.length) base.events = m.events;
        if (m.cost_gbp !== undefined) base.cost_gbp = m.cost_gbp;
        if (m.stop_reason) base.stop_reason = m.stop_reason;
        if (m.stopped) base.stopped = true;
        if (m.suggestions?.length) base.suggestions = m.suggestions;
      }
      return base;
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
    if ((!input.trim() && !attachedImage) || isStreaming) return;
    // If the user attached an image but didn't type anything, give the model
    // a minimal nudge so the request still has a coherent user turn.
    const displayContent = input.trim() || (attachedImage ? `[Attached image: ${attachedImage.name}]` : "");
    const userMessage: Message = { role: "user", content: displayContent };
    const allMessages = [...messages, userMessage];
    setMessages((prev) => [...prev, userMessage]);
    if (messages.length === 0 && user) setHistoryOpen(true);
    // Snapshot the attachment for this send, then clear it from the input.
    const sendingImage = attachedImage;
    setInput("");
    setAttachedImage(null);
    setAttachError(null);
    if (typeof window !== "undefined") {
      try { localStorage.removeItem("policyengine-uk-chat:draft"); } catch {}
    }
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
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (user?.id) headers["X-User-Id"] = user.id;
      // The image is sent as raw base64 (no `data:image/...;base64,` prefix)
      // alongside its media type. The backend re-wraps it into Anthropic's
      // vision content-block shape on the latest user message.
      const imagePayload = sendingImage
        ? {
            image_base64: sendingImage.dataUrl.replace(/^data:[^;]+;base64,/, ""),
            image_media_type: sendingImage.mediaType,
          }
        : {};
      const response = await fetch(getBackendEndpoint("chat/message"), {
        method: "POST",
        headers,
        body: JSON.stringify({ messages: apiMessages, session_id: sessionId.current, user_id: user?.id || null, charts_mode: chartsMode, ...imagePayload }),
        signal: controller.signal,
      });
      if (response.status === 402) {
        const err = await response.json().catch(() => ({ error: "No credit remaining" }));
        setMessages((prev) => [...prev, { role: "assistant", content: err.error || "No credit remaining. Please top up to continue." }]);
        setIsStreaming(false); setIsWaiting(false);
        return;
      }
      if (response.status === 429) {
        const retryAfterHeader = response.headers.get("retry-after");
        const seconds = retryAfterHeader ? parseInt(retryAfterHeader, 10) : 60;
        setMessages((prev) => [...prev, { role: "assistant", content: `You're sending messages a bit fast — please wait ~${seconds}s and try again.`, isComplete: true }]);
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
              const stopReason = typeof data.stop_reason === "string" ? data.stop_reason : undefined;
              const hasTools = events.some((e) => e.type === "tool");
              if (hasTools) {
                setMessages((prev) => {
                  const newMsgs = [...prev];
                  const lastIdx = newMsgs.length - 1;
                  if (newMsgs[lastIdx]?.role === "assistant") newMsgs[lastIdx] = { ...newMsgs[lastIdx], isComplete: true, cost_gbp: msgCost, stop_reason: stopReason };
                  setCollapsedWorking((c) => new Set(c).add(lastIdx));
                  return newMsgs;
                });
              } else {
                setMessages((prev) => {
                  const newMsgs = [...prev];
                  const lastIdx = newMsgs.length - 1;
                  if (newMsgs[lastIdx]?.role === "assistant") newMsgs[lastIdx] = { ...newMsgs[lastIdx], isComplete: true, cost_gbp: msgCost, stop_reason: stopReason };
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
            } else if (data.type === "suggestions") {
              // Best-effort follow-up chips. Backend may emit this AFTER the
              // `done` event (the turn is already complete by the time these
              // arrive). Silently ignore malformed payloads.
              const raw = Array.isArray(data.suggestions) ? data.suggestions : [];
              const cleaned = raw.filter((s: unknown): s is string => typeof s === "string" && s.trim().length > 0).slice(0, 3);
              if (cleaned.length) {
                setMessages((prev) => {
                  const newMsgs = [...prev];
                  const lastIdx = newMsgs.length - 1;
                  if (newMsgs[lastIdx]?.role === "assistant") {
                    newMsgs[lastIdx] = { ...newMsgs[lastIdx], suggestions: cleaned };
                  }
                  return newMsgs;
                });
                // Persist suggestions alongside the saved conversation.
                if (sessionId.current) {
                  const finalMsgs = [
                    ...allMessages,
                    { role: "assistant" as const, content: currentText, isComplete: true, events: [...events], suggestions: cleaned },
                  ];
                  saveConversation(finalMsgs, sessionId.current);
                }
              }
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
        // User stopped the stream — flush what we have, mark `stopped`
        // so the UI shows a Continue affordance.
        if (drainTimer) { clearInterval(drainTimer); drainTimer = null; }
        displayedText = currentText;
        updateMessage();
        setMessages((prev) => {
          const newMsgs = [...prev];
          const lastIdx = newMsgs.length - 1;
          if (newMsgs[lastIdx]?.role === "assistant") newMsgs[lastIdx] = { ...newMsgs[lastIdx], isComplete: true, stopped: true };
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

  /** Resume a previously truncated or stopped assistant turn.
   *
   * The conversation up to and including the partial assistant message is
   * sent to /chat/message; the model continues from there via Anthropic's
   * assistant-prefill behaviour (no extra user nudge needed). New text and
   * events are appended into the SAME message bubble so the user sees one
   * continuous answer.
   */
  const continueMessage = async (idx: number) => {
    if (isStreaming) return;
    const target = messages[idx];
    if (!target || target.role !== "assistant" || !target.isComplete) return;
    // Refuse if a tool is still pending in this message — re-triggering
    // would orphan the partial tool call.
    if (target.events?.some((e) => e.type === "tool" && e.data.status === "pending")) return;

    const priorMessages = messages.slice(0, idx + 1);
    const apiMessages = priorMessages.map((msg) => {
      let content = msg.content;
      if (msg.role === "assistant" && msg.events) {
        const toolResults = msg.events.filter((e): e is { type: "tool"; data: ToolData } => e.type === "tool" && !!e.data.result_summary).map((e) => `[Tool: ${e.data.tool_name}] ${e.data.result_summary}`).join("\n\n");
        if (toolResults) content += "\n\n---\nTool results:\n" + toolResults;
      }
      return { role: msg.role, content };
    });

    // The partial turn is sent as the final message so the model continues it
    // (assistant prefill). Anthropic rejects a prefill that is empty or ends
    // with whitespace — trim it, and bail if there is nothing to continue from.
    const prefill = apiMessages[apiMessages.length - 1];
    prefill.content = prefill.content.trimEnd();
    if (!prefill.content) return;

    // Optimistic: clear truncation/stopped flags and mark in-flight.
    setMessages((prev) => prev.map((m, i) => i === idx ? { ...m, isComplete: false, stop_reason: undefined, stopped: undefined } : m));
    setIsStreaming(true);
    setIsWaiting(true);

    const controller = new AbortController();
    abortRef.current = controller;

    let appendedText = "";
    const newEvents: StreamEvent[] = [];
    const toolsMap = new Map<string, ToolData>();
    const baseContent = target.content;
    const baseEvents = target.events ? [...target.events] : [];
    const baseCost = target.cost_gbp || 0;

    const flushTarget = () => {
      setMessages((prev) => prev.map((m, i) => i === idx ? {
        ...m,
        content: baseContent + appendedText,
        events: [...baseEvents, ...newEvents],
      } : m));
    };

    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (user?.id) headers["X-User-Id"] = user.id;
      const response = await fetch(getBackendEndpoint("chat/message"), {
        method: "POST",
        headers,
        body: JSON.stringify({ messages: apiMessages, session_id: sessionId.current, user_id: user?.id || null, charts_mode: chartsMode }),
        signal: controller.signal,
      });
      if (response.status === 402) {
        const err = await response.json().catch(() => ({ error: "No credit remaining" }));
        setMessages((prev) => [...prev, { role: "assistant", content: err.error || "No credit remaining. Please top up to continue.", isComplete: true }]);
        return;
      }
      if (response.status === 429) {
        const seconds = parseInt(response.headers.get("retry-after") || "60", 10);
        setMessages((prev) => [...prev, { role: "assistant", content: `You're sending messages a bit fast — please wait ~${seconds}s and try again.`, isComplete: true }]);
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
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === "chunk") {
              setIsWaiting(false);
              const lastEvent = newEvents[newEvents.length - 1];
              if (lastEvent?.type === "text" && !lastEvent.thinking) lastEvent.content += data.content;
              else newEvents.push({ type: "text", content: data.content });
              appendedText += data.content;
              flushTarget();
            } else if (data.type === "tool_start") {
              setIsWaiting(false);
              const toolData: ToolData = { tool_name: data.tool_name, tool_id: data.tool_id, status: "pending" };
              toolsMap.set(data.tool_id, toolData);
              newEvents.push({ type: "tool", data: toolData });
              flushTarget();
            } else if (data.type === "tool_use") {
              const existing = toolsMap.get(data.tool_id);
              if (existing) existing.input = data.tool_input;
              else { const td: ToolData = { tool_name: data.tool_name, tool_id: data.tool_id, status: "pending", input: data.tool_input }; toolsMap.set(data.tool_id, td); newEvents.push({ type: "tool", data: td }); }
              flushTarget();
            } else if (data.type === "tool_result") {
              const tool = toolsMap.get(data.tool_id);
              if (tool) { tool.status = data.status; tool.result_summary = data.result_summary; }
              flushTarget();
              setIsWaiting(true);
            } else if (data.type === "done") {
              setIsWaiting(false);
              const newCost = typeof data.cost_gbp === "number" ? data.cost_gbp : 0;
              const stopReason = typeof data.stop_reason === "string" ? data.stop_reason : undefined;
              const finalContent = baseContent + appendedText;
              const finalEvents = [...baseEvents, ...newEvents];
              setMessages((prev) => prev.map((m, i) => i === idx ? {
                ...m,
                isComplete: true,
                content: finalContent,
                events: finalEvents,
                cost_gbp: baseCost + newCost,
                stop_reason: stopReason,
                stopped: false,
              } : m));
              if (data.balance) setBalance(data.balance); else fetchBalance();
              if (sessionId.current) {
                // Persist the fresh post-continuation metadata too — otherwise a
                // reload restores the stale truncation flag and pre-continuation
                // cost, making the resumed turn look like it never continued.
                const savedMessages = messages.map((m, i) => i === idx ? {
                  ...m,
                  isComplete: true,
                  content: finalContent,
                  events: finalEvents,
                  cost_gbp: baseCost + newCost,
                  stop_reason: stopReason,
                  stopped: false,
                } : m);
                saveConversation(savedMessages, sessionId.current);
              }
            } else if (data.type === "suggestions") {
              const raw = Array.isArray(data.suggestions) ? data.suggestions : [];
              const cleaned = raw.filter((s: unknown): s is string => typeof s === "string" && s.trim().length > 0).slice(0, 3);
              if (cleaned.length) {
                setMessages((prev) => prev.map((m, i) => i === idx ? { ...m, suggestions: cleaned } : m));
              }
            } else if (data.type === "error") {
              const errorText = `Error: ${data.content || "Something went wrong"}`;
              const lastEvent = newEvents[newEvents.length - 1];
              if (lastEvent?.type === "text") lastEvent.content += "\n\n" + errorText;
              else newEvents.push({ type: "text", content: errorText });
              appendedText += errorText;
              flushTarget();
            }
          } catch {}
        }
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setMessages((prev) => prev.map((m, i) => i === idx ? {
          ...m,
          isComplete: true,
          content: baseContent + appendedText,
          events: [...baseEvents, ...newEvents],
          stopped: true,
        } : m));
      } else {
        const errorText = `Continuation failed: ${error instanceof Error ? error.message : "Unknown error"}`;
        setMessages((prev) => prev.map((m, i) => i === idx ? {
          ...m,
          isComplete: true,
          content: baseContent + appendedText + "\n\n" + errorText,
          events: [...baseEvents, ...newEvents],
        } : m));
      }
    } finally {
      abortRef.current = null;
      setIsStreaming(false);
      setIsWaiting(false);
    }
  };

  // ---- Slash-command menu ---------------------------------------------------
  // Open whenever the input starts with "/" and at least one command matches the
  // characters typed after the slash. If nothing matches, hide silently so a
  // real user message starting with "/" doesn't get a stuck empty popup.
  const slashQuery = input.startsWith("/") ? input.slice(1).toLowerCase() : null;
  const filteredSlashCommands = slashQuery === null
    ? []
    : SLASH_COMMANDS.filter((c) => c.name.startsWith(slashQuery));
  const slashOpen = slashQuery !== null && filteredSlashCommands.length > 0;

  // Keep the selected index inside the filtered range as the query changes.
  useEffect(() => {
    if (slashIndex >= filteredSlashCommands.length) setSlashIndex(0);
  }, [filteredSlashCommands.length, slashIndex]);

  const closeSlash = useCallback(() => {
    setInput("");
    setSlashIndex(0);
    const el = inputRef.current;
    if (el) { el.style.height = "auto"; }
  }, []);

  const selectSlashCommand = useCallback((cmd: SlashCommand) => {
    if (cmd.kind === "action") {
      if (cmd.name === "charts") {
        setChartsMode((v) => !v);
        closeSlash();
      } else if (cmd.name === "new" || cmd.name === "clear") {
        // startNewChat already clears input + refocuses, but call closeSlash
        // first so we don't leave a "/new" string sitting around if anything
        // changes about startNewChat in the future.
        closeSlash();
        startNewChat();
      }
      return;
    }
    if (cmd.kind === "fill" && cmd.fillText !== undefined) {
      setInput(cmd.fillText);
      setSlashIndex(0);
      // Refocus + caret at end + resize after React commits the new value.
      setTimeout(() => {
        const el = inputRef.current;
        if (!el) return;
        el.focus();
        const end = el.value.length;
        try { el.setSelectionRange(end, end); } catch {}
        el.style.height = "auto";
        el.style.height = el.scrollHeight + "px";
      }, 0);
    }
  }, [closeSlash]);

  // Outside-click clears the slash text and closes the menu (Esc-equivalent).
  useEffect(() => {
    if (!slashOpen) return;
    const onDocPointerDown = (ev: MouseEvent) => {
      const menu = slashMenuRef.current;
      const ta = inputRef.current;
      const target = ev.target as Node | null;
      if (!target) return;
      if (menu && menu.contains(target)) return;
      if (ta && ta.contains(target)) return;
      setSlashIndex(0);
      setInput("");
    };
    document.addEventListener("mousedown", onDocPointerDown);
    return () => document.removeEventListener("mousedown", onDocPointerDown);
  }, [slashOpen]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (slashOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIndex((i) => (i + 1) % filteredSlashCommands.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIndex((i) => (i - 1 + filteredSlashCommands.length) % filteredSlashCommands.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const cmd = filteredSlashCommands[Math.min(slashIndex, filteredSlashCommands.length - 1)];
        if (cmd) selectSlashCommand(cmd);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closeSlash();
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
  const ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"];

  const showAttachError = (msg: string) => {
    setAttachError(msg);
    setTimeout(() => setAttachError((prev) => (prev === msg ? null : prev)), 4000);
  };

  const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset the input value so picking the same file twice still fires onChange.
    e.target.value = "";
    if (!file) return;
    if (!ALLOWED_IMAGE_TYPES.includes(file.type)) {
      showAttachError("Only JPG, PNG, WEBP, or GIF images are supported.");
      return;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      showAttachError("Image is larger than 5MB. Please attach a smaller file.");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = typeof reader.result === "string" ? reader.result : "";
      if (!dataUrl) {
        showAttachError("Could not read that image. Please try again.");
        return;
      }
      setAttachedImage({ dataUrl, mediaType: file.type, name: file.name });
    };
    reader.onerror = () => showAttachError("Could not read that image. Please try again.");
    reader.readAsDataURL(file);
  };

  const autoResize = (el: HTMLTextAreaElement) => {
    // Cap height at ~10 lines (16px font * 1.5 line-height * 10) so a long paste
    // can't push the chat transcript off-screen; scroll inside the textarea above the cap.
    const MAX_HEIGHT = 240;
    el.style.height = "auto";
    const next = el.scrollHeight;
    if (next > MAX_HEIGHT) {
      el.style.height = MAX_HEIGHT + "px";
      el.style.overflowY = "auto";
    } else {
      el.style.height = next + "px";
      el.style.overflowY = "hidden";
    }
  };

  /** Fill the textarea with a follow-up suggestion and focus it. Does NOT
   * send — the user reviews, edits if they want, then presses Enter. */
  const useSuggestion = (text: string) => {
    if (isStreaming) return;
    setInput(text);
    setTimeout(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      autoResize(el);
      // Place cursor at end so further typing extends the suggestion.
      const len = el.value.length;
      try { el.setSelectionRange(len, len); } catch {}
    }, 0);
  };

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

  const copyMessage = async (idx: number) => {
    const msg = messages[idx];
    if (!msg) return;
    const prose = extractFinalProse(msg);
    if (!prose.trim()) return;
    try {
      await navigator.clipboard.writeText(prose);
      setCopiedMessageIdx(idx);
      setTimeout(() => setCopiedMessageIdx((current) => current === idx ? null : current), 2000);
    } catch (error) {
      console.error("Failed to copy message", error);
    }
  };

  const downloadConversation = () => {
    if (!messages.length) return;
    const title = activeConversationId
      ? conversations.find((c) => c.id === activeConversationId)?.title
      : undefined;
    const md = conversationToMarkdown(messages, title);
    const stamp = new Date().toISOString().slice(0, 10);
    const slug = title ? `-${slugify(title)}` : "";
    const filename = `policyengine-chat-${stamp}${slug}.md`;
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
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
            <pre style={{ ...codeStyle, background: "var(--surface-2)", color: THEME.text2, borderLeft: `2px solid ${THEME.border}` }}>{inputStr.length > 2000 ? inputStr.slice(0, 2000) + "…" : inputStr}</pre>
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
            <pre style={{ ...codeStyle, background: "var(--surface-2)", color: THEME.text2, borderLeft: `2px solid ${THEME.primary}` }}>{outputStr.length > 2000 ? outputStr.slice(0, 2000) + "…" : outputStr}</pre>
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
          {t.status === "pending" && <Loader size={10} color="#8e8e8e" />}
          {hasDetails && <IconChevronDown size={10} style={{ opacity: 0.4, transform: isExpanded ? "none" : "rotate(-90deg)", transition: "transform 0.15s" }} />}
          <span style={{ color: THEME.text3 }}>{({
            run_python: "python",
            calculate_household: "household sim",
            validate_reform: "reform validation",
            run_economy_simulation: "economy sim",
            analyse_microdata: "microdata analysis",
          } as Record<string, string>)[t.tool_name] ?? t.tool_name}</span>
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
        if (!isInline && match) return <SyntaxHighlighter style={oneDark} language={match[1]} customStyle={{ margin: "12px 0", fontSize: "12px", lineHeight: 1.7, background: "#1a1917", border: "none", borderRadius: "8px", padding: "16px 18px" }}>{String(children).replace(/\n$/, "")}</SyntaxHighlighter>;
        if (isInline) return <code style={{ background: "var(--surface-2)", color: "var(--text)", padding: "2px 5px", fontSize: "13px", borderRadius: "4px" }}>{children}</code>;
        return <pre style={{ display: "block", margin: "12px 0", lineHeight: 1.7, whiteSpace: "pre-wrap", background: "#1a1917", color: "#c9c5bc", padding: "16px 18px", borderRadius: "8px", fontFamily: "'JetBrains Mono', monospace", fontSize: "12px" }}><code>{children}</code></pre>;
      },
      p: ({ children }: { children?: React.ReactNode }) => <p style={{ margin: "0 0 14px 0", lineHeight: 1.75 }}>{children}</p>,
      strong: ({ children }: { children?: React.ReactNode }) => <strong style={{ fontWeight: 600, color: "var(--text)" }}>{children}</strong>,
      ul: ({ children }: { children?: React.ReactNode }) => <ul style={{ margin: "0 0 14px 0", paddingLeft: "22px", listStyleType: "disc" }}>{children}</ul>,
      ol: ({ children }: { children?: React.ReactNode }) => <ol style={{ margin: "0 0 14px 0", paddingLeft: "22px", listStyleType: "decimal" }}>{children}</ol>,
      li: ({ children }: { children?: React.ReactNode }) => <li style={{ marginBottom: "5px", lineHeight: 1.65, listStyleType: "inherit" }}>{children}</li>,
      h1: ({ children }: { children?: React.ReactNode }) => <h1 style={{ fontSize: "20px", fontWeight: 600, margin: "22px 0 10px", color: "var(--text)" }}>{children}</h1>,
      h2: ({ children }: { children?: React.ReactNode }) => <h2 style={{ fontSize: "18px", fontWeight: 600, margin: "20px 0 8px", color: "var(--text)" }}>{children}</h2>,
      h3: ({ children }: { children?: React.ReactNode }) => <h3 style={{ fontSize: "16px", fontWeight: 600, margin: "16px 0 6px", color: "var(--text)" }}>{children}</h3>,
      table: ({ children }: { children?: React.ReactNode }) => <table style={{ margin: "14px 0", borderCollapse: "collapse", fontSize: "14px", width: "100%" }}>{children}</table>,
      thead: ({ children }: { children?: React.ReactNode }) => <thead>{children}</thead>,
      tbody: ({ children }: { children?: React.ReactNode }) => <tbody>{children}</tbody>,
      tr: ({ children, ...props }: { children?: React.ReactNode }) => {
        const node = props as { node?: { position?: { start?: { line?: number } } } };
        const rowIndex = node?.node?.position?.start?.line ?? 0;
        return <tr style={{ borderBottom: "1px solid var(--border-light)", background: rowIndex % 2 === 0 ? "var(--surface-2)" : "transparent" }}>{children}</tr>;
      },
      th: ({ children }: { children?: React.ReactNode }) => <th style={{ padding: "10px 14px", textAlign: "left", fontSize: "12px", fontWeight: 600, color: "var(--muted)", borderBottom: "1px solid var(--border)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{children}</th>,
      td: ({ children }: { children?: React.ReactNode }) => <td style={{ padding: "9px 14px", color: "var(--text-2)", fontSize: "14px" }}>{children}</td>,
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
          if (segment.type === "loading") return <div key={idx} style={{ margin: "16px 0", padding: "40px", background: "var(--surface-2)", border: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "center", gap: "10px", color: "var(--muted)", fontSize: "13px" }}><Loader size={14} color="#8e8e8e" /><span>Generating chart…</span></div>;
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
      finalEvents = visibleFinalEvents(msg);
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
    <div style={{ minHeight: "100dvh", background: "var(--bg)", color: "var(--text)", fontFamily: "system-ui, -apple-system, sans-serif" }}>
      <style>{`
        [data-tip],[data-tip-right]{position:relative}
        [data-tip]::after,[data-tip-right]::after{
          position:absolute;
          background:var(--text); color:var(--bg);
          padding:4px 8px; border-radius:6px; font-size:11px; line-height:1.3;
          white-space:normal; max-width:240px; width:max-content; text-align:center;
          opacity:0; pointer-events:none; z-index:60;
          transition:opacity 60ms ease;
        }
        [data-tip]::after{
          content:attr(data-tip);
          left:50%; top:calc(100% + 6px); transform:translateX(-50%);
        }
        [data-tip-right]::after{
          content:attr(data-tip-right);
          left:calc(100% + 8px); top:50%; transform:translateY(-50%);
        }
        [data-tip]:hover::after,[data-tip-right]:hover::after{opacity:1}
      `}</style>
      {/* Body */}
      <div style={{ display: "flex", margin: "0 auto", padding: "0", gap: "0", width: "100%", minHeight: "100dvh" }}>
        {!isEmbed && !historyOpen && (
          /* Rail */
          <div data-pe-sidebar style={{ width: "60px", flexShrink: 0, background: "var(--sidebar-bg)", borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", alignItems: "center", padding: "10px 0", position: "sticky", top: 0, height: "100dvh", boxSizing: "border-box" }}>
            <button onClick={() => setHistoryOpen(true)} data-tip-right="Open sidebar" aria-label="Open sidebar" style={{ background: "transparent", border: "none", cursor: "pointer", padding: "8px", borderRadius: "10px", display: "flex", color: "var(--text)", marginBottom: "4px" }}
              onMouseEnter={(e) => (e.currentTarget as HTMLElement).style.background = "var(--surface-hover)"}
              onMouseLeave={(e) => (e.currentTarget as HTMLElement).style.background = "transparent"}
            >
              <span role="img" aria-label="PolicyEngine" style={{ display: "inline-block", width: "24px", height: "24px", background: "var(--text)", WebkitMaskImage: "url(/favicon.svg)", maskImage: "url(/favicon.svg)", WebkitMaskRepeat: "no-repeat", maskRepeat: "no-repeat", WebkitMaskSize: "contain", maskSize: "contain", WebkitMaskPosition: "center", maskPosition: "center" }} />
            </button>
            <button onClick={startNewChat} data-tip-right="New chat" aria-label="New chat" style={{ background: "transparent", border: "none", cursor: "pointer", padding: "10px", borderRadius: "10px", display: "flex", color: "var(--text-2)", marginTop: "4px" }}
              onMouseEnter={(e) => (e.currentTarget as HTMLElement).style.background = "var(--surface-hover)"}
              onMouseLeave={(e) => (e.currentTarget as HTMLElement).style.background = "transparent"}
            >
              <IconEdit size={20} />
            </button>
            <button onClick={() => setHistoryOpen(true)} data-tip-right="Chats" aria-label="Chats" style={{ background: "transparent", border: "none", cursor: "pointer", padding: "10px", borderRadius: "10px", display: "flex", color: "var(--text-2)" }}
              onMouseEnter={(e) => (e.currentTarget as HTMLElement).style.background = "var(--surface-hover)"}
              onMouseLeave={(e) => (e.currentTarget as HTMLElement).style.background = "transparent"}
            >
              <IconMessage size={20} />
            </button>
            <div style={{ flex: 1 }} />
            <button onClick={toggleTheme} data-tip-right={theme === "light" ? "Switch to dark" : "Switch to light"} aria-label={theme === "light" ? "Switch to dark" : "Switch to light"} style={{ background: "transparent", border: "none", cursor: "pointer", padding: "10px", borderRadius: "10px", display: "flex", color: "var(--text-2)", marginBottom: "6px" }}
              onMouseEnter={(e) => (e.currentTarget as HTMLElement).style.background = "var(--surface-hover)"}
              onMouseLeave={(e) => (e.currentTarget as HTMLElement).style.background = "transparent"}
            >
              {theme === "light" ? <IconMoon size={18} /> : <IconSun size={18} />}
            </button>
            {user ? (
              <button onClick={signOut} data-tip-right={`${user.email} — sign out`} aria-label={`${user.email} — sign out`} style={{ background: "var(--accent)", color: "var(--accent-fg)", border: "none", cursor: "pointer", width: "32px", height: "32px", borderRadius: "999px", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "13px", fontWeight: 600, textTransform: "uppercase" }}>
                {(user.email || "?").charAt(0)}
              </button>
            ) : (
              <button onClick={() => { setShowAuth(true); setAuthError(null); }} data-tip-right="Sign in" aria-label="Sign in" style={{ background: "transparent", border: "1px solid var(--border)", cursor: "pointer", padding: "8px", borderRadius: "999px", display: "flex", color: "var(--text-2)" }}>
                <IconUser size={16} />
              </button>
            )}
          </div>
        )}
        {/* Sidebar */}
        {!isEmbed && historyOpen && (
          <div data-pe-sidebar style={{ width: "260px", flexShrink: 0, background: "var(--sidebar-bg)", borderRight: "1px solid var(--border)", padding: "12px 8px", position: "sticky", top: 0, height: "100dvh", alignSelf: "flex-start", display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "4px", gap: "4px" }}>
              <button onClick={startNewChat} style={{ flex: 1, fontSize: "14px", color: "var(--text)", cursor: "pointer", padding: "10px 12px", border: "none", borderRadius: "10px", background: "transparent", fontFamily: "inherit", display: "inline-flex", alignItems: "center", gap: "10px", fontWeight: 500, justifyContent: "flex-start" }}
                onMouseEnter={(e) => (e.currentTarget as HTMLElement).style.background = "var(--surface-hover)"}
                onMouseLeave={(e) => (e.currentTarget as HTMLElement).style.background = "transparent"}
              >
                <IconPlus size={16} /> New chat
              </button>
              <button onClick={() => setHistoryOpen(false)} data-tip="Collapse sidebar" aria-label="Collapse sidebar" style={{ background: "transparent", border: "none", borderRadius: "8px", cursor: "pointer", color: "var(--muted)", display: "flex", padding: "8px" }}
                onMouseEnter={(e) => (e.currentTarget as HTMLElement).style.background = "var(--surface-hover)"}
                onMouseLeave={(e) => (e.currentTarget as HTMLElement).style.background = "transparent"}
              >
                <IconX size={16} />
              </button>
            </div>
            <div style={{ flex: "1 1 auto", minHeight: 0, overflowY: "auto", paddingTop: "8px" }}>
              <div style={{ fontSize: "11px", color: "var(--muted)", letterSpacing: "0.04em", textTransform: "uppercase", fontWeight: 600, marginBottom: "6px", paddingLeft: "10px" }}>Chats</div>
              {!user ? (
                <div style={{ fontSize: "13px", color: "var(--muted)", padding: "8px 10px", lineHeight: 1.5 }}>
                  <button onClick={() => { setShowAuth(true); setAuthError(null); }} style={{ color: "var(--text)", background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: "inherit", fontSize: "13px", textDecoration: "underline" }}>Sign in</button>
                  {" to save your chats."}
                </div>
              ) : conversations.length === 0 ? (
                <div style={{ fontSize: "13px", color: "var(--muted)", fontStyle: "italic", padding: "8px 10px" }}>No previous chats</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "1px" }}>
                  {conversations.map((conv) => {
                    const isActive = activeConversationId === conv.id;
                    return (
                    <div key={conv.id} onClick={() => loadConversation(conv)} style={{ padding: "8px 10px", cursor: "pointer", background: isActive ? "var(--surface-hover)" : "transparent", borderRadius: "8px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}
                      onMouseEnter={(e) => { if (!isActive) (e.currentTarget as HTMLElement).style.background = "var(--surface-hover)"; }}
                      onMouseLeave={(e) => { if (!isActive) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
                    >
                      <div style={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
                        <div style={{ fontSize: "14px", color: "var(--text)", lineHeight: 1.4, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{conv.title}</div>
                      </div>
                      <div style={{ display: "flex", gap: "2px", flexShrink: 0, opacity: 0.6 }}>
                        <button onClick={(e) => shareConversation(e, conv.id)} data-tip={copiedShareId === conv.id ? "Link copied" : "Share"} aria-label="Share" style={{ background: "none", border: "none", color: copiedShareId === conv.id ? "var(--accent)" : "var(--muted)", cursor: "pointer", display: "flex", padding: "4px", borderRadius: "4px" }}
                          onMouseEnter={(e) => { if (copiedShareId !== conv.id) (e.currentTarget as HTMLElement).style.color = "var(--text)"; }}
                          onMouseLeave={(e) => { if (copiedShareId !== conv.id) (e.currentTarget as HTMLElement).style.color = "var(--muted)"; }}
                        >
                          <IconShare size={12} />
                        </button>
                        <button onClick={(e) => deleteConversation(e, conv.id)} data-tip="Delete" aria-label="Delete" style={{ background: "none", border: "none", color: "var(--muted)", cursor: "pointer", display: "flex", padding: "4px", borderRadius: "4px" }}
                          onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = "#ef4444"; }}
                          onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = "var(--muted)"; }}
                        >
                          <IconTrash size={12} />
                        </button>
                      </div>
                    </div>
                    );
                  })}
                </div>
              )}
            </div>
            <div style={{ borderTop: "1px solid var(--border)", paddingTop: "10px" }}>
              {user ? (
                <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "8px 10px", borderRadius: "10px" }}>
                  <div style={{ width: "28px", height: "28px", borderRadius: "999px", background: "var(--accent)", color: "var(--accent-fg)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px", fontWeight: 600, flexShrink: 0, textTransform: "uppercase" }}>
                    {(user.email || "?").slice(0, 1)}
                  </div>
                  <div style={{ flex: 1, minWidth: 0, fontSize: "13px", color: "var(--text)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{user.email}</div>
                  <button onClick={signOut} data-tip="Sign out" aria-label="Sign out" style={{ background: "none", border: "none", cursor: "pointer", color: "var(--muted)", display: "flex", padding: "4px" }}>
                    <IconLogout size={14} />
                  </button>
                </div>
              ) : (
                <button onClick={() => { setShowAuth(true); setAuthError(null); }} style={{ width: "100%", fontSize: "13px", color: "var(--text)", cursor: "pointer", padding: "10px 12px", border: "none", borderRadius: "10px", background: "transparent", fontFamily: "inherit", display: "inline-flex", alignItems: "center", gap: "10px", justifyContent: "flex-start" }}
                  onMouseEnter={(e) => (e.currentTarget as HTMLElement).style.background = "var(--surface-hover)"}
                  onMouseLeave={(e) => (e.currentTarget as HTMLElement).style.background = "transparent"}
                >
                  <IconUser size={16} /> Sign in
                </button>
              )}
            </div>
            {modelVersion && (
              <div style={{ paddingTop: "8px", textAlign: "center", color: "var(--faint)", fontSize: "11px" }}>
                policyengine-uk v{modelVersion}
              </div>
            )}
          </div>
        )}

        {/* Chat area */}
        <div data-pe-chat style={{ flex: 1, padding: "0 24px", paddingTop: "16px", paddingBottom: "24px", minWidth: 0, minHeight: hasMessages ? "auto" : "calc(100vh - 120px)", display: "flex", flexDirection: "column", justifyContent: hasMessages ? "flex-start" : "center", alignItems: "stretch" }}>
          {hasMessages && (
            <div style={{ width: "100%", maxWidth: "760px", margin: "0 auto", marginBottom: "8px", display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "12px" }}>
              <button
                onClick={() => { setReportError(null); setReportOpen(true); }}
                disabled={isStreaming}
                style={{ fontSize: "12px", color: isStreaming ? "var(--faint)" : "var(--muted)", background: "none", border: "none", cursor: isStreaming ? "not-allowed" : "pointer", padding: "0", fontFamily: "inherit", display: "inline-flex", alignItems: "center", gap: "5px" }}
                data-tip="Report this thread"
              >
                <IconBug size={12} />
                Report issue
              </button>
              <button
                onClick={downloadConversation}
                disabled={isStreaming}
                style={{ fontSize: "12px", color: isStreaming ? "var(--faint)" : "var(--muted)", background: "none", border: "none", cursor: isStreaming ? "not-allowed" : "pointer", padding: "0", fontFamily: "inherit", display: "inline-flex", alignItems: "center", gap: "5px" }}
                data-tip="Download this conversation as Markdown"
              >
                <IconDownload size={12} />
                Download .md
              </button>
            </div>
          )}

          {!hasMessages && (
            <div style={{ width: "100%", maxWidth: "760px", margin: "0 auto", textAlign: "center", marginBottom: "20px" }}>
              <h1 style={{ fontSize: "30px", fontWeight: 500, color: "var(--text)", margin: 0, letterSpacing: "-0.01em" }}>What&apos;s on your mind today?</h1>
            </div>
          )}

          {hasMessages && (
            <div ref={scrollRef} style={{ width: "100%", maxWidth: "760px", margin: "0 auto", marginBottom: "20px" }}>
              {messages.map((msg, idx) => (
                <div key={idx} style={{ marginBottom: "8px" }}>
                  {msg.role === "user" ? (
                    <div style={{ display: "flex", justifyContent: "flex-end", padding: "10px 0" }}>
                      <div style={{ background: "var(--user-bubble)", color: "var(--text)", padding: "10px 16px", borderRadius: "22px", maxWidth: "75%", whiteSpace: "pre-wrap", fontSize: "15px", lineHeight: 1.55 }}>{msg.content}</div>
                    </div>
                  ) : (
                    <div style={{ padding: "10px 0 18px" }}>
                      <div className={!msg.isComplete ? "streaming-text" : undefined} style={{ fontFamily: "system-ui, -apple-system, sans-serif", color: "var(--text)", fontSize: "15.5px", lineHeight: 1.7, minWidth: 0 }}>
                        {renderAssistantMessage(msg, idx)}
                      </div>
                      {(msg.isComplete || msg.cost_gbp !== undefined) && (
                        <div style={{ display: "flex", gap: "12px", alignItems: "center", marginTop: "4px" }}>
                          {msg.isComplete && (
                            <button
                              type="button"
                              onClick={() => copyMessage(idx)}
                              data-tip={copiedMessageIdx === idx ? "Copied" : "Copy answer to clipboard"}
                              style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "11px", color: copiedMessageIdx === idx ? "var(--accent)" : "var(--muted)", background: "none", border: "none", cursor: "pointer", padding: 0, fontFamily: "inherit" }}
                              onMouseEnter={(e) => { if (copiedMessageIdx !== idx) (e.currentTarget as HTMLElement).style.color = "var(--text)"; }}
                              onMouseLeave={(e) => { if (copiedMessageIdx !== idx) (e.currentTarget as HTMLElement).style.color = "var(--muted)"; }}
                            >
                              <IconCopy size={12} /> {copiedMessageIdx === idx ? "Copied" : "Copy"}
                            </button>
                          )}
                          {msg.cost_gbp !== undefined && (
                            <span style={{ fontSize: "11px", color: "var(--faint)", fontVariantNumeric: "tabular-nums" }}>
                              {msg.cost_gbp < 0.01 ? `${(msg.cost_gbp * 100).toFixed(2)}p` : `£${msg.cost_gbp.toFixed(3)}`}
                            </span>
                          )}
                        </div>
                      )}
                      {msg.isComplete && !isStreaming && (msg.stop_reason === "max_tokens" || msg.stopped) && !msg.events?.some((e) => e.type === "tool" && e.data.status === "pending") && (
                        <div style={{ marginTop: "10px", display: "flex", alignItems: "center", gap: "8px" }}>
                          <button
                            type="button"
                            onClick={() => continueMessage(idx)}
                            data-tip={msg.stop_reason === "max_tokens"
                              ? "The answer hit the response length cap — continue from where it stopped."
                              : "Resume from where you stopped the answer."}
                            style={{ display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "12px", color: "var(--accent)", background: "transparent", border: "1px solid var(--accent)", borderRadius: "999px", padding: "4px 10px", cursor: "pointer", fontFamily: "inherit" }}
                          >
                            ↳ Continue
                          </button>
                          <span style={{ fontSize: "11px", color: "var(--muted)" }}>
                            {msg.stop_reason === "max_tokens" ? "Truncated at max length" : "Stopped"}
                          </span>
                        </div>
                      )}
                      {msg.isComplete && msg.suggestions && msg.suggestions.length > 0 && (
                        <div data-pe-suggestions style={{ marginTop: "12px", display: "flex", flexWrap: "wrap", gap: "6px" }}>
                          {msg.suggestions.map((suggestion, sIdx) => (
                            <button
                              key={sIdx}
                              type="button"
                              onClick={() => useSuggestion(suggestion)}
                              data-tip="Use this follow-up — you can edit it before sending"
                              style={{
                                fontSize: "12.5px",
                                color: "var(--text-2)",
                                background: "var(--surface)",
                                border: "1px solid var(--border)",
                                borderRadius: "999px",
                                padding: "5px 12px",
                                cursor: "pointer",
                                fontFamily: "inherit",
                                lineHeight: 1.3,
                                textAlign: "left",
                                transition: "background 120ms, color 120ms, border-color 120ms",
                              }}
                              onMouseEnter={(e) => {
                                const el = e.currentTarget as HTMLElement;
                                el.style.background = "var(--surface-hover)";
                                el.style.color = "var(--text)";
                                el.style.borderColor = "var(--accent)";
                              }}
                              onMouseLeave={(e) => {
                                const el = e.currentTarget as HTMLElement;
                                el.style.background = "var(--surface)";
                                el.style.color = "var(--text-2)";
                                el.style.borderColor = "var(--border)";
                              }}
                            >
                              {suggestion}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
              {isWaiting && (
                <div style={{ padding: "18px 0 14px", marginBottom: "18px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "5px" }}>
                    <div style={{ width: "5px", height: "5px", borderRadius: "50%", background: "var(--muted)", animation: "thinking-dot 1.2s ease-in-out 0s infinite" }} />
                    <div style={{ width: "5px", height: "5px", borderRadius: "50%", background: "var(--muted)", animation: "thinking-dot 1.2s ease-in-out 0.2s infinite" }} />
                    <div style={{ width: "5px", height: "5px", borderRadius: "50%", background: "var(--muted)", animation: "thinking-dot 1.2s ease-in-out 0.4s infinite" }} />
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
          <div style={{ width: "100%", maxWidth: "760px", margin: "0 auto", position: "relative" }}>
            {!hasMessages && (
              <div aria-hidden="true" style={{ position: "absolute", inset: "-40px -60px", background: "radial-gradient(ellipse at center, var(--accent-15), transparent 70%)", filter: "blur(20px)", pointerEvents: "none", zIndex: 0 }} />
            )}
            <div style={{
              position: "relative",
              zIndex: 1,
              border: "1px solid var(--border)",
              background: "var(--surface)",
              borderRadius: "28px",
              padding: "14px 18px 10px",
              boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
            }}>
              {attachedImage && (
                <div style={{ marginBottom: "8px", display: "flex", alignItems: "flex-start", gap: "8px" }}>
                  <div style={{ position: "relative", display: "inline-block" }}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={attachedImage.dataUrl}
                      alt={attachedImage.name}
                      style={{ display: "block", maxWidth: "80px", maxHeight: "80px", borderRadius: "8px", border: "1px solid var(--border)", objectFit: "cover" }}
                    />
                    <button
                      type="button"
                      onClick={() => setAttachedImage(null)}
                      disabled={isStreaming}
                      data-tip="Remove image"
                      aria-label="Remove attached image"
                      style={{ position: "absolute", top: "-6px", right: "-6px", width: "18px", height: "18px", borderRadius: "999px", background: "var(--text)", color: "var(--surface)", border: "1px solid var(--surface)", cursor: isStreaming ? "not-allowed" : "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center", padding: 0 }}
                    >
                      <IconX size={12} />
                    </button>
                  </div>
                </div>
              )}
              {attachError && (
                <div role="alert" style={{ marginBottom: "8px", fontSize: "12px", color: "var(--accent)" }}>{attachError}</div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp,image/gif"
                onChange={handleFileSelected}
                style={{ display: "none" }}
              />
              <div style={{ position: "relative" }}>
                {!input && !hasMessages && (
                  <div aria-hidden="true" style={{ position: "absolute", top: "4px", left: "0", fontSize: "16px", lineHeight: 1.5, color: "var(--faint)", pointerEvents: "none" }}>
                    {animatedPlaceholder || "Ask anything"}
                    <span style={{ display: "inline-block", width: "2px", height: "1em", background: "var(--muted)", marginLeft: "1px", verticalAlign: "text-bottom", animation: "blink 1s step-end infinite" }} />
                    <style>{`@keyframes blink{50%{opacity:0}}`}</style>
                  </div>
                )}
                {!input && hasMessages && (
                  <div aria-hidden="true" style={{ position: "absolute", top: "4px", left: "0", fontSize: "16px", lineHeight: 1.5, color: "var(--faint)", pointerEvents: "none" }}>
                    Ask anything
                  </div>
                )}
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => { setInput(e.target.value); autoResize(e.target); }}
                  onKeyDown={handleKeyDown}
                  disabled={isStreaming}
                  rows={1}
                  aria-label="Ask a question"
                  style={{ width: "100%", maxHeight: "240px", background: "transparent", border: "none", outline: "none", fontSize: "16px", lineHeight: 1.5, color: "var(--text)", fontFamily: "inherit", resize: "none", padding: "4px 0", opacity: isStreaming ? 0.5 : 1, overflowY: "hidden", caretColor: "var(--text)", boxSizing: "border-box" }}
                />
              </div>
              <div style={{ marginTop: "6px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
                <div style={{ display: "inline-flex", alignItems: "center", gap: "6px", minWidth: 0 }}>
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isStreaming}
                    data-tip="Attach an image (JPG, PNG, WEBP, or GIF, up to 5MB)"
                    aria-label="Attach image"
                    style={{ width: "32px", height: "32px", borderRadius: "999px", background: "transparent", color: "var(--text-3)", border: "none", cursor: isStreaming ? "not-allowed" : "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center", padding: 0, opacity: isStreaming ? 0.5 : 1, transition: "background 120ms, color 120ms" }}
                  >
                    <IconPaperclip size={18} />
                  </button>
                  <button
                    type="button"
                    onClick={() => setChartsMode((v) => !v)}
                    disabled={isStreaming}
                    data-tip={chartsMode
                      ? "Charts mode on — the agent will prefer to include a chart when the question is plot-worthy."
                      : "Charts mode off — turn on to bias answers toward including a chart for distributions, comparisons, or trends."}
                    style={{
                      display: "inline-flex", alignItems: "center", gap: "6px",
                      padding: "5px 11px",
                      background: chartsMode ? "var(--accent-15)" : "transparent",
                      color: chartsMode ? "var(--accent)" : "var(--text-3)",
                      border: `1px solid ${chartsMode ? "var(--accent)" : "var(--border)"}`,
                      borderRadius: "999px",
                      fontSize: "12px",
                      fontFamily: "inherit",
                      cursor: isStreaming ? "not-allowed" : "pointer",
                      fontWeight: 500,
                      opacity: isStreaming ? 0.5 : 1,
                      transition: "background 120ms, color 120ms, border-color 120ms",
                    }}
                  >
                    <IconChartBar size={12} /> Charts {chartsMode ? "on" : "off"}
                  </button>
                  {input.length > 0 && (
                    <span
                      aria-live="polite"
                      style={{
                        fontSize: "11px",
                        fontVariantNumeric: "tabular-nums",
                        color: input.length >= 8000 ? "var(--accent)" : input.length >= 4000 ? "var(--text-3)" : "var(--faint)",
                        transition: "color 160ms",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {input.length.toLocaleString()}{input.length >= 16000 ? " — long" : ""}
                    </span>
                  )}
                </div>
                {isStreaming ? (
                  <button
                    onClick={stopStreaming}
                    data-tip="Stop" aria-label="Stop"
                    style={{ width: "32px", height: "32px", borderRadius: "999px", background: "var(--accent)", color: "var(--accent-fg)", border: "none", cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center", padding: 0 }}
                  >
                    <IconX size={16} />
                  </button>
                ) : (
                  <button
                    onClick={sendMessage}
                    disabled={!input.trim() && !attachedImage}
                    data-tip="Send"
                    aria-label="Send"
                    style={{ width: "32px", height: "32px", borderRadius: "999px", background: (input.trim() || attachedImage) ? "var(--accent)" : "var(--surface-hover)", color: (input.trim() || attachedImage) ? "var(--accent-fg)" : "var(--muted)", border: "none", cursor: (input.trim() || attachedImage) ? "pointer" : "not-allowed", display: "inline-flex", alignItems: "center", justifyContent: "center", padding: 0, transition: "background 120ms" }}
                  >
                    <IconArrowUp size={16} />
                  </button>
                )}
              </div>
            </div>
            {slashOpen && (
              <div
                ref={slashMenuRef}
                role="listbox"
                aria-label="Slash commands"
                style={{
                  position: "absolute",
                  left: 0,
                  right: 0,
                  top: "calc(100% + 6px)",
                  zIndex: 5,
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: "14px",
                  boxShadow: "0 6px 24px rgba(0,0,0,0.08)",
                  padding: "6px",
                  maxHeight: "260px",
                  overflowY: "auto",
                }}
              >
                {filteredSlashCommands.map((cmd, idx) => {
                  const active = idx === Math.min(slashIndex, filteredSlashCommands.length - 1);
                  return (
                    <div
                      key={cmd.name}
                      role="option"
                      aria-selected={active}
                      onMouseEnter={() => setSlashIndex(idx)}
                      onMouseDown={(e) => { e.preventDefault(); selectSlashCommand(cmd); }}
                      style={{
                        display: "flex",
                        alignItems: "baseline",
                        gap: "10px",
                        padding: "8px 10px",
                        borderRadius: "10px",
                        cursor: "pointer",
                        background: active ? "var(--surface-hover)" : "transparent",
                        color: "var(--text)",
                      }}
                    >
                      <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: "13px", color: "var(--accent)", minWidth: "64px" }}>
                        /{cmd.name}
                      </span>
                      <span style={{ fontSize: "13px", color: "var(--text-3)" }}>
                        {cmd.description}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
            {!hasMessages && !input && (
              <div style={{ marginTop: "14px", display: "flex", flexWrap: "wrap", justifyContent: "center", gap: "8px", position: "relative", zIndex: 1 }}>
                {[
                  "What's the personal allowance?",
                  "Tax on £50,000 income?",
                  "Child benefit for 3 kids?",
                  "How does marriage allowance work?",
                ].map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => {
                      setInput(prompt);
                      setTimeout(() => {
                        const el = inputRef.current;
                        if (el) { el.focus(); autoResize(el); }
                      }, 0);
                    }}
                    style={{
                      padding: "6px 12px",
                      background: "transparent",
                      color: "var(--text-3)",
                      border: "1px solid var(--border)",
                      borderRadius: "999px",
                      fontSize: "12px",
                      fontFamily: "inherit",
                      cursor: "pointer",
                      fontWeight: 500,
                      transition: "background 120ms, color 120ms, border-color 120ms",
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-hover)"; e.currentTarget.style.color = "var(--text)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "var(--text-3)"; }}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Auth modal */}
      {showAuth && (
        <div onClick={() => setShowAuth(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ background: "var(--surface)", color: "var(--text)", padding: "32px", width: "360px", maxWidth: "90vw", borderRadius: "16px", border: "1px solid var(--border)", boxShadow: "0 10px 40px rgba(0,0,0,0.15)" }}>
            <h2 style={{ margin: "0 0 20px", fontSize: "18px", fontWeight: 600, color: "var(--text)" }}>
              {authMode === "signin" ? "Sign in" : "Create account"}
            </h2>
            {authError && <div style={{ padding: "8px 12px", background: "var(--accent-15)", color: "#ef4444", fontSize: "13px", marginBottom: "16px", borderRadius: "8px" }}>{authError}</div>}
            <form onSubmit={async (e) => {
              e.preventDefault();
              setAuthSubmitting(true);
              setAuthError(null);
              const { error } = authMode === "signin" ? await signIn(authEmail, authPassword) : await signUp(authEmail, authPassword);
              setAuthSubmitting(false);
              if (error) setAuthError(error);
              else { setShowAuth(false); setAuthEmail(""); setAuthPassword(""); }
            }}>
              <input type="email" placeholder="Email" value={authEmail} onChange={(e) => setAuthEmail(e.target.value)} required style={{ width: "100%", padding: "10px 12px", fontSize: "14px", border: "1px solid var(--border)", marginBottom: "10px", fontFamily: "inherit", boxSizing: "border-box", borderRadius: "8px", background: "var(--surface)", color: "var(--text)" }} />
              <input type="password" placeholder="Password" value={authPassword} onChange={(e) => setAuthPassword(e.target.value)} required minLength={6} style={{ width: "100%", padding: "10px 12px", fontSize: "14px", border: "1px solid var(--border)", marginBottom: "16px", fontFamily: "inherit", boxSizing: "border-box", borderRadius: "8px", background: "var(--surface)", color: "var(--text)" }} />
              <button type="submit" disabled={authSubmitting} style={{ width: "100%", padding: "10px", fontSize: "14px", background: "var(--accent)", color: "var(--accent-fg)", border: "none", cursor: authSubmitting ? "not-allowed" : "pointer", fontFamily: "inherit", opacity: authSubmitting ? 0.7 : 1, borderRadius: "8px", fontWeight: 500 }}>
                {authSubmitting ? "..." : authMode === "signin" ? "Sign in" : "Create account"}
              </button>
            </form>
            <div style={{ marginTop: "16px", textAlign: "center", fontSize: "13px", color: "var(--muted)" }}>
              {authMode === "signin" ? (
                <>No account? <button onClick={() => { setAuthMode("signup"); setAuthError(null); }} style={{ color: "var(--text)", background: "none", border: "none", cursor: "pointer", fontFamily: "inherit", fontSize: "13px", textDecoration: "underline" }}>Create one</button></>
              ) : (
                <>Have an account? <button onClick={() => { setAuthMode("signin"); setAuthError(null); }} style={{ color: "var(--text)", background: "none", border: "none", cursor: "pointer", fontFamily: "inherit", fontSize: "13px", textDecoration: "underline" }}>Sign in</button></>
              )}
            </div>
          </div>
        </div>
      )}

      {reportOpen && (
        <div onClick={() => !reportSubmitting && setReportOpen(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ background: "var(--surface)", color: "var(--text)", padding: "28px", width: "520px", maxWidth: "92vw", border: "1px solid var(--border)", borderRadius: "16px", boxShadow: "0 10px 40px rgba(0,0,0,0.15)" }}>
            <h2 style={{ margin: "0 0 10px", fontSize: "18px", fontWeight: 600, color: "var(--text)" }}>Report this thread</h2>
            <p style={{ margin: "0 0 14px", fontSize: "14px", lineHeight: 1.6, color: "var(--text-3)" }}>
              This will open a prefilled GitHub issue with a link to the shared thread and the most relevant parts of the conversation so we can debug it later.
            </p>
            <textarea
              value={reportNote}
              onChange={(e) => setReportNote(e.target.value)}
              placeholder="What looks off? For example: the budget impact seems too high, the answer ignored Scotland, or the explanation contradicts the chart."
              rows={5}
              style={{ width: "100%", padding: "12px", fontSize: "14px", border: "1px solid var(--border)", borderRadius: "8px", fontFamily: "inherit", boxSizing: "border-box", resize: "vertical", color: "var(--text)", background: "var(--surface)", lineHeight: 1.5 }}
            />
            {reportError && (
              <div style={{ marginTop: "10px", fontSize: "13px", color: "#ef4444" }}>{reportError}</div>
            )}
            <div style={{ marginTop: "16px", display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button
                onClick={() => setReportOpen(false)}
                disabled={reportSubmitting}
                style={{ fontSize: "13px", padding: "8px 14px", border: "1px solid var(--border)", borderRadius: "999px", background: "transparent", color: "var(--text-2)", cursor: reportSubmitting ? "not-allowed" : "pointer", fontFamily: "inherit" }}
              >
                Cancel
              </button>
              <button
                onClick={submitReport}
                disabled={reportSubmitting}
                style={{ fontSize: "13px", padding: "8px 14px", border: "none", borderRadius: "999px", background: "var(--accent)", color: "var(--accent-fg)", cursor: reportSubmitting ? "not-allowed" : "pointer", fontFamily: "inherit", display: "inline-flex", alignItems: "center", gap: "6px", opacity: reportSubmitting ? 0.7 : 1 }}
              >
                {reportSubmitting ? <Loader size={12} color="#ffffff" /> : <IconBug size={13} />}
                Open GitHub issue
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
