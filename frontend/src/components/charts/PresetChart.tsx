"use client";

import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Label,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_COLORS, CHART_TYPOGRAPHY, PresetChartSpec } from "./types";
import { formatValue } from "./utils";

interface PresetChartProps {
  spec: PresetChartSpec;
  width?: number;
  height?: number;
}

type ChartRow = Record<string, string | number | boolean | null | undefined>;

const WINNERS_LOSERS_SERIES = [
  { key: "gain_more_than_5pct", label: "Gain more than 5%", color: "#1f6b68" },
  { key: "gain_less_than_5pct", label: "Gain less than 5%", color: "#7fb6b3" },
  { key: "no_change", label: "No change", color: "#e6e4df" },
  { key: "lose_less_than_5pct", label: "Loss less than 5%", color: "#b9b5ad" },
  { key: "lose_more_than_5pct", label: "Loss more than 5%", color: "#6b6860" },
] as const;

const FONT_STYLE = {
  fontFamily: CHART_TYPOGRAPHY.sansFamily,
  fontSize: 11,
  fill: CHART_COLORS.label,
};

const TOOLTIP_STYLE = {
  background: "#fff",
  border: `1px solid ${CHART_COLORS.grid}`,
  padding: "10px 12px",
  fontFamily: CHART_TYPOGRAPHY.sansFamily,
  fontSize: 12,
  boxShadow: "0 2px 8px rgba(0, 0, 0, 0.12)",
};

function rows(spec: PresetChartSpec): ChartRow[] {
  return Array.isArray(spec.data)
    ? spec.data.filter((row): row is ChartRow => Boolean(row) && typeof row === "object")
    : [];
}

