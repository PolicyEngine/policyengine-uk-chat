"use client";
import { TooltipData, CHART_TYPOGRAPHY } from "./types";

interface TooltipProps {
  data: TooltipData | null;
  containerRef: React.RefObject<HTMLDivElement | null>;
}

export function Tooltip({ data, containerRef }: TooltipProps) {
  if (!data) return null;
  const containerRect = containerRef.current?.getBoundingClientRect();
  if (!containerRect) return null;
  const tooltipWidth = 180;
  const flipX = data.x + tooltipWidth + 12 > containerRect.width;

  return (
    <div style={{ position: "absolute", left: flipX ? data.x - tooltipWidth - 12 : data.x + 12, top: data.y - 8, background: "#fff", boxShadow: "0 4px 20px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04)", padding: "10px 14px", pointerEvents: "none", zIndex: 100, minWidth: "140px", maxWidth: `${tooltipWidth}px`, fontFamily: CHART_TYPOGRAPHY.fontFamily }}>
      {data.title && <div style={{ fontSize: 11, fontWeight: 500, color: "#9e9a90", letterSpacing: "0.03em", marginBottom: "6px" }}>{data.title}</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
        {data.values.map((item, i) => (
          <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "20px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              {item.color && <div style={{ width: "8px", height: "8px", background: item.color, flexShrink: 0 }} />}
              <span style={{ fontSize: 11, color: "#6b6860" }}>{item.label}</span>
            </div>
            <span style={{ fontSize: 11, fontWeight: 600, color: "#1c1a17", fontVariantNumeric: "tabular-nums" }}>{item.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
