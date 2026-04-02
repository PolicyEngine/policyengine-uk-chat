"use client";

import { ChartSpec } from "./types";
import { LineChart } from "./LineChart";
import { BarChart } from "./BarChart";

export { type ChartSpec } from "./types";

interface ChartProps {
  spec: ChartSpec;
  width?: number;
  height?: number;
}

export function Chart({ spec, width, height }: ChartProps) {
  switch (spec.type) {
    case "line":
      return <LineChart spec={spec} width={width} height={height} />;
    case "bar":
      return <BarChart spec={spec} width={width} height={height} />;
    case "area":
      return <LineChart spec={{ ...spec, type: "line", areaFill: true }} width={width} height={height} />;
    default:
      return <div style={{ padding: "20px", color: "#666" }}>Unknown chart type</div>;
  }
}

export function parseChartSpec(json: string): ChartSpec | null {
  try {
    const spec = JSON.parse(json);
    if (spec?.type && ["line", "bar", "area"].includes(spec.type)) return spec as ChartSpec;
    return null;
  } catch {
    return null;
  }
}

export function extractChartSpecs(content: string): { charts: ChartSpec[]; cleanContent: string; hasIncompleteChart: boolean } {
  const charts: ChartSpec[] = [];
  let hasIncompleteChart = false;

  let cleanContent = content.replace(/```chart\s*([\s\S]*?)```/g, (match, jsonContent) => {
    const spec = parseChartSpec(jsonContent.trim());
    if (spec) { charts.push(spec); return `[CHART_PLACEHOLDER_${charts.length - 1}]`; }
    return match;
  });

  if (/```chart\s*[\s\S]*$/.test(cleanContent)) {
    hasIncompleteChart = true;
    cleanContent = cleanContent.replace(/```chart\s*[\s\S]*$/, "[CHART_LOADING]");
  }

  return { charts, cleanContent, hasIncompleteChart };
}
