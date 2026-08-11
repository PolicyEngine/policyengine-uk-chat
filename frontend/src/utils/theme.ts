"use client";

import { useCallback, useEffect, useState } from "react";

export type ThemePreference = "light" | "dark" | "auto";
export type ResolvedTheme = "light" | "dark";

const THEME_STORAGE_KEY = "theme";
const DARK_MODE_QUERY = "(prefers-color-scheme: dark)";

export function normaliseThemePreference(
  value: string | null,
): ThemePreference {
  return value === "light" || value === "dark" || value === "auto"
    ? value
    : "auto";
}

export function resolveTheme(
  preference: ThemePreference,
  systemPrefersDark: boolean,
): ResolvedTheme {
  if (preference === "auto") return systemPrefersDark ? "dark" : "light";
  return preference;
}

function readPreference(): ThemePreference {
  if (typeof window === "undefined") return "auto";
  try {
    return normaliseThemePreference(window.localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "auto";
  }
}

function readSystemPreference(): boolean {
  return typeof window !== "undefined" && window.matchMedia(DARK_MODE_QUERY).matches;
}

function applyTheme(theme: ResolvedTheme) {
  if (typeof document === "undefined") return;
  if (theme === "dark") document.documentElement.setAttribute("data-theme", "dark");
  else document.documentElement.removeAttribute("data-theme");
}

export function useThemePreference() {
  const [preference, setPreferenceState] = useState<ThemePreference>(readPreference);
  const [systemPrefersDark, setSystemPrefersDark] = useState(readSystemPreference);
  const resolvedTheme = resolveTheme(preference, systemPrefersDark);

  useEffect(() => {
    const media = window.matchMedia(DARK_MODE_QUERY);
    const update = (event: MediaQueryListEvent | MediaQueryList) => {
      setSystemPrefersDark(event.matches);
    };
    update(media);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    applyTheme(resolvedTheme);
  }, [resolvedTheme]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {}
  }, []);

  return { preference, resolvedTheme, setPreference };
}
