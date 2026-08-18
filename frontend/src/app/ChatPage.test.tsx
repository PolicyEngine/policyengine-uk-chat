import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChatPage from "./ChatPage";

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
});

describe("ChatPage", () => {
  it("restores the automated placeholder when an unsent input is cleared", () => {
    render(<ChatPage />);

    const input = screen.getByRole("textbox", { name: "Ask a question" });
    expect(input).toHaveFocus();
    expect(screen.getByText("Ask anything")).toBeInTheDocument();
    expect(input.style.caretColor).toBe("transparent");

    fireEvent.click(input);
    expect(screen.queryByText("Ask anything")).not.toBeInTheDocument();
    expect(input.style.caretColor).toBe("var(--text)");

    fireEvent.change(input, { target: { value: "How does income tax work?" } });
    expect(input.style.caretColor).toBe("var(--text)");

    fireEvent.change(input, { target: { value: "" } });
    expect(screen.getByText("Ask anything")).toBeInTheDocument();
    expect(input.style.caretColor).toBe("transparent");
  });
});
