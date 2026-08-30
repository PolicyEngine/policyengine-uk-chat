import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChatPage from "./ChatPage";

const FIRST_EXAMPLE_QUERY = "What's the current personal allowance?";

const encodeStreamEvent = (event: Record<string, unknown>): Uint8Array =>
  new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`);

vi.mock("@/utils/AuthContext", () => ({
  useAuth: () => ({
    user: null,
    loading: false,
    signIn: vi.fn(),
    signUp: vi.fn(),
    signOut: vi.fn(),
  }),
}));

vi.mock("@/utils/theme", () => ({
  useThemePreference: () => ({
    preference: "auto",
    setPreference: vi.fn(),
  }),
}));

vi.mock("@mantine/core", () => ({
  Loader: () => <span data-testid="loader" />,
}));

beforeEach(() => {
  const storedValues = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => storedValues.get(key) ?? null,
      removeItem: (key: string) => storedValues.delete(key),
      setItem: (key: string, value: string) => storedValues.set(key, value),
    },
  });
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
  vi.stubGlobal("ResizeObserver", class {
    disconnect() {}
    observe() {}
    unobserve() {}
  });
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  Reflect.deleteProperty(Element.prototype, "scrollIntoView");
  Reflect.deleteProperty(window, "localStorage");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("ChatPage", () => {
  it("persists the sidebar debug setting, sends an idempotent turn identifier, and renders sanitized debug activity outside the transcript", async () => {
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    vi.mocked(fetch).mockImplementation((input) =>
      String(input).includes("chat/message")
        ? Promise.resolve(new Response(stream, { status: 200 }))
        : new Promise<Response>(() => {}),
    );

    render(<ChatPage />);
    const debug = screen.getByRole("button", { name: "Debug activity" });
    expect(debug).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(debug);
    expect(debug).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem("policyengine-uk-chat:debug")).toBe("true");
    fireEvent.change(screen.getByRole("textbox", { name: "Ask a question" }), {
      target: { value: "Calculate a reform" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await act(async () => {
      streamController?.enqueue(encodeStreamEvent({
        type: "invocation_activity",
        phase: "finished",
        invocation: {
          turn_id: "turn-1",
          invocation_id: "private-1",
          parent_invocation_id: null,
          sequence: 1,
          kind: "tool",
          identifier: "validate_reform",
          version: "1",
          visibility: "private",
          started_at: "2026-01-01T00:00:00Z",
          completed_at: "2026-01-01T00:00:00Z",
          duration_ms: 4,
          status: "completed",
          summary: "tool validate_reform completed",
          debug_input: { reform_instruction: "Raise the allowance" },
          debug_output: { status: "completed" },
        },
      }));
      streamController?.enqueue(encodeStreamEvent({ type: "chunk", content: "Done." }));
      streamController?.enqueue(encodeStreamEvent({ type: "done" }));
      streamController?.close();
    });

    const messageCall = vi.mocked(fetch).mock.calls.find(([input]) =>
      String(input).includes("chat/message"),
    );
    const body = JSON.parse(String(messageCall?.[1]?.body));
    expect(body.debug).toBe(true);
    expect(body.turn_id).toEqual(expect.any(String));
    expect(screen.getByRole("region", { name: "Invocation activity" })).toHaveTextContent("validate_reform");
    expect(screen.getByText("private")).toBeInTheDocument();
    const details = screen.getByRole("button", {
      name: "Toggle validate_reform details",
    });
    expect(details).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(details);
    expect(screen.getByText("Input")).toBeInTheDocument();
    expect(screen.getByText("Output")).toBeInTheDocument();
  });

  it("keeps response loading visible while an invocation is running", async () => {
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    vi.mocked(fetch).mockImplementation((input) =>
      String(input).includes("chat/message")
        ? Promise.resolve(new Response(stream, { status: 200 }))
        : new Promise<Response>(() => {}),
    );

    render(<ChatPage />);
    fireEvent.change(screen.getByRole("textbox", { name: "Ask a question" }), {
      target: { value: "Calculate my tax" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await act(async () => {
      streamController?.enqueue(encodeStreamEvent({
        type: "invocation_activity",
        phase: "started",
        invocation: {
          turn_id: "turn-1",
          invocation_id: "household-1",
          parent_invocation_id: null,
          sequence: 1,
          kind: "capability",
          identifier: "household_calculation",
          version: "1",
          visibility: "public",
          started_at: "2026-01-01T00:00:00Z",
          completed_at: null,
          duration_ms: null,
          status: "running",
          summary: "Calculating household results",
        },
      }));
    });
    expect(screen.getByRole("status", { name: "Generating response" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Invocation activity" })).toHaveTextContent("household_calculation");
    expect(screen.getByText("running")).toBeInTheDocument();

    await act(async () => {
      streamController?.enqueue(encodeStreamEvent({
        type: "invocation_activity",
        phase: "finished",
        invocation: {
          turn_id: "turn-1",
          invocation_id: "household-1",
          parent_invocation_id: null,
          sequence: 1,
          kind: "capability",
          identifier: "household_calculation",
          version: "1",
          visibility: "public",
          started_at: "2026-01-01T00:00:00Z",
          completed_at: "2026-01-01T00:00:01Z",
          duration_ms: 1000,
          status: "completed",
          summary: "Household calculation completed",
        },
      }));
      streamController?.enqueue(encodeStreamEvent({
        type: "chunk",
        content: "Your tax result is £1,000.",
      }));
      streamController?.enqueue(encodeStreamEvent({ type: "done" }));
      streamController?.close();
    });
    expect(screen.getByText("Your tax result is £1,000.")).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "Generating response" })).not.toBeInTheDocument();
    expect(screen.getByText(/completed · 1000 ms/)).toBeInTheDocument();
  });

  it("shows the automated placeholder only for an empty, blurred new-chat input", () => {
    render(<ChatPage />);

    const input = screen.getByRole("textbox", { name: "Ask a question" });
    expect(input).not.toHaveFocus();
    expect(screen.queryByText("Ask anything")).not.toBeInTheDocument();
    expect(input.style.caretColor).toBe("transparent");

    act(() => input.focus());
    expect(input).toHaveFocus();
    expect(screen.queryByText("Ask anything")).not.toBeInTheDocument();
    expect(input.style.caretColor).toBe("var(--text)");

    fireEvent.change(input, { target: { value: "How does income tax work?" } });
    expect(input.style.caretColor).toBe("var(--text)");

    fireEvent.change(input, { target: { value: "" } });
    expect(screen.queryByText("Ask anything")).not.toBeInTheDocument();
    expect(input.style.caretColor).toBe("var(--text)");

    act(() => input.blur());
    expect(input).not.toHaveFocus();
    expect(screen.queryByText("Ask anything")).not.toBeInTheDocument();
    expect(input.style.caretColor).toBe("transparent");
  });

  it("holds the complete example before deleting and restarts after focus", () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { unmount } = render(<ChatPage />);
    const input = screen.getByRole("textbox", { name: "Ask a question" });

    for (let index = 0; index < FIRST_EXAMPLE_QUERY.length; index += 1) {
      act(() => vi.advanceTimersByTime(50));
    }
    expect(screen.getByText(FIRST_EXAMPLE_QUERY)).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1_999));
    expect(screen.getByText(FIRST_EXAMPLE_QUERY)).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByText(FIRST_EXAMPLE_QUERY)).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(30));
    expect(screen.getByText(FIRST_EXAMPLE_QUERY.slice(0, -1))).toBeInTheDocument();

    act(() => input.focus());
    act(() => input.blur());
    act(() => vi.advanceTimersByTime(50));
    expect(screen.getByText(FIRST_EXAMPLE_QUERY[0])).toBeInTheDocument();

    unmount();
  });
});
