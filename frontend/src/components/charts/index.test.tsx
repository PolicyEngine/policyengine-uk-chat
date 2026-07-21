import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Chart, extractChartSpecs } from "./index";
import type { ChartSpec } from "./types";

vi.mock("./LineChart", () => ({
  LineChart: ({ spec }: { spec: { areaFill?: boolean } }) => (
    <div data-area-fill={String(Boolean(spec.areaFill))}>line chart</div>
  ),
}));
vi.mock("./BarChart", () => ({
  BarChart: () => <div>bar chart</div>,
}));
vi.mock("./ScatterChart", () => ({
  ScatterChart: () => <div>scatter chart</div>,
}));
vi.mock("./PresetChart", () => ({
  PresetChart: () => <div>preset chart</div>,
}));

describe("extractChartSpecs", () => {
  it("extracts valid chart blocks and leaves invalid blocks untouched", () => {
    const valid = '```chart\n{"type":"bar","x":{"field":"x"},"y":{"field":"y"},"series":[],"data":[]}\n```';
    const invalid = '```chart\n{"type":"table"}\n```';

    const result = extractChartSpecs(`Before\n${valid}\n${invalid}\nAfter`);

    expect(result.charts).toHaveLength(1);
    expect(result.charts[0].type).toBe("bar");
    expect(result.cleanContent).toContain("[CHART_PLACEHOLDER_0]");
    expect(result.cleanContent).toContain(invalid);
  });

  it("marks an incomplete streaming chart as loading", () => {
    const result = extractChartSpecs('Text\n```chart\n{"type":"line"');

    expect(result.charts).toEqual([]);
    expect(result.cleanContent).toBe("Text\n[CHART_LOADING]");
  });

  it("keeps malformed completed JSON visible", () => {
    const content = "```chart\n{not-json}\n```";
    expect(extractChartSpecs(content)).toEqual({ charts: [], cleanContent: content });
  });
});

describe("Chart", () => {
  const base = {
    x: { field: "x" },
    y: { field: "y" },
    series: [],
    data: [],
  };

  it.each([
    ["line", "line chart"],
    ["bar", "bar chart"],
    ["scatter", "scatter chart"],
  ] as const)("dispatches %s specifications", (type, label) => {
    render(<Chart spec={{ ...base, type } as ChartSpec} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("converts area specifications to area-filled line charts", () => {
    render(<Chart spec={{ ...base, type: "area" }} />);
    expect(screen.getByText("line chart")).toHaveAttribute("data-area-fill", "true");
  });

  it("dispatches preset charts", () => {
    render(
      <Chart
        spec={{ type: "preset", preset: "budget_waterfall", data: [] }}
      />,
    );
    expect(screen.getByText("preset chart")).toBeInTheDocument();
  });

  it("renders a fallback for unknown chart types", () => {
    render(<Chart spec={{ type: "unknown" } as unknown as ChartSpec} />);
    expect(screen.getByText("Unknown chart type")).toBeInTheDocument();
  });
});
