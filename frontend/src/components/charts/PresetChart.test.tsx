import { render, screen } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import { PresetChart } from "./PresetChart";
import type { PresetChartKind, PresetChartSpec } from "./types";

vi.mock("recharts", () => {
  const Container = ({ children }: PropsWithChildren) => <div>{children}</div>;
  const BarChart = ({
    children,
    data,
  }: PropsWithChildren<{ data?: unknown }>) => (
    <div data-testid="bar-chart" data-chart={JSON.stringify(data)}>
      {children}
    </div>
  );
  const Empty = () => null;
  const Label = ({ value }: { value?: string }) => (
    <span data-testid="chart-label" data-value={value} />
  );
  const Tooltip = ({ filterNull }: { filterNull?: boolean }) => (
    <span data-testid="chart-tooltip" data-filter-null={String(filterNull)} />
  );
  const ReferenceDot = ({
    label,
  }: {
    label?: { value?: string };
  }) => <span data-testid="missing-value-marker">{label?.value}</span>;
  return {
    Bar: Container,
    BarChart,
    CartesianGrid: Empty,
    Cell: Empty,
    Label,
    Line: Empty,
    LineChart: Container,
    ReferenceDot,
    ReferenceLine: Empty,
    ResponsiveContainer: Container,
    Tooltip,
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

function expectChartLabel(value: string) {
  expect(
    screen
      .getAllByTestId("chart-label")
      .map((label) => label.getAttribute("data-value")),
  ).toContain(value);
}

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

  it("labels wealth-decile impacts with their measured and grouping concepts", () => {
    const spec: PresetChartSpec = {
      type: "preset",
      preset: "decile_absolute_bar",
      measureLabel: "household net income",
      groupLabel: "Wealth decile",
      data: [{ label: "1", value: 10 }],
    };

    render(<PresetChart spec={spec} />);

    expect(
      screen.getByText("Average household net income change by wealth decile"),
    ).toBeInTheDocument();
    expectChartLabel("Wealth decile");
    expectChartLabel("Absolute change in household net income");
  });

  it("keeps missing decile values null so no zero-valued bar is rendered", () => {
    const spec: PresetChartSpec = {
      type: "preset",
      preset: "decile_relative_bar",
      measureLabel: "equivalised HBAI net income",
      groupLabel: "Equivalised HBAI net income decile",
      data: [{ label: "1", value: null }],
    };

    render(<PresetChart spec={spec} />);

    expect(screen.getByTestId("bar-chart")).toHaveAttribute(
      "data-chart",
      JSON.stringify([{ label: "1", value: null }]),
    );
    expect(screen.getByTestId("chart-tooltip")).toHaveAttribute(
      "data-filter-null",
      "false",
    );
    expect(screen.getByTestId("missing-value-marker")).toHaveTextContent("—");
  });

  it("labels wealth winners and losers by wealth decile", () => {
    const spec: PresetChartSpec = {
      type: "preset",
      preset: "winners_losers_stacked_bar",
      groupLabel: "Wealth decile",
      data: [{ decile: 1, no_change: null }],
    };

    render(<PresetChart spec={spec} />);

    expect(
      screen.getByText("Households gaining and losing by wealth decile"),
    ).toBeInTheDocument();
    expectChartLabel("Wealth decile");
    for (const tooltip of screen.getAllByTestId("chart-tooltip")) {
      expect(tooltip).toHaveAttribute("data-filter-null", "false");
    }
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
