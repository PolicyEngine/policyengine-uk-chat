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
  it("shows active wording during tool processing and completed wording after output appears", async () => {
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
        type: "tool_start",
        tool_name: "run_python",
        tool_id: "tool-1",
      }));
    });
    expect(screen.getByText("Working through the problem")).toBeInTheDocument();

    await act(async () => {
      streamController?.enqueue(encodeStreamEvent({
        type: "chunk",
        content: "Your tax result is £1,000.",
      }));
      streamController?.enqueue(encodeStreamEvent({ type: "done" }));
      streamController?.close();
    });
    expect(screen.getByText("Worked through the problem")).toBeInTheDocument();
  });

  it("keeps active wording when a completed tool response has no visible output", async () => {
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
        type: "tool_start",
        tool_name: "run_python",
        tool_id: "tool-1",
      }));
      streamController?.enqueue(encodeStreamEvent({ type: "chunk", content: "   " }));
      streamController?.enqueue(encodeStreamEvent({ type: "done" }));
      streamController?.close();
    });

    expect(screen.getByText("Working through the problem")).toBeInTheDocument();
    expect(screen.queryByText("Worked through the problem")).not.toBeInTheDocument();
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
