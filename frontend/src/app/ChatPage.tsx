"use client";

import { useState, useRef, useEffect, useCallback, useMemo, type CSSProperties } from "react";
import { Loader } from "@mantine/core";
import { IconX, IconTrash, IconChevronDown, IconUser, IconShare, IconBug, IconArrowUp, IconMessage, IconEdit, IconCopy, IconDownload, IconChartBar, IconPaperclip, IconDots, IconSearch, IconLayoutSidebarLeftCollapse, IconLayoutSidebarLeftExpand } from "@tabler/icons-react";
import { useAuth } from "@/utils/AuthContext";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Chart, extractChartSpecs } from "@/components/charts";
import { THEME } from "@/components/theme";
import AccountMenu from "@/components/AccountMenu";
import AuthDialog from "@/components/AuthDialog";
import ChatSearchDialog, { type ChatSearchResult } from "@/components/ChatSearchDialog";
import ThemeSelector from "@/components/ThemeSelector";
import { APP_BASE_PATH, getAppBaseUrl, getBackendEndpoint } from "@/utils/backend";
import { enqueueSerial } from "@/utils/serialQueue";
import { useThemePreference } from "@/utils/theme";
import { STARTER_PROMPTS } from "./chat-prompts";

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
  messages: Array<{ role: string; content: string; events?: StreamEvent[]; attachment?: MessageAttachment }>;
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

const PLACEHOLDER_TYPE_DELAY_MS = 50;
const PLACEHOLDER_HOLD_DELAY_MS = 2000;
const PLACEHOLDER_DELETE_DELAY_MS = 30;

function useAnimatedPlaceholder(queries: string[], enabled: boolean) {
  const [queryIndex, setQueryIndex] = useState(0);
  const [charIndex, setCharIndex] = useState(0);
  const [isDeleting, setIsDeleting] = useState(false);
  const currentQuery = queries[queryIndex] ?? "";

  useEffect(() => {
    setQueryIndex(Math.floor(Math.random() * queries.length));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!enabled || !currentQuery) {
      setCharIndex(0);
      setIsDeleting(false);
      return;
    }

    const finishedTyping = charIndex === currentQuery.length;
    const finishedDeleting = charIndex === 0;
    const delay = finishedTyping && !isDeleting
      ? PLACEHOLDER_HOLD_DELAY_MS
      : isDeleting
        ? PLACEHOLDER_DELETE_DELAY_MS
        : PLACEHOLDER_TYPE_DELAY_MS;

    const timeout = setTimeout(() => {
      if (finishedTyping && !isDeleting) {
        setIsDeleting(true);
        return;
      }
      if (finishedDeleting && isDeleting) {
        const offset = queries.length > 1
          ? 1 + Math.floor(Math.random() * (queries.length - 1))
          : 0;
        setQueryIndex((queryIndex + offset) % queries.length);
        setIsDeleting(false);
        return;
      }
      setCharIndex((current) => current + (isDeleting ? -1 : 1));
    }, delay);

    return () => clearTimeout(timeout);
  }, [queries, queryIndex, charIndex, currentQuery, isDeleting, enabled]);

  return enabled ? currentQuery.slice(0, charIndex) : "";
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
  attachment?: MessageAttachment;
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

interface MessageAttachment {
  name: string;
  mediaType: string;
}

// ---- Export helpers ---------------------------------------------------------

const stripChartPlaceholders = (text: string): string =>
  text.replace(/\[CHART_PLACEHOLDER_\d+\]/g, "[Chart]").replace(/\[CHART_LOADING\]/g, "[Chart]");

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
          const inputStr = t.input ? JSON.stringify(t.input, null, 2) : "";
          if (inputStr) parts.push(`\n**${t.tool_name} input**\n\`\`\`json\n${inputStr}\n\`\`\`\n`);
          if (t.result_summary) parts.push(`\n**${t.tool_name} output**\n\`\`\`\n${t.result_summary}\n\`\`\`\n`);
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

/** Parse a Retry-After header into whole seconds. The header may be an
 * HTTP-date (which parseInt turns into NaN) or missing — fall back to 60. */
function parseRetryAfterSeconds(header: string | null): number {
  const parsed = header ? parseInt(header, 10) : NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 60;
}

/** Describe tool-backed work as complete only after final output is visible. */
const getWorkingSummaryLabel = (finalEvents: readonly StreamEvent[]): string =>
  finalEvents.some((event) => event.type === "text" && event.content.trim())
    ? "Worked through the problem"
    : "Working through the problem";

