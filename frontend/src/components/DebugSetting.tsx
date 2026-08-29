"use client";

import { IconBug } from "@tabler/icons-react";


interface DebugSettingProps {
  compact: boolean;
  enabled: boolean;
  disabled?: boolean;
  onChange: (enabled: boolean) => void;
}

export default function DebugSetting({
  compact,
  enabled,
  disabled = false,
  onChange,
}: DebugSettingProps) {
  return (
    <button
      type="button"
      aria-label="Debug activity"
      aria-pressed={enabled}
      disabled={disabled}
      data-tip-right={compact ? `Debug activity: ${enabled ? "On" : "Off"}` : undefined}
      onClick={() => onChange(!enabled)}
      style={{
        width: "100%",
        height: "40px",
        background: enabled ? "var(--accent-15)" : "transparent",
        border: "none",
        borderRadius: "10px",
        color: enabled ? "var(--accent)" : "var(--text-2)",
        cursor: disabled ? "not-allowed" : "pointer",
        display: "flex",
        alignItems: "center",
        padding: 0,
        opacity: disabled ? 0.5 : 1,
        overflow: "hidden",
        fontFamily: "inherit",
        fontSize: "13px",
        textAlign: "left",
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
        <IconBug size={18} />
      </span>
      <span
        data-pe-sidebar-label
        aria-hidden={compact}
        style={{
          minWidth: 0,
          maxWidth: compact ? 0 : "190px",
          opacity: compact ? 0 : 1,
          overflow: "hidden",
          whiteSpace: "nowrap",
          visibility: compact ? "hidden" : "visible",
          transition: compact
            ? "opacity 80ms ease, max-width 200ms ease, visibility 0s linear 200ms"
            : "opacity 120ms ease 80ms, max-width 200ms ease",
        }}
      >
        Debug activity: {enabled ? "On" : "Off"}
      </span>
    </button>
  );
}
