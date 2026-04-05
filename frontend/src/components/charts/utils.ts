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

/**
 * Snap a value to a nice number — the nearest value in {1, 2, 2.5, 5} × 10^n.
 * `ceil` rounds up (for domain max), `floor` rounds down (for domain min).
 */
function niceNumber(value: number, mode: "ceil" | "floor"): number {
  if (value === 0) return 0;
  const sign = value < 0 ? -1 : 1;
  const abs = Math.abs(value);
  const exponent = Math.floor(Math.log10(abs));
  const magnitude = Math.pow(10, exponent);
  const normalized = abs / magnitude; // in [1, 10)

  const NICE = [1, 2, 2.5, 5, 10];

  let nice: number;
  if ((mode === "ceil" && sign > 0) || (mode === "floor" && sign < 0)) {
    // Round up in absolute value
    nice = NICE.find((n) => n >= normalized - 1e-10) ?? 10;
  } else {
    // Round down in absolute value
    nice = [...NICE].reverse().find((n) => n <= normalized + 1e-10) ?? 1;
  }

  return sign * nice * magnitude;
}

export function getNiceDomain(data: number[], min?: number, max?: number, padding = 0.1): [number, number] {
  const dataMin = Math.min(...data);
  const dataMax = Math.max(...data);
  const range = dataMax - dataMin;

  // If explicit bounds are provided, use them directly
  let domainMin = min ?? dataMin - range * padding;
  let domainMax = max ?? dataMax + range * padding;

  // Clamp to zero if data is non-negative but padding pushed it negative
  if (min === undefined && dataMin >= 0 && domainMin < 0) domainMin = 0;

  // Snap to nice numbers when bounds aren't explicitly set
  if (min === undefined && domainMin !== 0) domainMin = niceNumber(domainMin, "floor");
  if (max === undefined) domainMax = niceNumber(domainMax, "ceil");

  return [domainMin, domainMax];
}