async function apiRequest<T>(method: string, endpoint: string, params?: Record<string, string>, body?: unknown): Promise<T> {
  const url = new URL(getBackendEndpoint(endpoint), window.location.origin);
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const options: RequestInit = { method, headers: { "Content-Type": "application/json" } };
  if (body && ["POST", "PUT", "PATCH"].includes(method)) options.body = JSON.stringify(body);
  const res = await fetch(url.toString(), options);
  if (!res.ok) {
    let detail = "";
    try { const errorBody = await res.json(); detail = errorBody.details || errorBody.error || ""; } catch {}
    throw new Error(`API error ${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export default function ChatPage() {
  const { user, loading: authLoading, signIn, signUp, signOut } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isInputFocused, setIsInputFocused] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isWaiting, setIsWaiting] = useState(false);
  const [collapsedWorking, setCollapsedWorking] = useState<Set<number>>(new Set());
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [copiedSnippetId, setCopiedSnippetId] = useState<string | null>(null);
  const [copiedMessageIdx, setCopiedMessageIdx] = useState<number | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [chatSearchOpen, setChatSearchOpen] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  const conversationCache = useRef<Map<number, ConversationDetail>>(new Map());
  const [showAuth, setShowAuth] = useState(false);
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
  const { preference: themePreference, setPreference: setThemePreference } = useThemePreference();
  const bottomRef = useRef<HTMLDivElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [composerHeight, setComposerHeight] = useState(0);
  const sessionId = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const draftRestoredRef = useRef(false);
  // Monotonic stream generation. Incremented whenever the visible conversation
  // changes (new chat, load, delete-active) so any in-flight sendMessage /
  // continueMessage callbacks can detect they are stale and stop writing state.
  const streamGeneration = useRef(0);
  // Title of the currently displayed conversation (null until known), plus a
  // memoized in-flight title generation so a turn's double save (done +
  // suggestions) only ever POSTs chat/title once per conversation.
  const conversationTitleRef = useRef<string | null>(null);
  const titleGenPromiseRef = useRef<Promise<string> | null>(null);
  const saveQueueRef = useRef<Promise<void>>(Promise.resolve());

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
  const hasMessages = messages.length > 0;
  const showPlaceholder = !input && (hasMessages || !isInputFocused);
  const showAnimatedPlaceholder = showPlaceholder && !hasMessages;
  const showStaticPlaceholder = showPlaceholder && hasMessages;
  const animatedPlaceholder = useAnimatedPlaceholder(EXAMPLE_QUERIES, showAnimatedPlaceholder);

  useEffect(() => {
    apiRequest<{ engine: string; engine_version: string; policyengine_uk: string }>("GET", "version")
      .then((v) =>
        setModelVersion(`${v.engine} v${v.engine_version}`),
      )
      .catch(() => {});
  }, []);

  useEffect(() => {
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

  // The conversation scrolls with the document while only the rounded composer
  // floats above it. Only follow new content when the user is already near the
  // bottom — never fight someone reading earlier messages mid-stream.
  const isNearBottom = () => {
    if (typeof window === "undefined" || typeof document === "undefined") return true;
    return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 200;
  };

  useEffect(() => {
    if (!isNearBottom()) return;
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  // Reserve exactly enough document space for the floating composer so the
  // final answer can always scroll clear of it, including attachments and
  // multi-line drafts.
  useEffect(() => {
    if (!hasMessages || !composerRef.current) {
      setComposerHeight(0);
      return;
    }
    const composer = composerRef.current;
    const updateHeight = () => setComposerHeight(composer.offsetHeight);
    updateHeight();
    const observer = new ResizeObserver(updateHeight);
    observer.observe(composer);
    return () => observer.disconnect();
  }, [hasMessages]);

  useEffect(() => {
    if (!composerHeight || !isNearBottom()) return;
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [composerHeight]);

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

  // Kill any in-flight stream before the visible conversation changes: bump
  // the generation so stale callbacks bail, abort the request, and reset the
  // streaming UI state (the stale stream's own resets are generation-guarded).
  const invalidateStream = () => {
    streamGeneration.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
    setIsWaiting(false);
  };

  const loadConversation = async (conv: ConversationSummary) => {
    invalidateStream();
    conversationTitleRef.current = null;
    titleGenPromiseRef.current = null;
    // If the user switches again (or starts a new chat) while this fetch is in
    // flight, a later invalidateStream bumps the generation — bail instead of
    // applying this conversation's state out of order.
    const generation = streamGeneration.current;
    try {
      const data = conversationCache.current.get(conv.id) || await apiRequest<ConversationDetail>("GET", `conversations/${conv.id}`);
      if (streamGeneration.current !== generation) return;
      if (!data?.messages?.length) { console.error("No messages in conversation", data); return; }
      const loaded: Message[] = data.messages.map((m) => {
        const raw = m as { role: string; content: string; events?: StreamEvent[]; attachment?: MessageAttachment; stop_reason?: string; stopped?: boolean; cost_gbp?: number; suggestions?: string[] };
        return {
          role: raw.role as "user" | "assistant",
          content: raw.content || "",
          attachment: raw.attachment,
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
      conversationTitleRef.current = data.title || conv.title || null;
      setActiveConversationId(data.id);
      setCollapsedWorking(collapsed);
      setMessages(loaded);
      setSidebarOpen(true);
    } catch (e) {
      console.error("Failed to load conversation", e);
      if (streamGeneration.current === generation) setMessages([{ role: "assistant", content: `Failed to load conversation: ${e instanceof Error ? e.message : "Unknown error"}` }]);
    }
  };

  const [copiedShareId, setCopiedShareId] = useState<number | null>(null);
  const [conversationMenu, setConversationMenu] = useState<{ id: number; top: number; left: number } | null>(null);

  const toggleConversationMenu = (e: React.MouseEvent<HTMLButtonElement>, id: number) => {
    e.stopPropagation();
    if (conversationMenu?.id === id) {
      setConversationMenu(null);
      return;
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const menuWidth = 184;
    const menuHeight = 104;
    const opensUpward = rect.bottom + 6 + menuHeight > window.innerHeight - 12;
    setConversationMenu({
      id,
      top: opensUpward ? rect.top - menuHeight - 6 : rect.bottom + 6,
      left: Math.max(8, rect.right - menuWidth),
    });
  };

  useEffect(() => {
    if (!conversationMenu) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (!target.closest("[data-pe-conversation-menu], [data-pe-conversation-menu-trigger]")) {
        setConversationMenu(null);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setConversationMenu(null);
    };
    document.addEventListener("click", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("click", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [conversationMenu]);

  const shareConversation = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    try {
      const { share_token } = await apiRequest<{ share_token: string }>("POST", `conversations/${id}/share`, user?.id ? { user_id: user.id } : undefined);
      const url = `${getAppBaseUrl(window.location.origin)}/s/${share_token}`;
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
      conversationCache.current.delete(id);
      if (activeConversationId === id) {
        // Deleting the conversation on screen: kill any in-flight stream and
        // fully reset chat state so the next send starts a fresh session
        // instead of resurrecting the just-deleted conversation.
        invalidateStream();
        conversationTitleRef.current = null;
        titleGenPromiseRef.current = null;
        setMessages([]);
        sessionId.current = null;
        setActiveConversationId(null);
        setCollapsedWorking(new Set());
      }
    } catch (e) { console.error(e); }
  };

  const saveConversation = useCallback((msgs: Message[], sid: string): Promise<ConversationDetail | null> => {
    const persist = async (): Promise<ConversationDetail | null> => {
      const generation = streamGeneration.current;
      const firstUserMsg = msgs.find((m) => m.role === "user");
      if (!firstUserMsg) return null;
      const firstAssistantMsg = msgs.find((m) => m.role === "assistant");
      const firstAssistantContent = (() => {
        if (!firstAssistantMsg?.isComplete || !firstAssistantMsg.events?.length) return firstAssistantMsg?.content;
        const lastToolIdx = firstAssistantMsg.events.reduce((acc, e, i) => e.type === "tool" ? i : acc, -1);
        if (lastToolIdx >= 0) return firstAssistantMsg.events.slice(lastToolIdx + 1).filter((e): e is { type: "text"; content: string } => e.type === "text").map((e) => e.content).join("") || firstAssistantMsg.content;
        return firstAssistantMsg.content;
      })();

      // Generate a title only when the conversation doesn't already have one.
      let title = conversationTitleRef.current;
      if (!title) {
        if (!titleGenPromiseRef.current) {
          const fallback = firstUserMsg.content.slice(0, 60);
          titleGenPromiseRef.current = apiRequest<{ title: string }>("POST", "chat/title", undefined, { first_user_message: firstUserMsg.content, first_assistant_message: firstAssistantContent || "" })
            .then(({ title: generated }) => generated || fallback)
            .catch((e) => { console.error("Title generation failed", e); return fallback; });
        }
        title = await titleGenPromiseRef.current;
        if (streamGeneration.current === generation) conversationTitleRef.current = title;
      }

      const apiMessages = msgs.map((m) => {
        const base: Record<string, unknown> = { role: m.role, content: m.content };
        if (m.attachment) base.attachment = m.attachment;
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
        // The sidebar list and cache stay correct regardless, but only mark this
        // conversation active if the user hasn't switched away in the meantime.
        if (streamGeneration.current === generation) setActiveConversationId(saved.id);
        conversationCache.current.set(saved.id, saved);
        setConversations((prev) => {
          const filtered = prev.filter((c) => c.session_id !== sid);
          return [{ id: saved.id, session_id: sid, title, created_at: saved.created_at, updated_at: saved.updated_at }, ...filtered];
        });
        return saved;
      } catch (e) { console.error("Failed to save conversation", e); }
      return null;
    };

    // `done` and `suggestions` are separate stream events. Queue the complete
    // save jobs in arrival order so the suggestions payload cannot race or be
    // overwritten by the earlier transcript-only payload.
    return enqueueSerial(saveQueueRef, persist);
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
        app_url: getAppBaseUrl(window.location.origin),
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
    invalidateStream();
    conversationTitleRef.current = null;
    titleGenPromiseRef.current = null;
    setMessages([]);
    sessionId.current = null;
    setActiveConversationId(null);
    setCollapsedWorking(new Set());
    setSidebarOpen(false);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const sendMessage = async () => {
    if ((!input.trim() && !attachedImage) || isStreaming) return;
    // Capture the stream generation for this send. If the user starts a new
    // chat, loads another conversation, or deletes the active one while this
    // stream is in flight, the generation bumps and every state write below
    // bails instead of corrupting the newly displayed conversation.
    const generation = streamGeneration.current;
    const isStale = () => streamGeneration.current !== generation;
    // If the user attached an image but didn't type anything, give the model
    // a minimal nudge so the request still has a coherent user turn.
    const sendingImage = attachedImage;
    const displayContent = input.trim() || (sendingImage ? `[Attached image: ${sendingImage.name}]` : "");
    const userMessage: Message = {
      role: "user",
      content: displayContent,
      attachment: sendingImage ? { name: sendingImage.name, mediaType: sendingImage.mediaType } : undefined,
    };
    const allMessages = [...messages, userMessage];
    setMessages((prev) => [...prev, userMessage]);
    if (messages.length === 0 && user) setSidebarOpen(true);
    // Snapshot the attachment for this send, then clear it from the input.
    setInput("");
    setAttachedImage(null);
    setAttachError(null);
    if (typeof window !== "undefined") {
      try { localStorage.removeItem("policyengine-uk-chat:draft"); } catch {}
    }
    setIsStreaming(true);
    setIsWaiting(true);

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
    // Turn metadata from the `done` event, hoisted so the later `suggestions`
    // save persists the same cost/truncation flags as the on-screen message.
    let msgCost: number | undefined;
    let stopReason: string | undefined;

    const updateMessage = () => {
      if (isStale()) return;
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
        if (isStale()) {
          if (drainTimer) { clearInterval(drainTimer); drainTimer = null; }
          return;
        }
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
        if (!isStale()) setMessages((prev) => [...prev, { role: "assistant", content: err.error || "No credit remaining. Please top up to continue." }]);
        return;
      }
      if (response.status === 429) {
        const seconds = parseRetryAfterSeconds(response.headers.get("retry-after"));
        if (!isStale()) setMessages((prev) => [...prev, { role: "assistant", content: `You're sending messages a bit fast — please wait ~${seconds}s and try again.`, isComplete: true }]);
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
          if (isStale()) return;
          if (!line.startsWith("data: ")) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === "chunk") {
              setIsWaiting(false);
              const lastEvent = events[events.length - 1];
              if (lastEvent?.type === "text") lastEvent.content += data.content;
              else events.push({ type: "text", content: data.content });
              currentText += data.content;
              startDrain();
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
              msgCost = typeof data.cost_gbp === "number" ? data.cost_gbp : undefined;
              stopReason = typeof data.stop_reason === "string" ? data.stop_reason : undefined;
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
                // Save the same metadata the on-screen message carries so a
                // reload keeps the cost line and the Continue affordance for
                // max_tokens-truncated turns.
                const finalMsgs = [...allMessages, { role: "assistant" as const, content: currentText, isComplete: true, events: [...events], cost_gbp: msgCost, stop_reason: stopReason }];
                saveConversation(finalMsgs, data.session_id);
              }
              setTimeout(() => {
                if (!isStale() && isNearBottom()) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
              }, 100);
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
                // Persist suggestions alongside the saved conversation,
                // keeping the metadata from `done` intact.
                if (sessionId.current) {
                  const finalMsgs = [
                    ...allMessages,
                    { role: "assistant" as const, content: currentText, isComplete: true, events: [...events], cost_gbp: msgCost, stop_reason: stopReason, suggestions: cleaned },
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
        if (drainTimer) { clearInterval(drainTimer); drainTimer = null; }
        if (!isStale()) {
          displayedText = currentText;
          const hasContent = currentText.trim().length > 0 || events.some((e) => e.type === "tool");
          if (hasContent) {
            // User stopped the stream — flush what we have, mark `stopped`
            // so the UI shows a Continue affordance.
            updateMessage();
            setMessages((prev) => {
              const newMsgs = [...prev];
              const lastIdx = newMsgs.length - 1;
              if (newMsgs[lastIdx]?.role === "assistant") newMsgs[lastIdx] = { ...newMsgs[lastIdx], isComplete: true, stopped: true };
              return newMsgs;
            });
          } else {
            // Stopped before the first token — there is nothing to continue
            // from, so drop the empty assistant bubble if one was created.
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.role === "assistant" && !last.content && !last.events?.some((e) => e.type === "tool")) return prev.slice(0, -1);
              return prev;
            });
          }
        }
      } else if (!isStale()) {
        setMessages((prev) => [...prev, { role: "assistant", content: `Something went wrong: ${error instanceof Error ? error.message : "Unknown error"}` }]);
      }
    } finally {
      // A newer stream may already own abortRef — only clear our own controller.
      if (abortRef.current === controller) abortRef.current = null;
      if (drainTimer) { clearInterval(drainTimer); drainTimer = null; }
      if (!isStale()) {
        setIsStreaming(false);
        setIsWaiting(false);
        setTimeout(() => inputRef.current?.focus(), 0);
      }
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
    // Same stream-generation guard as sendMessage: bail out of every state
    // write if the visible conversation changes while this is in flight.
    const generation = streamGeneration.current;
    const isStale = () => streamGeneration.current !== generation;
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

    // Snapshot the flags we are about to clear so early-exit paths (402, 429,
    // fetch failure) can restore them instead of leaving the message stuck
    // incomplete with no Continue affordance.
    const prevFlags = { isComplete: target.isComplete, stop_reason: target.stop_reason, stopped: target.stopped };
    const restoreTargetFlags = () => {
      setMessages((prev) => prev.map((m, i) => i === idx ? { ...m, ...prevFlags } : m));
    };

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
      if (isStale()) return;
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
        if (!isStale()) {
          restoreTargetFlags();
          setMessages((prev) => [...prev, { role: "assistant", content: err.error || "No credit remaining. Please top up to continue.", isComplete: true }]);
        }
        return;
      }
      if (response.status === 429) {
        const seconds = parseRetryAfterSeconds(response.headers.get("retry-after"));
        if (!isStale()) {
          restoreTargetFlags();
          setMessages((prev) => [...prev, { role: "assistant", content: `You're sending messages a bit fast — please wait ~${seconds}s and try again.`, isComplete: true }]);
        }
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
          if (isStale()) return;
          if (!line.startsWith("data: ")) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === "chunk") {
              setIsWaiting(false);
              const lastEvent = newEvents[newEvents.length - 1];
              if (lastEvent?.type === "text") lastEvent.content += data.content;
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
        if (!isStale()) {
          setMessages((prev) => prev.map((m, i) => i === idx ? {
            ...m,
            isComplete: true,
            content: baseContent + appendedText,
            events: [...baseEvents, ...newEvents],
            stopped: true,
          } : m));
        }
      } else if (!isStale()) {
        const errorText = `Continuation failed: ${error instanceof Error ? error.message : "Unknown error"}`;
        // Restore the pre-continuation truncation/stopped flags so the
        // Continue affordance survives a failed attempt.
        setMessages((prev) => prev.map((m, i) => i === idx ? {
          ...m,
          isComplete: true,
          content: baseContent + appendedText + "\n\n" + errorText,
          events: [...baseEvents, ...newEvents],
          stop_reason: prevFlags.stop_reason,
          stopped: prevFlags.stopped,
        } : m));
      }
    } finally {
      // A newer stream may already own abortRef — only clear our own controller.
      if (abortRef.current === controller) abortRef.current = null;
      if (!isStale()) {
        setIsStreaming(false);
        setIsWaiting(false);
      }
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
    const codeStyle = { margin: 0, padding: "8px 10px", background: "#1a1917", color: "#c9c5bc", whiteSpace: "pre-wrap" as const, wordBreak: "break-word" as const, maxHeight: "300px", overflow: "auto" as const, fontSize: "11px", lineHeight: 1.7, fontFamily: "'JetBrains Mono', monospace" };
    const copyButtonStyle = { fontSize: "10px", color: THEME.primary, background: "none", border: "none", cursor: "pointer", padding: 0, fontFamily: "'JetBrains Mono', monospace" } as const;
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
            validate_reform: "reform validation",
            validate_household: "household validation",
            run_household_simulation: "household simulation",
            run_society_simulation: "society simulation",
            compute_budgetary_impact: "budgetary impact",
            compute_program_breakdown: "programme breakdown",
            compute_decile_impacts: "decile impacts",
            compute_winners_losers: "winners and losers",
            compute_poverty_metrics: "poverty metrics",
            compute_inequality_metrics: "inequality metrics",
            aggregate_result: "aggregate result",
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
            if (chart) return <div key={idx} style={{ margin: "16px 0", maxWidth: "100%", minWidth: 0 }}><Chart spec={chart} height={400} /></div>;
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

    return (
      <>
        {hasTools && (
          <>
            <div onClick={toggleWorking} style={{ display: "flex", alignItems: "baseline", gap: "6px", color: THEME.muted, fontSize: "12px", cursor: "pointer", userSelect: "none", margin: "6px 0", padding: "2px 0" }}>
              <IconChevronDown size={12} style={{ opacity: 0.5, transform: isWorkingCollapsed ? "rotate(-90deg)" : "none", transition: "transform 0.15s", flexShrink: 0, position: "relative", top: "1px" }} />
              <span style={{ color: THEME.text3, fontStyle: "italic" }}>{getWorkingSummaryLabel(finalEvents)}</span>
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
  const sidebarOffset = isEmbed ? 0 : sidebarOpen ? 260 : 60;
  const recentSearchResults = useMemo<ChatSearchResult[]>(
    () => conversations.map((conversation) => ({ ...conversation, snippet: null })),
    [conversations],
  );
  const searchChats = useCallback(
    (query: string) => user
      ? apiRequest<ChatSearchResult[]>("GET", "conversations/search", { user_id: user.id, query })
      : Promise.resolve([]),
    [user],
  );

  const openChatSearch = () => {
    setChatSearchOpen(true);
  };

  const sidebarButtonStyle: CSSProperties = {
    width: "100%",
    height: "40px",
    flexShrink: 0,
    border: "none",
    borderRadius: "10px",
    background: "transparent",
    color: "var(--text-2)",
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    padding: 0,
    fontFamily: "inherit",
    fontSize: "14px",
    fontWeight: 500,
    textAlign: "left",
  };
  const sidebarIconStyle: CSSProperties = {
    width: "44px",
    height: "40px",
    flexShrink: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  };
  const sidebarLabelStyle: CSSProperties = {
    minWidth: 0,
    maxWidth: sidebarOpen ? "180px" : 0,
    opacity: sidebarOpen ? 1 : 0,
    overflow: "hidden",
    whiteSpace: "nowrap",
    visibility: sidebarOpen ? "visible" : "hidden",
    transition: sidebarOpen
      ? "opacity 120ms ease 80ms, max-width 200ms ease"
      : "opacity 80ms ease, max-width 200ms ease, visibility 0s linear 200ms",
  };

  return (
    <div style={{ minHeight: "calc(100dvh - var(--pe-shell-h))", background: "var(--bg)", color: "var(--text)", fontFamily: "system-ui, -apple-system, sans-serif" }}>
      <style>{`
        [data-tip],[data-tip-left],[data-tip-right]{position:relative}
        [data-tip]::after,[data-tip-left]::after,[data-tip-right]::after{
          /* content:none until :hover so the hidden box is never laid out —
             an opacity:0 tooltip below/right of the last in-flow element
             stretches the document's scrollable area (dead space). */
          content:none;
          position:absolute;
          background:var(--text); color:var(--bg);
          padding:4px 8px; border-radius:6px; font-size:11px; line-height:1.3;
          white-space:normal; max-width:240px; width:max-content; text-align:center;
          pointer-events:none; z-index:60;
          animation:tip-fade 60ms ease;
        }
        [data-tip]::after{
          left:50%; top:calc(100% + 6px); transform:translateX(-50%);
        }
        [data-tip-left]::after{
          right:calc(100% + 8px); top:50%; transform:translateY(-50%);
        }
        [data-tip-right]::after{
          left:calc(100% + 8px); top:50%; transform:translateY(-50%);
        }
        [data-tip]:hover::after{content:attr(data-tip)}
        [data-tip-left]:hover::after{content:attr(data-tip-left)}
        [data-tip-right]:hover::after{content:attr(data-tip-right)}
        @keyframes tip-fade{from{opacity:0}to{opacity:1}}
        @media (prefers-reduced-motion:reduce){
          [data-pe-sidebar],[data-pe-sidebar-label],[data-pe-sidebar-content],[data-pe-composer]{
            transition:none !important;
          }
        }
        @media (max-width:640px){
          [data-pe-composer][data-fixed="true"]{
            left:50% !important;
            width:calc(100% - 24px) !important;
          }
        }
      `}</style>
      {/* Body */}
      <div style={{ display: "flex", margin: "0 auto", padding: "0", gap: "0", width: "100%", minHeight: "calc(100dvh - var(--pe-shell-h))" }}>
        {!isEmbed && (
          <div
            data-pe-sidebar
            data-expanded={sidebarOpen}
            style={{
              width: sidebarOpen ? "260px" : "60px",
              flexShrink: 0,
              background: "var(--sidebar-bg)",
              borderRight: "1px solid var(--border)",
              padding: "10px 8px",
              position: "sticky",
              top: "var(--pe-shell-h)",
              height: "calc(100dvh - var(--pe-shell-h))",
              alignSelf: "flex-start",
              display: "flex",
              flexDirection: "column",
              boxSizing: "border-box",
              transition: "width 200ms ease",
              willChange: "width",
            }}
          >
            <button
              type="button"
              onClick={() => {
                setConversationMenu(null);
                setSidebarOpen((open) => !open);
              }}
              data-tip-right={sidebarOpen ? undefined : "Open sidebar"}
              aria-label={sidebarOpen ? "Close sidebar" : "Open sidebar"}
              aria-expanded={sidebarOpen}
              style={{ ...sidebarButtonStyle, color: "var(--text)" }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-hover)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
            >
              <span style={sidebarIconStyle}>
                {sidebarOpen ? <IconLayoutSidebarLeftCollapse size={20} /> : <IconLayoutSidebarLeftExpand size={20} />}
              </span>
              <span data-pe-sidebar-label aria-hidden={!sidebarOpen} style={sidebarLabelStyle}>Close sidebar</span>
            </button>
            <button
              type="button"
              onClick={startNewChat}
              data-tip-right={sidebarOpen ? undefined : "New chat"}
              aria-label="New chat"
              style={sidebarButtonStyle}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-hover)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
            >
              <span style={sidebarIconStyle}><IconEdit size={20} /></span>
              <span data-pe-sidebar-label aria-hidden={!sidebarOpen} style={sidebarLabelStyle}>New chat</span>
            </button>
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              data-tip-right={sidebarOpen ? undefined : "Chats"}
              aria-label="Chats"
              aria-expanded={sidebarOpen}
              style={sidebarButtonStyle}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-hover)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
            >
              <span style={sidebarIconStyle}><IconMessage size={20} /></span>
              <span data-pe-sidebar-label aria-hidden={!sidebarOpen} style={sidebarLabelStyle}>Chats</span>
            </button>
            <button
              type="button"
              onClick={openChatSearch}
              data-tip-right={sidebarOpen ? undefined : "Search chats"}
              aria-label="Search chats"
              aria-expanded={chatSearchOpen}
              style={{ ...sidebarButtonStyle, background: chatSearchOpen ? "var(--surface-hover)" : "transparent" }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-hover)"; }}
              onMouseLeave={(e) => { if (!chatSearchOpen) e.currentTarget.style.background = "transparent"; }}
            >
              <span style={sidebarIconStyle}><IconSearch size={20} /></span>
              <span data-pe-sidebar-label aria-hidden={!sidebarOpen} style={sidebarLabelStyle}>Search chats</span>
            </button>
            <div
              data-pe-sidebar-content
              aria-hidden={!sidebarOpen}
              onScroll={() => setConversationMenu(null)}
              style={{
                flex: "1 1 auto",
                minHeight: 0,
                display: "flex",
                flexDirection: "column",
                overflowY: sidebarOpen ? "auto" : "hidden",
                paddingTop: "8px",
                opacity: sidebarOpen ? 1 : 0,
                visibility: sidebarOpen ? "visible" : "hidden",
                pointerEvents: sidebarOpen ? "auto" : "none",
                transition: sidebarOpen
                  ? "opacity 120ms ease 80ms"
                  : "opacity 80ms ease, visibility 0s linear 200ms",
              }}
            >
              {!user ? (
                <div style={{ fontSize: "13px", color: "var(--muted)", padding: "8px 10px", lineHeight: 1.5 }}>
                  <button onClick={() => setShowAuth(true)} style={{ color: "var(--text)", background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: "inherit", fontSize: "13px", textDecoration: "underline" }}>Sign in</button>
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
                      <div style={{ display: "flex", flexShrink: 0 }}>
                        <button
                          type="button"
                          onClick={(e) => toggleConversationMenu(e, conv.id)}
                          data-pe-conversation-menu-trigger
                          aria-label={`More options for ${conv.title}`}
                          aria-haspopup="menu"
                          aria-expanded={conversationMenu?.id === conv.id}
                          style={{ background: "none", border: "none", color: "var(--muted)", cursor: "pointer", display: "flex", padding: "4px", borderRadius: "6px" }}
                          onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = "var(--text)"; }}
                          onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = "var(--muted)"; }}
                        >
                          <IconDots size={16} />
                        </button>
                      </div>
                    </div>
                    );
                  })}
                </div>
              )}
              {modelVersion && (
                <div style={{ marginTop: "auto", padding: "12px 8px 4px", flexShrink: 0, whiteSpace: "nowrap", textAlign: "center", color: "var(--faint)", fontSize: "11px" }}>
                  {modelVersion}
                </div>
              )}
            </div>
            <div style={{ borderTop: "1px solid var(--border)", paddingTop: "8px", width: "100%" }}>
              <ThemeSelector compact={!sidebarOpen} preference={themePreference} onChange={setThemePreference} />
              {user ? (
                <AccountMenu compact={!sidebarOpen} email={user.email || "Account"} onSignOut={signOut} />
              ) : (
                <button
                  type="button"
                  onClick={() => setShowAuth(true)}
                  data-tip-right={sidebarOpen ? undefined : "Sign in"}
                  aria-label="Sign in"
                  style={sidebarButtonStyle}
                  onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-hover)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                >
                  <span style={sidebarIconStyle}>
                    <span style={{ width: "28px", height: "28px", border: "1px solid var(--border)", borderRadius: "999px", display: "flex", alignItems: "center", justifyContent: "center" }}>
                      <IconUser size={16} />
                    </span>
                  </span>
                  <span data-pe-sidebar-label aria-hidden={!sidebarOpen} style={sidebarLabelStyle}>Sign in</span>
                </button>
              )}
            </div>
          </div>
        )}

        {conversationMenu && (
          <div
            data-pe-conversation-menu
            role="menu"
            aria-label="Chat actions"
            style={{
              position: "fixed",
              top: `${conversationMenu.top}px`,
              left: `${conversationMenu.left}px`,
              width: "184px",
              zIndex: 100,
              padding: "6px",
              border: "1px solid var(--border)",
              borderRadius: "16px",
              background: "var(--surface)",
              boxShadow: "0 10px 28px rgba(0,0,0,0.14)",
              boxSizing: "border-box",
            }}
          >
            <button
              type="button"
              role="menuitem"
              onClick={(e) => shareConversation(e, conversationMenu.id)}
              style={{ width: "100%", display: "flex", alignItems: "center", gap: "10px", padding: "10px 12px", border: "none", borderRadius: "10px", background: "transparent", color: copiedShareId === conversationMenu.id ? "var(--accent)" : "var(--text)", cursor: "pointer", fontFamily: "inherit", fontSize: "14px", textAlign: "left" }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-hover)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
            >
              <IconShare size={18} />
              {copiedShareId === conversationMenu.id ? "Share link copied" : "Share"}
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={(e) => { deleteConversation(e, conversationMenu.id); setConversationMenu(null); }}
              style={{ width: "100%", display: "flex", alignItems: "center", gap: "10px", padding: "10px 12px", border: "none", borderRadius: "10px", background: "transparent", color: "#dc2626", cursor: "pointer", fontFamily: "inherit", fontSize: "14px", textAlign: "left" }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-hover)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
            >
              <IconTrash size={18} />
              Delete
            </button>
          </div>
        )}

        {/* Chat area */}
        <div data-pe-chat style={{ flex: 1, padding: "16px 24px max(24px, env(safe-area-inset-bottom))", minWidth: 0, minHeight: "calc(100dvh - var(--pe-shell-h))", boxSizing: "border-box", display: "flex", flexDirection: "column", justifyContent: hasMessages ? "flex-start" : "center", alignItems: "stretch" }}>
          {!hasMessages && (
            <div style={{ width: "100%", maxWidth: "760px", margin: "0 auto", textAlign: "center", marginBottom: "20px" }}>
              <h1 style={{ fontSize: "30px", fontWeight: 500, color: "var(--text)", margin: 0, letterSpacing: "-0.01em" }}>What&apos;s on your mind today?</h1>
            </div>
          )}

          {hasMessages && (
            <div
              ref={transcriptRef}
              data-pe-transcript
              style={{ width: "100%" }}
            >
            <div style={{ width: "100%", maxWidth: "760px", margin: "0 auto", marginBottom: "20px", paddingBottom: `${composerHeight + 36}px`, boxSizing: "border-box" }}>
              {messages.map((msg, idx) => (
                <div key={idx} style={{ marginBottom: "8px" }}>
                  {msg.role === "user" ? (
                    <div style={{ display: "flex", justifyContent: "flex-end", padding: "10px 0" }}>
                      <div style={{ background: "var(--user-bubble)", color: "var(--text)", padding: "10px 16px", borderRadius: "22px", maxWidth: "75%", whiteSpace: "pre-wrap", fontSize: "15px", lineHeight: 1.55 }}>
                        {msg.attachment && (
                          <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: msg.content.startsWith("[Attached image:") ? 0 : "6px", color: "var(--text-2)", fontSize: "12px", lineHeight: 1.3 }}>
                            <IconPaperclip size={14} style={{ flexShrink: 0 }} />
                            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{msg.attachment.name}</span>
                          </div>
                        )}
                        {!msg.attachment || !msg.content.startsWith("[Attached image:") ? msg.content : null}
                      </div>
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
                          {msg.isComplete && idx === messages.length - 1 && (
                            <>
                              <button
                                type="button"
                                onClick={() => { setReportError(null); setReportOpen(true); }}
                                data-tip="Report this thread"
                                style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "11px", color: "var(--muted)", background: "none", border: "none", cursor: "pointer", padding: 0, fontFamily: "inherit" }}
                              >
                                <IconBug size={12} /> Report issue
                              </button>
                              <button
                                type="button"
                                onClick={downloadConversation}
                                data-tip="Download this conversation as Markdown"
                                style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "11px", color: "var(--muted)", background: "none", border: "none", cursor: "pointer", padding: 0, fontFamily: "inherit" }}
                              >
                                <IconDownload size={12} /> Download .md
                              </button>
                            </>
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
              <div ref={bottomRef} aria-hidden="true" />
            </div>
            </div>
          )}

          {/* Input */}
          <div
            ref={composerRef}
            data-pe-composer
            data-fixed={hasMessages ? "true" : "false"}
            style={{
              width: hasMessages ? "calc(100% - 48px)" : "100%",
              maxWidth: "760px",
              margin: hasMessages ? 0 : "0 auto",
              position: hasMessages ? "fixed" : "relative",
              left: hasMessages ? `calc(${sidebarOffset}px + (100% - ${sidebarOffset}px) / 2)` : undefined,
              bottom: hasMessages ? "max(24px, env(safe-area-inset-bottom))" : undefined,
              transform: hasMessages ? "translateX(-50%)" : undefined,
              transition: hasMessages ? "left 200ms ease" : undefined,
              zIndex: hasMessages ? 50 : undefined,
              flexShrink: 0,
            }}
          >
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
                {showAnimatedPlaceholder && (
                  <div aria-hidden="true" style={{ position: "absolute", top: "4px", left: "0", fontSize: "16px", lineHeight: 1.5, color: "var(--faint)", pointerEvents: "none" }}>
                    {animatedPlaceholder}
                    <span style={{ display: "inline-block", width: "2px", height: "1em", background: "var(--muted)", marginLeft: "1px", verticalAlign: "text-bottom", animation: "blink 1s step-end infinite" }} />
                    <style>{`@keyframes blink{50%{opacity:0}}`}</style>
                  </div>
                )}
                {showStaticPlaceholder && (
                  <div aria-hidden="true" style={{ position: "absolute", top: "4px", left: "0", fontSize: "16px", lineHeight: 1.5, color: "var(--faint)", pointerEvents: "none" }}>
                    Ask anything
                  </div>
                )}
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => { setInput(e.target.value); autoResize(e.target); }}
                  onFocus={() => setIsInputFocused(true)}
                  onBlur={() => setIsInputFocused(false)}
                  onKeyDown={handleKeyDown}
                  disabled={isStreaming}
                  rows={1}
                  aria-label="Ask a question"
                  style={{ width: "100%", maxHeight: "240px", background: "transparent", border: "none", outline: "none", fontSize: "16px", lineHeight: 1.5, color: "var(--text)", fontFamily: "inherit", resize: "none", padding: "4px 0", opacity: isStreaming ? 0.5 : 1, overflowY: "hidden", caretColor: showAnimatedPlaceholder ? "transparent" : "var(--text)", boxSizing: "border-box" }}
                />
              </div>
              <div style={{ marginTop: "6px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
                <div style={{ display: "inline-flex", alignItems: "center", gap: "6px", minWidth: 0 }}>
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isStreaming}
                    data-tip-left="Attach an image (JPG, PNG, WEBP, or GIF, up to 5MB)"
                    aria-label="Attach image"
                    style={{ width: "32px", height: "32px", borderRadius: "999px", background: "transparent", color: "var(--text-3)", border: "none", cursor: isStreaming ? "not-allowed" : "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center", padding: 0, opacity: isStreaming ? 0.5 : 1, transition: "background 120ms, color 120ms" }}
                  >
                    <IconPaperclip size={18} />
                  </button>
                  <button
                    type="button"
                    onClick={() => setChartsMode((v) => !v)}
                    disabled={isStreaming}
                    data-tip-right={chartsMode
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
                  bottom: "calc(100% + 6px)",
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
                {STARTER_PROMPTS.map((prompt) => (
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

      <ChatSearchDialog
        open={chatSearchOpen}
        recent={recentSearchResults}
        search={searchChats}
        onClose={() => setChatSearchOpen(false)}
        onSelect={(result) => {
          setChatSearchOpen(false);
          void loadConversation(result);
        }}
      />

      <AuthDialog
        open={showAuth}
        onClose={() => setShowAuth(false)}
        signIn={signIn}
        signUp={signUp}
      />

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
