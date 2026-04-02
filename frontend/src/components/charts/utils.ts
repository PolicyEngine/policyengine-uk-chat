import { AxisConfig, CHART_COLORS } from "./types";

export function formatValue(value: number, format?: AxisConfig["format"]): string {
  if (value === null || value === undefined || isNaN(value)) return "—";
  switch (format) {
    case "currency":
      if (Math.abs(value) >= 1e9) return `£${(value / 1e9).toFixed(1)}bn`;
      if (Math.abs(value) >= 1e6) return `£${(value / 1e6).toFixed(1)}m`;
      if (Math.abs(value) >= 1e3) return `£${(value / 1e3).toFixed(1)}k`;
      return `£${value.toLocaleString("en-GB", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
    case "percent":
      return `${value.toFixed(1)}%`;
    case "percent_decimal":
      return `${(value * 100).toFixed(1)}%`;
    case "compact":
      if (Math.abs(value) >= 1e9) return `${(value / 1e9).toFixed(1)}bn`;
      if (Math.abs(value) >= 1e6) return `${(value / 1e6).toFixed(1)}m`;
      if (Math.abs(value) >= 1e3) return `${(value / 1e3).toFixed(1)}k`;
      return value.toLocaleString("en-GB");
    case "year":
      return String(Math.round(value));
    default:
      return value.toLocaleString("en-GB", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  }
}

export function getSeriesColor(index: number, customColor?: string): string {
  return customColor || CHART_COLORS.series[index % CHART_COLORS.series.length];
}

export function getDashArray(style?: "solid" | "dashed" | "dotted"): string {
  if (style === "dashed") return "6,4";
  if (style === "dotted") return "2,3";
  return "none";
}

export const CHART_MARGINS = { top: 20, right: 20, bottom: 50, left: 60 };

export function getNiceDomain(data: number[], min?: number, max?: number, padding = 0.1): [number, number] {
  const dataMin = Math.min(...data);
  const dataMax = Math.max(...data);
  const range = dataMax - dataMin;
  const domainMin = min ?? dataMin - range * padding;
  const domainMax = max ?? dataMax + range * padding;
  if (min === undefined && dataMin >= 0 && domainMin < 0) return [0, domainMax];
  return [domainMin, domainMax];
}
