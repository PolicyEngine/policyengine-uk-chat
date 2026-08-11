"use client";

import { useEffect, useRef, useState } from "react";
import { IconChevronDown, IconLogout } from "@tabler/icons-react";

interface AccountMenuProps {
  compact: boolean;
  email: string;
  onSignOut: () => void | Promise<void>;
}

export default function AccountMenu({
  compact,
  email,
  onSignOut,
}: AccountMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

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
        aria-label="Open account settings"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        style={compact
          ? {
              background: "var(--accent)",
              color: "var(--accent-fg)",
              border: "none",
              cursor: "pointer",
              width: "32px",
              height: "32px",
              borderRadius: "999px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "13px",
              fontWeight: 600,
              textTransform: "uppercase",
            }
          : {
              width: "100%",
              display: "flex",
              alignItems: "center",
              gap: "10px",
              padding: "8px 10px",
              border: "none",
              borderRadius: "10px",
              background: open ? "var(--surface-hover)" : "transparent",
              color: "var(--text)",
              cursor: "pointer",
              fontFamily: "inherit",
              textAlign: "left",
            }}
      >
        <span
          aria-hidden="true"
          style={{
            width: compact ? "auto" : "28px",
            height: compact ? "auto" : "28px",
            borderRadius: "999px",
            background: compact ? "transparent" : "var(--accent)",
            color: compact ? "inherit" : "var(--accent-fg)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "12px",
            fontWeight: 600,
            flexShrink: 0,
            textTransform: "uppercase",
          }}
        >
          {(email || "?").slice(0, 1)}
        </span>
        {!compact && (
          <>
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "13px" }}>
              {email}
            </span>
            <IconChevronDown size={14} aria-hidden="true" />
          </>
        )}
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Account settings"
          style={{
            position: "absolute",
            left: compact ? "calc(100% + 10px)" : 0,
            bottom: compact ? 0 : "calc(100% + 8px)",
            width: "220px",
            padding: "6px",
            border: "1px solid var(--border)",
            borderRadius: "12px",
            background: "var(--surface)",
            boxShadow: "0 10px 28px rgba(0,0,0,0.16)",
            zIndex: 250,
            boxSizing: "border-box",
          }}
        >
          <div style={{ padding: "8px 10px", color: "var(--muted)", fontSize: "12px", overflow: "hidden", textOverflow: "ellipsis" }}>
            {email}
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void onSignOut();
            }}
            style={{ width: "100%", display: "flex", alignItems: "center", gap: "9px", padding: "9px 10px", border: "none", borderRadius: "8px", background: "transparent", color: "var(--text)", cursor: "pointer", fontFamily: "inherit", fontSize: "13px", textAlign: "left" }}
          >
            <IconLogout size={16} aria-hidden="true" />
            Log out
          </button>
        </div>
      )}
    </div>
  );
}
