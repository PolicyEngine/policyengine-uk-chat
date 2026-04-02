export interface AxisConfig {
  field: string;
  label?: string;
  format?: "currency" | "percent" | "percent_decimal" | "number" | "compact" | "year";
  min?: number;
  max?: number;
  tickCount?: number;
}

export interface SeriesConfig {
  field: string;
  label?: string;
  color?: string;
  lineStyle?: "solid" | "dashed" | "dotted";
  lineWidth?: number;
  curve?: "smooth" | "step" | "linear";
  radius?: number;
  stack?: string;
}

export interface AnnotationConfig {
  type: "line" | "label" | "area";
  x?: number;
  y?: number;
  text?: string;
  x1?: number;
  x2?: number;
  y1?: number;
  y2?: number;
  color?: string;
  style?: "solid" | "dashed";
}

export interface LineChartSpec {
  type: "line";
  title?: string;
  subtitle?: string;
  source?: string;
  x: AxisConfig;
  y: AxisConfig;
  series: SeriesConfig[];
  data: Record<string, number | string>[];
  annotations?: AnnotationConfig[];
  showLegend?: boolean;
  showGrid?: boolean;
  areaFill?: boolean;
}

export interface BarChartSpec {
  type: "bar";
  title?: string;
  subtitle?: string;
  source?: string;
  x: AxisConfig;
  y: AxisConfig;
  series: SeriesConfig[];
  data: Record<string, number | string>[];
  annotations?: AnnotationConfig[];
  showLegend?: boolean;
  showGrid?: boolean;
  arrangement?: "grouped" | "stacked";
  orientation?: "vertical" | "horizontal";
}

export interface AreaChartSpec {
  type: "area";
  title?: string;
  subtitle?: string;
  source?: string;
  x: AxisConfig;
  y: AxisConfig;
  series: SeriesConfig[];
  data: Record<string, number | string>[];
  annotations?: AnnotationConfig[];
  showLegend?: boolean;
  showGrid?: boolean;
  stacked?: boolean;
}

export type ChartSpec = LineChartSpec | BarChartSpec | AreaChartSpec;

export interface TooltipData {
  x: number;
  y: number;
  title?: string;
  values: { label: string; value: string; color?: string }[];
}

export const CHART_COLORS = {
  series: ["#228be6", "#16a34a", "#d97706", "#9333ea", "#ef4444", "#0891b2", "#f97316", "#6366f1"],
  positive: "#16a34a",
  negative: "#dc2626",
  neutral: "#9e9a90",
  grid: "#e5e7eb",
  axis: "#9e9a90",
  label: "#6b6860",
};

export const CHART_TYPOGRAPHY = {
  fontFamily: "ui-monospace, 'JetBrains Mono', monospace",
  sansFamily: "system-ui, sans-serif",
  title: { fontSize: 16, fontWeight: 600, color: "#1c1a17" },
  subtitle: { fontSize: 13, fontWeight: 400, color: "#6b6860" },
  tickLabel: { fontSize: 11, fontWeight: 400, color: "#9e9a90" },
  legend: { fontSize: 11, fontWeight: 400, color: "#9e9a90" },
};
