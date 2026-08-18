import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChatPage from "./ChatPage";

const FIRST_EXAMPLE_QUERY = "What's the current personal allowance?";

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
