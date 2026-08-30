import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useLocalStorage } from "./useLocalStorage";


let values: Map<string, string>;

beforeEach(() => {
  values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, value),
    },
  });
});

afterEach(() => {
  Reflect.deleteProperty(window, "localStorage");
});

describe("useLocalStorage", () => {
  it("defaults when unset and restores a stored value after remount", () => {
    const first = renderHook(() => useLocalStorage("debug", false));
    expect(first.result.current[0]).toBe(false);

    act(() => first.result.current[1](true));
    expect(first.result.current[0]).toBe(true);
    expect(values.get("debug")).toBe("true");
    first.unmount();

    const restored = renderHook(() => useLocalStorage("debug", false));
    expect(restored.result.current[0]).toBe(true);
  });

  it("uses the default for invalid stored JSON", () => {
    values.set("debug", "not-json");

    const { result } = renderHook(() => useLocalStorage("debug", false));

    expect(result.current[0]).toBe(false);
  });

  it("uses the default when a validator rejects parsed storage", () => {
    values.set("debug", JSON.stringify("yes"));

    const { result } = renderHook(() => useLocalStorage(
      "debug",
      false,
      (value): value is boolean => typeof value === "boolean",
    ));

    expect(result.current[0]).toBe(false);
  });

  it("supports functional updates and storage events", () => {
    const { result } = renderHook(() => useLocalStorage("debug", false));

    act(() => result.current[1]((current) => !current));
    expect(result.current[0]).toBe(true);

    act(() => {
      window.dispatchEvent(new StorageEvent("storage", {
        key: "debug",
        newValue: "false",
      }));
    });
    expect(result.current[0]).toBe(false);
  });
});
