"use client";

import { ChartSpec } from "./types";
import { LineChart } from "./LineChart";
import { BarChart } from "./BarChart";
import { ScatterChart } from "./ScatterChart";
import { PresetChart } from "./PresetChart";

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
    case "scatter":
      return <ScatterChart spec={spec} width={width} height={height} />;
    case "preset":
      return <PresetChart spec={spec} width={width} height={height} />;
    default:
      return <div style={{ padding: "20px", color: "#666" }}>Unknown chart type</div>;
  }
}

function parseChartSpec(json: string): ChartSpec | null {
  try {
    const spec = JSON.parse(json);
    if (spec?.type && ["line", "bar", "area", "scatter", "preset"].includes(spec.type)) return spec as ChartSpec;
    return null;
  } catch {
    return null;
  }
}

export function extractChartSpecs(content: string): { charts: ChartSpec[]; cleanContent: string } {
  const charts: ChartSpec[] = [];

  let cleanContent = content.replace(/```chart\s*([\s\S]*?)```/g, (match, jsonContent) => {
    const spec = parseChartSpec(jsonContent.trim());
    if (spec) { charts.push(spec); return `[CHART_PLACEHOLDER_${charts.length - 1}]`; }
    return match;
  });

  const finalOpenFence = cleanContent.lastIndexOf("```chart");
  if (finalOpenFence !== -1) {
    const closingFence = cleanContent.indexOf("```", finalOpenFence + "```chart".length);
    if (closingFence === -1) {
      cleanContent = `${cleanContent.slice(0, finalOpenFence)}[CHART_LOADING]`;
    }
  }

  return { charts, cleanContent };
}
