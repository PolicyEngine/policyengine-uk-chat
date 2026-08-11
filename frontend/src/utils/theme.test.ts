import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  normaliseThemePreference,
  resolveTheme,
  type ThemePreference,
  useThemePreference,
} from "./theme";

beforeEach(() => {
  const values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      clear: () => values.clear(),
      getItem: (key: string) => values.get(key) ?? null,
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, value),
    },
  });
});

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
  Reflect.deleteProperty(window, "localStorage");
  vi.unstubAllGlobals();
});

describe("theme preferences", () => {
  it.each<[string | null, ThemePreference]>([
    [null, "auto"],
    ["auto", "auto"],
    ["light", "light"],
    ["dark", "dark"],
    ["unexpected", "auto"],
  ])("normalises stored value %j to %s", (stored, expected) => {
    expect(normaliseThemePreference(stored)).toBe(expected);
  });

  it("resolves auto from the current system preference", () => {
    expect(resolveTheme("auto", true)).toBe("dark");
    expect(resolveTheme("auto", false)).toBe("light");
  });

  it("keeps explicit preferences independent of the system", () => {
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("updates auto mode when the browser system preference changes", () => {
    let changeListener: ((event: MediaQueryListEvent) => void) | undefined;
    const media = {
      matches: false,
      addEventListener: vi.fn((_event, listener) => {
        changeListener = listener;
      }),
      removeEventListener: vi.fn(),
    };
    vi.stubGlobal("matchMedia", vi.fn(() => media));
    window.localStorage.setItem("theme", "auto");

    const { unmount } = renderHook(() => useThemePreference());
    expect(document.documentElement).not.toHaveAttribute("data-theme");

    act(() => changeListener?.({ matches: true } as MediaQueryListEvent));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");

    unmount();
    expect(media.removeEventListener).toHaveBeenCalledWith(
      "change",
      expect.any(Function),
    );
  });
});
