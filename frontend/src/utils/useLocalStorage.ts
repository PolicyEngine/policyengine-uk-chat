"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

const parseStoredValue = <T,>(
  stored: string,
  fallback: T,
  isValid?: (value: unknown) => value is T,
): T => {
  try {
    const parsed: unknown = JSON.parse(stored);
    return isValid && !isValid(parsed) ? fallback : parsed as T;
  } catch {
    return fallback;
  }
};

export function useLocalStorage<T>(
  key: string,
  defaultValue: T,
  isValid?: (value: unknown) => value is T,
): readonly [T, Dispatch<SetStateAction<T>>] {
  const defaultValueRef = useRef(defaultValue);
  const [value, setValueState] = useState(defaultValue);

  useEffect(() => {
    defaultValueRef.current = defaultValue;
  }, [defaultValue]);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(key);
      setValueState(
        stored === null
          ? defaultValueRef.current
          : parseStoredValue(stored, defaultValueRef.current, isValid),
      );
    } catch {
      setValueState(defaultValueRef.current);
    }
  }, [isValid, key]);

  useEffect(() => {
    const updateFromAnotherDocument = (event: StorageEvent) => {
      if (event.key !== key) return;
      setValueState(
        event.newValue === null
          ? defaultValueRef.current
          : parseStoredValue(event.newValue, defaultValueRef.current, isValid),
      );
    };
    window.addEventListener("storage", updateFromAnotherDocument);
    return () => window.removeEventListener("storage", updateFromAnotherDocument);
  }, [isValid, key]);

  const setValue = useCallback<Dispatch<SetStateAction<T>>>((nextValue) => {
    setValueState((currentValue) => {
      const resolved = typeof nextValue === "function"
        ? (nextValue as (current: T) => T)(currentValue)
        : nextValue;
      try {
        window.localStorage.setItem(key, JSON.stringify(resolved));
      } catch {
        // Browser privacy settings or exhausted storage must not disable the UI.
      }
      return resolved;
    });
  }, [key]);

  return [value, setValue] as const;
}
