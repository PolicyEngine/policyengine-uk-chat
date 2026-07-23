import { render, screen } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import { PresetChart } from "./PresetChart";
import type { PresetChartKind, PresetChartSpec } from "./types";

vi.mock("recharts", () => {
  const Container = ({ children }: PropsWithChildren) => <div>{children}</div>;
  const Empty = () => null;
  return {
    Bar: Container,
    BarChart: Container,
    CartesianGrid: Empty,
    Cell: Empty,
    Label: Empty,
    Line: Empty,
    LineChart: Container,
    ReferenceLine: Empty,
    ResponsiveContainer: Container,
    Tooltip: Empty,
    XAxis: Container,
    YAxis: Container,
  };
});

const PRESETS: Array<[PresetChartKind, string, unknown]> = [
  ["budget_waterfall", "Budgetary impact", [{ label: "Total", value: 10, total: true }]],
  ["program_budget_waterfall", "Budgetary impact by programme", [{ label: "Tax", value: -5 }]],
  ["decile_absolute_bar", "Average household income change by decile", [{ label: "1", value: 12 }]],
  ["decile_relative_bar", "Relative household income change by decile", [{ label: "1", value: 1.2 }]],
  ["winners_losers_stacked_bar", "Households gaining and losing by decile", [{ decile: 1, no_change: 1 }]],
  ["poverty_relative_bar", "Relative change in poverty", [{ label: "All", value: -0.5 }]],
  ["inequality_relative_bar", "Relative change in inequality", [{ label: "Gini", value: 0.2 }]],
  ["earnings_variation_line", "Impact by earnings", [{ earnings: 10_000, value: 100 }]],
];

describe("PresetChart", () => {
  it.each(PRESETS)("renders the deterministic title for %s", (preset, title, data) => {
    render(<PresetChart spec={{ type: "preset", preset, data }} />);
    expect(screen.getByText(title)).toBeInTheDocument();
  });

  it("renders caller-provided title, subtitle, and source", () => {
    const spec: PresetChartSpec = {
      type: "preset",
      preset: "decile_absolute_bar",
      title: "Custom title",
      subtitle: "Custom subtitle",
      source: "PolicyEngine",
      data: [{ label: "1", value: 10 }],
    };

    render(<PresetChart spec={spec} />);

    expect(screen.getByText("Custom title")).toBeInTheDocument();
    expect(screen.getByText("Custom subtitle")).toBeInTheDocument();
    expect(screen.getByText("Source: PolicyEngine")).toBeInTheDocument();
  });

  it("rejects unsupported presets", () => {
    render(
      <PresetChart
        spec={{ type: "preset", preset: "unknown", data: [] } as unknown as PresetChartSpec}
      />,
    );
    expect(screen.getByText("Unsupported preset chart")).toBeInTheDocument();
  });
});
