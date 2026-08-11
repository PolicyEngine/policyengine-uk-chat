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
    <div ref={rootRef} style={{ position: "relative", width: "100%" }}>
      <button
        type="button"
        aria-label="Open account settings"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        style={{
          width: "100%",
          height: "40px",
          display: "flex",
          alignItems: "center",
          padding: 0,
          border: "none",
          borderRadius: "10px",
          background: open ? "var(--surface-hover)" : "transparent",
          color: "var(--text)",
          cursor: "pointer",
          fontFamily: "inherit",
          textAlign: "left",
          overflow: "hidden",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: "44px",
            height: "40px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <span
            style={{
              width: "28px",
              height: "28px",
              borderRadius: "999px",
              background: "var(--accent)",
              color: "var(--accent-fg)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "12px",
              fontWeight: 600,
              textTransform: "uppercase",
            }}
          >
            {(email || "?").slice(0, 1)}
          </span>
        </span>
        <span
          data-pe-sidebar-label
          aria-hidden={compact}
          style={{
            flex: compact ? "0 0 auto" : 1,
            minWidth: 0,
            maxWidth: compact ? 0 : "190px",
            opacity: compact ? 0 : 1,
            overflow: "hidden",
            visibility: compact ? "hidden" : "visible",
            display: "flex",
            alignItems: "center",
            gap: "8px",
            transition: compact
              ? "opacity 80ms ease, max-width 200ms ease, visibility 0s linear 200ms"
              : "opacity 120ms ease 80ms, max-width 200ms ease",
          }}
        >
          <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "13px" }}>
            {compact ? null : email}
          </span>
          <IconChevronDown size={14} aria-hidden="true" style={{ flexShrink: 0, marginRight: "10px" }} />
        </span>
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
