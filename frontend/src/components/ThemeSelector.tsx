"use client";

import { useEffect, useRef, useState } from "react";
import {
  IconCheck,
  IconDeviceDesktop,
  IconMoon,
  IconSun,
} from "@tabler/icons-react";
import type { ThemePreference } from "@/utils/theme";

interface ThemeSelectorProps {
  compact: boolean;
  preference: ThemePreference;
  onChange: (preference: ThemePreference) => void;
}

const OPTIONS: Array<{
  value: ThemePreference;
  label: string;
  icon: typeof IconSun;
}> = [
  { value: "light", label: "Light", icon: IconSun },
  { value: "dark", label: "Dark", icon: IconMoon },
  { value: "auto", label: "Auto", icon: IconDeviceDesktop },
];

export default function ThemeSelector({
  compact,
  preference,
  onChange,
}: ThemeSelectorProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const selected = OPTIONS.find((option) => option.value === preference) || OPTIONS[2];
  const SelectedIcon = selected.icon;

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div ref={rootRef} style={{ position: "relative", width: compact ? "auto" : "100%" }}>
      <button
        type="button"
        aria-label={`Appearance: ${selected.label}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        style={{
          width: compact ? "auto" : "100%",
          background: open ? "var(--surface-hover)" : "transparent",
          border: "none",
          cursor: "pointer",
          padding: compact ? "10px" : "9px 10px",
          borderRadius: "10px",
          display: "flex",
          alignItems: "center",
          justifyContent: compact ? "center" : "flex-start",
          gap: "10px",
          color: "var(--text-2)",
          fontFamily: "inherit",
          fontSize: "13px",
        }}
      >
        <SelectedIcon size={compact ? 18 : 16} aria-hidden="true" />
        {!compact && <span>Appearance: {selected.label}</span>}
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Choose appearance"
          style={{
            position: "absolute",
            left: compact ? "calc(100% + 10px)" : 0,
            bottom: compact ? 0 : "calc(100% + 8px)",
            width: "180px",
            padding: "6px",
            border: "1px solid var(--border)",
            borderRadius: "12px",
            background: "var(--surface)",
            boxShadow: "0 10px 28px rgba(0,0,0,0.16)",
            zIndex: 250,
          }}
        >
          {OPTIONS.map((option) => {
            const OptionIcon = option.icon;
            const checked = option.value === preference;
            return (
              <button
                key={option.value}
                type="button"
                role="menuitemradio"
                aria-checked={checked}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
                style={{ width: "100%", display: "flex", alignItems: "center", gap: "9px", padding: "9px 10px", border: "none", borderRadius: "8px", background: checked ? "var(--surface-hover)" : "transparent", color: "var(--text)", cursor: "pointer", fontFamily: "inherit", fontSize: "13px", textAlign: "left" }}
              >
                <OptionIcon size={16} aria-hidden="true" />
                <span style={{ flex: 1 }}>{option.label}</span>
                {checked && <IconCheck size={14} aria-hidden="true" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
