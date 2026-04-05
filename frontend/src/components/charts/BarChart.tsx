"use client";

import { useRef, useEffect, useState, useCallback, useId } from "react";
import * as d3 from "d3";
import { BarChartSpec, TooltipData, CHART_COLORS, CHART_TYPOGRAPHY } from "./types";
import { formatValue, getSeriesColor, CHART_MARGINS, getNiceDomain } from "./utils";
import { Tooltip } from "./Tooltip";

interface BarChartProps {
  spec: BarChartSpec;
  width?: number;
  height?: number;
}

export function BarChart({ spec, width = 540, height = 340 }: BarChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);
  const clipId = useId().replace(/:/g, "");

  const categories = spec.data.map((d) => String(d[spec.x.field]));
  const maxLabelLen = Math.max(...categories.map((c) => c.length));
  const allNumeric = categories.every((c) => /^-?\d+(\.\d+)?$/.test(c.trim()));
  const isHorizontal = spec.orientation === "horizontal" || (spec.orientation !== "vertical" && !allNumeric && (maxLabelLen > 12 || categories.length > 6));
  const isStacked = spec.arrangement === "stacked";

  const leftMargin = isHorizontal ? Math.min(Math.max(maxLabelLen * 7, 80), 200) : CHART_MARGINS.left;
  const margins = { ...CHART_MARGINS, left: leftMargin, bottom: isHorizontal ? 50 : 60 };
  const innerWidth = width - margins.left - margins.right;
  const innerHeight = height - margins.top - margins.bottom;

  let valMin: number, valMax: number;
  if (isStacked) {
    valMax = d3.max(spec.data, (d) => spec.series.reduce((s, ser) => s + (Number(d[ser.field]) || 0), 0)) || 0;
    valMin = d3.min(spec.data, (d) => spec.series.reduce((s, ser) => s + (Number(d[ser.field]) || 0), 0)) || 0;
  } else {
    const all = spec.series.flatMap((s) => spec.data.map((d) => Number(d[s.field])));
    valMax = d3.max(all) || 0; valMin = d3.min(all) || 0;
  }
  const valDomain = getNiceDomain([valMin, valMax], spec.y.min, spec.y.max, 0.1);

  let stackedData: d3.Series<Record<string, unknown>, string>[] = [];
  if (isStacked) {
    stackedData = d3.stack<Record<string, unknown>>().keys(spec.series.map((s) => s.field)).value((d, k) => Number(d[k]) || 0)(spec.data);
  }

  const handleMouseMove = useCallback((event: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const mouseX = event.clientX - rect.left - margins.left;
    const mouseY = event.clientY - rect.top - margins.top;
    if (mouseX < 0 || mouseX > innerWidth || mouseY < 0 || mouseY > innerHeight) { setTooltip(null); return; }
    let category: string | undefined;
    if (isHorizontal) {
      const s = d3.scaleBand().domain(categories).range([0, innerHeight]).padding(0.3);
      category = categories[Math.floor(mouseY / s.step())];
    } else {
      const s = d3.scaleBand().domain(categories).range([0, innerWidth]).padding(0.3);
      category = categories[Math.floor(mouseX / s.step())];
    }
    if (!category) { setTooltip(null); return; }
    const dp = spec.data.find((d) => String(d[spec.x.field]) === category);
    if (!dp) { setTooltip(null); return; }
    setTooltip({ x: event.clientX - rect.left, y: event.clientY - rect.top, title: category, values: spec.series.map((s, i) => ({ label: s.label || s.field, value: formatValue(Number(dp[s.field]), spec.y.format), color: getSeriesColor(i, s.color) })) });
  }, [spec, categories, innerWidth, innerHeight, margins, isHorizontal]);

  const handleMouseLeave = useCallback(() => setTooltip(null), []);

  useEffect(() => {
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.append("defs").append("clipPath").attr("id", clipId).append("rect").attr("width", innerWidth).attr("height", innerHeight);
    const g = svg.append("g").attr("transform", `translate(${margins.left},${margins.top})`);

    if (isHorizontal) {
      const catScale = d3.scaleBand().domain(categories).range([0, innerHeight]).padding(0.3);
      const valScale = d3.scaleLinear().domain(valDomain).range([0, innerWidth]);
      const groupScale = d3.scaleBand().domain(spec.series.map((s) => s.field)).range([0, catScale.bandwidth()]).padding(0.1);

      if (spec.showGrid !== false) {
        g.append("g").selectAll("line").data(valScale.ticks(5)).join("line").attr("x1", (d) => valScale(d)).attr("x2", (d) => valScale(d)).attr("y1", 0).attr("y2", innerHeight).attr("stroke", CHART_COLORS.grid).attr("stroke-dasharray", "2,3").attr("stroke-width", 1);
      }
      if (valDomain[0] < 0) g.append("line").attr("x1", valScale(0)).attr("x2", valScale(0)).attr("y1", 0).attr("y2", innerHeight).attr("stroke", CHART_COLORS.axis).attr("stroke-width", 1);

      g.append("g").call(d3.axisLeft(catScale).tickSize(0).tickPadding(8))
        .call((ax) => ax.select(".domain").attr("stroke", CHART_COLORS.axis))
        .call((ax) => ax.selectAll(".tick text").attr("fill", CHART_TYPOGRAPHY.tickLabel.color).attr("font-size", CHART_TYPOGRAPHY.tickLabel.fontSize).attr("font-family", CHART_TYPOGRAPHY.fontFamily));
      g.append("g").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(valScale).ticks(5).tickFormat((d) => formatValue(d as number, spec.y.format)).tickSize(0).tickPadding(8))
        .call((ax) => ax.select(".domain").attr("stroke", CHART_COLORS.axis))
        .call((ax) => ax.selectAll(".tick text").attr("fill", CHART_TYPOGRAPHY.tickLabel.color).attr("font-size", CHART_TYPOGRAPHY.tickLabel.fontSize).attr("font-family", CHART_TYPOGRAPHY.fontFamily));

      const bg = g.append("g").attr("clip-path", `url(#${clipId})`);
      if (isStacked) {
        stackedData.forEach((layer, i) => {
          const color = getSeriesColor(i, spec.series[i]?.color);
          bg.selectAll(`.bar-${i}`).data(layer).join("rect").attr("class", `bar-${i}`).attr("y", (d) => catScale(String(d.data[spec.x.field])) || 0).attr("x", (d) => valScale(Math.min(d[0], d[1]))).attr("height", catScale.bandwidth()).attr("width", (d) => Math.abs(valScale(d[1]) - valScale(d[0]))).attr("fill", color).attr("opacity", 0.7);
        });
      } else {
        spec.series.forEach((s, i) => {
          const color = getSeriesColor(i, s.color);
          bg.selectAll(`.bar-${i}`).data(spec.data).join("rect").attr("class", `bar-${i}`).attr("y", (d) => (catScale(String(d[spec.x.field])) || 0) + (groupScale(s.field) || 0)).attr("x", (d) => { const v = Number(d[s.field]); return v >= 0 ? valScale(0) : valScale(v); }).attr("height", groupScale.bandwidth()).attr("width", (d) => Math.abs(valScale(Number(d[s.field])) - valScale(0))).attr("fill", color).attr("opacity", 0.7);
        });
      }
    } else {
      const catScale = d3.scaleBand().domain(categories).range([0, innerWidth]).padding(0.3);
      const valScale = d3.scaleLinear().domain(valDomain).range([innerHeight, 0]);
      const groupScale = d3.scaleBand().domain(spec.series.map((s) => s.field)).range([0, catScale.bandwidth()]).padding(0.1);

      if (spec.showGrid !== false) {
        g.append("g").selectAll("line").data(valScale.ticks(5)).join("line").attr("x1", 0).attr("x2", innerWidth).attr("y1", (d) => valScale(d)).attr("y2", (d) => valScale(d)).attr("stroke", CHART_COLORS.grid).attr("stroke-dasharray", "2,3").attr("stroke-width", 1);
      }
      if (valDomain[0] < 0) g.append("line").attr("x1", 0).attr("x2", innerWidth).attr("y1", valScale(0)).attr("y2", valScale(0)).attr("stroke", CHART_COLORS.axis).attr("stroke-width", 1);

      g.append("g").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(catScale).tickSize(0).tickPadding(10))
        .call((ax) => ax.select(".domain").attr("stroke", CHART_COLORS.axis))
        .call((ax) => ax.selectAll(".tick text").attr("fill", CHART_TYPOGRAPHY.tickLabel.color).attr("font-size", CHART_TYPOGRAPHY.tickLabel.fontSize).attr("font-family", CHART_TYPOGRAPHY.fontFamily));
      g.append("g").call(d3.axisLeft(valScale).ticks(5).tickFormat((d) => formatValue(d as number, spec.y.format)).tickSize(0).tickPadding(10))
        .call((ax) => ax.select(".domain").remove())
        .call((ax) => ax.selectAll(".tick text").attr("fill", CHART_TYPOGRAPHY.tickLabel.color).attr("font-size", CHART_TYPOGRAPHY.tickLabel.fontSize).attr("font-family", CHART_TYPOGRAPHY.fontFamily));

      const bg = g.append("g").attr("clip-path", `url(#${clipId})`);
      if (isStacked) {
        stackedData.forEach((layer, i) => {
          const color = getSeriesColor(i, spec.series[i]?.color);
          bg.selectAll(`.bar-v-${i}`).data(layer).join("rect").attr("class", `bar-v-${i}`).attr("x", (d) => catScale(String(d.data[spec.x.field])) || 0).attr("y", (d) => valScale(d[1])).attr("width", catScale.bandwidth()).attr("height", (d) => valScale(d[0]) - valScale(d[1])).attr("fill", color).attr("opacity", 0.7);
        });
      } else {
        spec.series.forEach((s, i) => {
          const color = getSeriesColor(i, s.color);
          bg.selectAll(`.bar-v-${i}`).data(spec.data).join("rect").attr("class", `bar-v-${i}`).attr("x", (d) => (catScale(String(d[spec.x.field])) || 0) + (groupScale(s.field) || 0)).attr("y", (d) => { const v = Number(d[s.field]); return v >= 0 ? valScale(v) : valScale(0); }).attr("width", groupScale.bandwidth()).attr("height", (d) => Math.abs(valScale(0) - valScale(Number(d[s.field])))).attr("fill", color).attr("opacity", 0.7);
        });
      }
    }
  }, [spec, innerWidth, innerHeight, margins, isHorizontal, isStacked, stackedData, valDomain, categories, clipId]);

  return (
    <div ref={containerRef} style={{ position: "relative", fontFamily: CHART_TYPOGRAPHY.sansFamily, width, borderTop: `2px solid ${CHART_COLORS.axis}`, paddingTop: "12px" }}>
      {spec.title && <div style={{ fontSize: CHART_TYPOGRAPHY.title.fontSize, fontWeight: CHART_TYPOGRAPHY.title.fontWeight, color: CHART_TYPOGRAPHY.title.color, marginBottom: "12px" }}>{spec.title}</div>}
      <svg ref={svgRef} width={width} height={height} onMouseMove={handleMouseMove} onMouseLeave={handleMouseLeave} style={{ overflow: "visible", display: "block" }} />
      {spec.showLegend !== false && spec.series.length > 1 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "16px", marginTop: "8px", paddingTop: "8px", paddingLeft: margins.left, borderTop: "1px solid #e5e7eb" }}>
          {spec.series.map((s, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <div style={{ width: "10px", height: "10px", background: getSeriesColor(i, s.color) }} />
              <span style={{ fontSize: CHART_TYPOGRAPHY.legend.fontSize, color: CHART_TYPOGRAPHY.legend.color }}>{s.label || s.field}</span>
            </div>
          ))}
        </div>
      )}
      {spec.source && <div style={{ fontSize: 11, color: "#9e9a90", marginTop: "8px" }}>Source: {spec.source}</div>}
      <Tooltip data={tooltip} containerRef={containerRef} />
    </div>
  );
}