function numeric(row: ChartRow, field: string): number {
  const value = row[field];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function nullableNumeric(row: ChartRow, field: string): number | null {
  const value = row[field];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function titleFor(spec: PresetChartSpec): string {
  if (spec.title) return spec.title;
  switch (spec.preset) {
    case "budget_waterfall":
      return "Budgetary impact";
    case "program_budget_waterfall":
      return "Budgetary impact by programme";
    case "decile_absolute_bar":
      return spec.measureLabel && spec.groupLabel
        ? `Average ${spec.measureLabel} change by ${spec.groupLabel.toLocaleLowerCase("en-GB")}`
        : "Average household income change by decile";
    case "decile_relative_bar":
      return spec.measureLabel && spec.groupLabel
        ? `Relative ${spec.measureLabel} change by ${spec.groupLabel.toLocaleLowerCase("en-GB")}`
        : "Relative household income change by decile";
    case "winners_losers_stacked_bar":
      return spec.groupLabel
        ? `Households gaining and losing by ${spec.groupLabel.toLocaleLowerCase("en-GB")}`
        : "Households gaining and losing by decile";
    case "poverty_relative_bar":
      return "Relative change in poverty";
    case "inequality_relative_bar":
      return "Relative change in inequality";
    case "earnings_variation_line":
      return "Impact by earnings";
    default:
      return "Chart";
  }
}

function ChartShell({
  spec,
  children,
}: {
  spec: PresetChartSpec;
  children: ReactNode;
}) {
  return (
    <div
      style={{
        width: "100%",
        minWidth: 0,
        borderTop: `2px solid ${CHART_COLORS.axis}`,
        paddingTop: 12,
        fontFamily: CHART_TYPOGRAPHY.sansFamily,
      }}
    >
      <div
        style={{
          color: CHART_TYPOGRAPHY.title.color,
          fontSize: CHART_TYPOGRAPHY.title.fontSize,
          fontWeight: CHART_TYPOGRAPHY.title.fontWeight,
          marginBottom: 4,
        }}
      >
        {titleFor(spec)}
      </div>
      {spec.subtitle && (
        <div
          style={{
            color: CHART_TYPOGRAPHY.subtitle.color,
            fontSize: CHART_TYPOGRAPHY.subtitle.fontSize,
            marginBottom: 10,
          }}
        >
          {spec.subtitle}
        </div>
      )}
      {children}
      {spec.source && (
        <div style={{ color: CHART_COLORS.axis, fontSize: 11, marginTop: 8 }}>
          Source: {spec.source}
        </div>
      )}
    </div>
  );
}

function ValueTooltip({ active, payload, label, format }: any) {
  if (!active || !payload?.length) return null;
  const value = payload[0]?.payload?.originalValue ?? payload[0]?.value;
  return (
    <div style={TOOLTIP_STYLE}>
      <div style={{ fontWeight: 600, marginBottom: 3 }}>{label}</div>
      <div>
        {formatValue(
          typeof value === "number" && Number.isFinite(value) ? value : null,
          format,
        )}
      </div>
    </div>
  );
}

function WaterfallPreset({ spec, height }: { spec: PresetChartSpec; height: number }) {
  let cumulative = 0;
  const data = rows(spec).map((row) => {
    const value = numeric(row, "value");
    const isTotal = row.total === true;
    const start = isTotal ? 0 : cumulative;
    const end = isTotal ? value : cumulative + value;
    if (!isTotal) cumulative = end;
    return {
      label: String(row.label ?? ""),
      base: Math.min(start, end),
      size: Math.abs(end - start),
      originalValue: value,
      isTotal,
    };
  });
  const endpoints = data.flatMap((row) => [row.base, row.base + row.size, 0]);
  const min = Math.min(...endpoints);
  const max = Math.max(...endpoints);
  const padding = Math.max((max - min) * 0.12, 1);

  return (
    <ChartShell spec={spec}>
      <div style={{ height, minHeight: 300, width: "100%" }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 26, right: 18, bottom: 42, left: 24 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={CHART_COLORS.grid} />
            <XAxis dataKey="label" tick={FONT_STYLE} tickLine={false} />
            <YAxis
              domain={[min - padding, max + padding]}
              tick={FONT_STYLE}
              tickLine={false}
              tickFormatter={(value) => formatValue(value, "currency")}
              width={72}
            >
              <Label
                value="Budgetary impact"
                angle={-90}
                position="insideLeft"
                style={{ ...FONT_STYLE, textAnchor: "middle" }}
              />
            </YAxis>
            <ReferenceLine y={0} stroke={CHART_COLORS.axis} />
            <Tooltip content={<ValueTooltip format="currency" />} />
            <Bar dataKey="base" stackId="waterfall" fill="transparent" isAnimationActive={false} />
            <Bar dataKey="size" stackId="waterfall" isAnimationActive={false}>
              {data.map((row) => (
                <Cell
                  key={row.label}
                  fill={
                    row.isTotal
                      ? "#1c1a17"
                      : row.originalValue >= 0
                        ? CHART_COLORS.positive
                        : CHART_COLORS.negativeMuted
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartShell>
  );
}

function ImpactBars({
  spec,
  height,
  format,
}: {
  spec: PresetChartSpec;
  height: number;
  format: "currency" | "percent";
}) {
  const data = rows(spec).map((row) => ({
    label: String(row.label ?? ""),
    value: nullableNumeric(row, "value"),
  }));
  const xAxisLabel =
    spec.preset === "decile_absolute_bar" || spec.preset === "decile_relative_bar"
      ? spec.groupLabel ?? "Income decile"
      : spec.preset === "poverty_relative_bar"
        ? "Population group"
        : "Inequality measure";
  const measureLabel = spec.measureLabel ?? "household income";
  const yAxisLabel =
    spec.preset === "decile_absolute_bar"
      ? `Absolute change in ${measureLabel}`
      : spec.preset === "decile_relative_bar"
        ? `Relative change in ${measureLabel}`
        : spec.preset === "poverty_relative_bar"
          ? "Relative change in poverty rate"
          : "Relative change";
  return (
    <ChartShell spec={spec}>
      <div style={{ height, minHeight: 300, width: "100%" }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 24, right: 18, bottom: 42, left: 24 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={CHART_COLORS.grid} />
            <XAxis dataKey="label" tick={FONT_STYLE} tickLine={false} interval={0}>
              <Label value={xAxisLabel} position="bottom" offset={12} style={FONT_STYLE} />
            </XAxis>
            <YAxis
              tick={FONT_STYLE}
              tickLine={false}
              tickFormatter={(value) => formatValue(value, format)}
              width={72}
            >
              <Label
                value={yAxisLabel}
                angle={-90}
                position="insideLeft"
                style={{ ...FONT_STYLE, textAnchor: "middle" }}
              />
            </YAxis>
            <ReferenceLine y={0} stroke={CHART_COLORS.axis} />
            {data
              .filter((row) => row.value === null)
              .map((row) => (
                <ReferenceDot
                  key={`missing-${row.label}`}
                  x={row.label}
                  y={0}
                  r={0}
                  label={{
                    value: "—",
                    position: "top",
                    fill: CHART_COLORS.label,
                    fontSize: 12,
                  }}
                />
              ))}
            <Tooltip
              content={<ValueTooltip format={format} />}
              filterNull={false}
            />
            <Bar dataKey="value" isAnimationActive={false}>
              {data.map((row) => (
                <Cell
                  key={row.label}
                  fill={
                    row.value === null
                      ? "transparent"
                      : row.value < 0
                        ? CHART_COLORS.negative
                        : CHART_COLORS.positive
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartShell>
  );
}

function WinnersLosersTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const groupLabel = payload[0]?.payload?.groupLabel ?? "Decile";
  return (
    <div style={TOOLTIP_STYLE}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        {groupLabel} {label}
      </div>
      {payload.map((item: any) => {
        const series = WINNERS_LOSERS_SERIES.find((candidate) => candidate.key === item.dataKey);
        return (
          <div key={item.dataKey} style={{ margin: "2px 0" }}>
            {series?.label}:{" "}
            {formatValue(
              typeof item.value === "number" && Number.isFinite(item.value)
                ? item.value
                : null,
              "percent_decimal",
            )}
          </div>
        );
      })}
    </div>
  );
}

function WinnersLosersPreset({ spec, height }: { spec: PresetChartSpec; height: number }) {
  const sourceRows = rows(spec);
  const overall = sourceRows.find((row) => numeric(row, "decile") === 0);
  const groupLabel = spec.groupLabel ?? "Income decile";
  const allData = overall ? [{ ...overall, label: "All", groupLabel }] : [];
  const decileData = sourceRows
    .filter((row) => numeric(row, "decile") !== 0)
    .map((row) => ({
      ...row,
      label: String(row.decile ?? ""),
      groupLabel,
    }));
  const barHeight = 18;
  const overallHeight = allData.length ? 42 : 0;
  const gapHeight = allData.length ? 8 : 0;
  const decileHeight = Math.max(decileData.length * (barHeight + 1) + 50, 240);

  const renderBars = () => WINNERS_LOSERS_SERIES.map((series) => (
    <Bar
      key={series.key}
      dataKey={series.key}
      stackId="impact"
      fill={series.color}
      isAnimationActive={false}
    />
  ));

  return (
    <ChartShell spec={spec}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, minHeight: Math.max(height, 380) }}>
        <div style={{ display: "flex", flex: "1 1 360px", flexDirection: "column", minWidth: 0 }}>
          {allData.length > 0 && (
            <div style={{ height: overallHeight }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={allData}
                  layout="vertical"
                  stackOffset="expand"
                  barSize={barHeight}
                  margin={{ top: 8, right: 10, bottom: 0, left: 40 }}
                >
                  <XAxis type="number" hide />
                  <YAxis
                    type="category"
                    dataKey="label"
                    tick={FONT_STYLE}
                    tickLine={false}
                    axisLine={false}
                    width={40}
                  />
                  <Tooltip
                    content={<WinnersLosersTooltip />}
                    filterNull={false}
                  />
                  {renderBars()}
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
          <div style={{ height: gapHeight }} />
          <div style={{ height: decileHeight }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={decileData}
                layout="vertical"
                stackOffset="expand"
                barSize={barHeight}
                barCategoryGap={1}
                margin={{ top: 0, right: 10, bottom: 40, left: 40 }}
              >
                <XAxis
                  type="number"
                  tick={FONT_STYLE}
                  tickLine={false}
                  tickFormatter={(value) => formatValue(value, "percent_decimal")}
                >
                  <Label value="Population share" position="bottom" offset={14} style={FONT_STYLE} />
                </XAxis>
                <YAxis
                  type="category"
                  dataKey="label"
                  tick={FONT_STYLE}
                  tickLine={false}
                  width={40}
                  interval={0}
                >
                  <Label
                    value={groupLabel}
                    angle={-90}
                    position="insideLeft"
                    style={{ ...FONT_STYLE, textAnchor: "middle" }}
                  />
                </YAxis>
                <Tooltip
                  content={<WinnersLosersTooltip />}
                  filterNull={false}
                />
                {renderBars()}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div style={{ display: "flex", flex: "0 1 180px", flexDirection: "column", gap: 8, justifyContent: "center" }}>
          {WINNERS_LOSERS_SERIES.map((series) => (
            <div key={series.key} style={{ alignItems: "center", display: "flex", gap: 8 }}>
              <span
                style={{
                  background: series.color,
                  borderRadius: 2,
                  display: "inline-block",
                  flexShrink: 0,
                  height: 12,
                  width: 12,
                }}
              />
              <span style={{ color: CHART_COLORS.label, fontSize: 11, whiteSpace: "nowrap" }}>
                {series.label}
              </span>
            </div>
          ))}
        </div>
      </div>
    </ChartShell>
  );
}

function EarningsPreset({ spec, height }: { spec: PresetChartSpec; height: number }) {
  const data = rows(spec);
  return (
    <ChartShell spec={spec}>
      <div style={{ height, minHeight: 300, width: "100%" }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 24, right: 18, bottom: 42, left: 24 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={CHART_COLORS.grid} />
            <XAxis
              dataKey="earnings"
              tick={FONT_STYLE}
              tickLine={false}
              tickFormatter={(value) => formatValue(value, "currency")}
            />
            <YAxis
              tick={FONT_STYLE}
              tickLine={false}
              tickFormatter={(value) => formatValue(value, "currency")}
              width={72}
            />
            <ReferenceLine y={0} stroke={CHART_COLORS.axis} />
            <Tooltip content={<ValueTooltip format="currency" />} />
            <Line
              type="monotone"
              dataKey="value"
              stroke={CHART_COLORS.positive}
              strokeWidth={2.5}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </ChartShell>
  );
}

export function PresetChart({ spec, height = 400 }: PresetChartProps) {
  if (spec.preset === "budget_waterfall" || spec.preset === "program_budget_waterfall") {
    return <WaterfallPreset spec={spec} height={height} />;
  }
  if (spec.preset === "decile_absolute_bar") {
    return <ImpactBars spec={spec} height={height} format="currency" />;
  }
  if (
    spec.preset === "decile_relative_bar" ||
    spec.preset === "poverty_relative_bar" ||
    spec.preset === "inequality_relative_bar"
  ) {
    return <ImpactBars spec={spec} height={height} format="percent" />;
  }
  if (spec.preset === "winners_losers_stacked_bar") {
    return <WinnersLosersPreset spec={spec} height={height} />;
  }
  if (spec.preset === "earnings_variation_line") {
    return <EarningsPreset spec={spec} height={height} />;
  }
  return <div style={{ color: CHART_COLORS.label, padding: 20 }}>Unsupported preset chart</div>;
}
