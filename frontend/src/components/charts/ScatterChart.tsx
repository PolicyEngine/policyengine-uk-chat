"use client";

import { useRef, useEffect, useState, useCallback, useId } from "react";
import * as d3 from "d3";
import { ScatterChartSpec, TooltipData, CHART_COLORS, CHART_TYPOGRAPHY } from "./types";
import { formatValue, getSeriesColor, CHART_MARGINS, getNiceDomain } from "./utils";
import { Tooltip } from "./Tooltip";

interface ScatterChartProps {
  spec: ScatterChartSpec;
  width?: number;
  height?: number;
}

export function ScatterChart({ spec, width = 540, height = 340 }: ScatterChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);
  const clipId = useId().replace(/:/g, "");

  const margins = { ...CHART_MARGINS };
  const innerWidth = width - margins.left - margins.right;
  const innerHeight = height - margins.top - margins.bottom;

  const allX = spec.series.flatMap((s) => spec.data.map((d) => Number(d[s.xField])));
  const allY = spec.series.flatMap((s) => spec.data.map((d) => Number(d[s.yField])));
  const xDomain = getNiceDomain([d3.min(allX) || 0, d3.max(allX) || 0], spec.x.min, spec.x.max, 0.1);
  const yDomain = getNiceDomain([d3.min(allY) || 0, d3.max(allY) || 0], spec.y.min, spec.y.max, 0.1);

  const handleMouseMove = useCallback((event: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const mouseX = event.clientX - rect.left - margins.left;
    const mouseY = event.clientY - rect.top - margins.top;
    if (mouseX < 0 || mouseX > innerWidth || mouseY < 0 || mouseY > innerHeight) { setTooltip(null); return; }

    const xScale = d3.scaleLinear().domain(xDomain).range([0, innerWidth]);
    const yScale = d3.scaleLinear().domain(yDomain).range([innerHeight, 0]);

    let closest: { dist: number; seriesIdx: number; dp: Record<string, number | string> } | null = null;
    spec.series.forEach((s, si) => {
      spec.data.forEach((d) => {
        const dx = xScale(Number(d[s.xField])) - mouseX;
        const dy = yScale(Number(d[s.yField])) - mouseY;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 30 && (!closest || dist < closest.dist)) closest = { dist, seriesIdx: si, dp: d };
      });
    });

    if (!closest) { setTooltip(null); return; }
    const { seriesIdx, dp } = closest;
    const s = spec.series[seriesIdx];
    const values = [
      { label: spec.x.label || s.xField, value: formatValue(Number(dp[s.xField]), spec.x.format), color: getSeriesColor(seriesIdx, s.color) },
      { label: spec.y.label || s.yField, value: formatValue(Number(dp[s.yField]), spec.y.format), color: getSeriesColor(seriesIdx, s.color) },
    ];
    if (s.sizeField) values.push({ label: s.sizeField, value: formatValue(Number(dp[s.sizeField]), "number"), color: getSeriesColor(seriesIdx, s.color) });
    setTooltip({ x: event.clientX - rect.left, y: event.clientY - rect.top, title: s.label || `Series ${seriesIdx + 1}`, values });
  }, [spec, innerWidth, innerHeight, margins, xDomain, yDomain]);

  const handleMouseLeave = useCallback(() => setTooltip(null), []);

  useEffect(() => {
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.append("defs").append("clipPath").attr("id", clipId).append("rect").attr("width", innerWidth).attr("height", innerHeight);
    const g = svg.append("g").attr("transform", `translate(${margins.left},${margins.top})`);

    const xScale = d3.scaleLinear().domain(xDomain).range([0, innerWidth]);
    const yScale = d3.scaleLinear().domain(yDomain).range([innerHeight, 0]);

    if (spec.showGrid !== false) {
      g.append("g").selectAll("line").data(yScale.ticks(5)).join("line").attr("x1", 0).attr("x2", innerWidth).attr("y1", (d) => yScale(d)).attr("y2", (d) => yScale(d)).attr("stroke", CHART_COLORS.grid).attr("stroke-dasharray", "2,3").attr("stroke-width", 1);
      g.append("g").selectAll("line").data(xScale.ticks(5)).join("line").attr("x1", (d) => xScale(d)).attr("x2", (d) => xScale(d)).attr("y1", 0).attr("y2", innerHeight).attr("stroke", CHART_COLORS.grid).attr("stroke-dasharray", "2,3").attr("stroke-width", 1);
    }

    g.append("g").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(xScale).ticks(5).tickFormat((d) => formatValue(d as number, spec.x.format)).tickSize(0).tickPadding(10))
      .call((ax) => ax.select(".domain").attr("stroke", CHART_COLORS.axis))
      .call((ax) => ax.selectAll(".tick text").attr("fill", CHART_TYPOGRAPHY.tickLabel.color).attr("font-size", CHART_TYPOGRAPHY.tickLabel.fontSize).attr("font-family", CHART_TYPOGRAPHY.fontFamily));
    g.append("g").call(d3.axisLeft(yScale).ticks(5).tickFormat((d) => formatValue(d as number, spec.y.format)).tickSize(0).tickPadding(10))
      .call((ax) => ax.select(".domain").remove())
      .call((ax) => ax.selectAll(".tick text").attr("fill", CHART_TYPOGRAPHY.tickLabel.color).attr("font-size", CHART_TYPOGRAPHY.tickLabel.fontSize).attr("font-family", CHART_TYPOGRAPHY.fontFamily));

    const bg = g.append("g").attr("clip-path", `url(#${clipId})`);

    spec.series.forEach((s, i) => {
      const minR = s.minRadius ?? 3;
      const maxR = s.maxRadius ?? 20;
      let sizeScale: d3.ScaleLinear<number, number> | null = null;
      if (s.sizeField) {
        const sizeValues = spec.data.map((d) => Number(d[s.sizeField!])).filter((v) => !isNaN(v));
        sizeScale = d3.scaleLinear().domain([d3.min(sizeValues) || 0, d3.max(sizeValues) || 1]).range([minR, maxR]);
      }

      bg.selectAll(`.dot-${i}`)
        .data(spec.data)
        .join("circle")
        .attr("class", `dot-${i}`)
        .attr("cx", (d) => xScale(Number(d[s.xField])))
        .attr("cy", (d) => yScale(Number(d[s.yField])))
        .attr("r", (d) => sizeScale ? sizeScale(Number(d[s.sizeField!])) : minR)
        .attr("fill", getSeriesColor(i, s.color))
        .attr("opacity", 0.7)
        .attr("stroke", getSeriesColor(i, s.color))
        .attr("stroke-width", 1);
    });
  }, [spec, innerWidth, innerHeight, margins, xDomain, yDomain, clipId]);

  return (
    <div ref={containerRef} style={{ position: "relative", fontFamily: CHART_TYPOGRAPHY.sansFamily, width, borderTop: `2px solid ${CHART_COLORS.axis}`, paddingTop: "12px" }}>
      {spec.title && <div style={{ fontSize: CHART_TYPOGRAPHY.title.fontSize, fontWeight: CHART_TYPOGRAPHY.title.fontWeight, color: CHART_TYPOGRAPHY.title.color, marginBottom: "12px" }}>{spec.title}</div>}
      <svg ref={svgRef} width={width} height={height} onMouseMove={handleMouseMove} onMouseLeave={handleMouseLeave} style={{ overflow: "visible", display: "block" }} />
      {spec.showLegend !== false && spec.series.length > 1 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "16px", marginTop: "8px", paddingTop: "8px", paddingLeft: margins.left, borderTop: "1px solid #e5e7eb" }}>
          {spec.series.map((s, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <div style={{ width: "10px", height: "10px", borderRadius: "50%", background: getSeriesColor(i, s.color) }} />
              <span style={{ fontSize: CHART_TYPOGRAPHY.legend.fontSize, color: CHART_TYPOGRAPHY.legend.color }}>{s.label || `${s.xField} vs ${s.yField}`}</span>
            </div>
          ))}
        </div>
      )}
      {spec.source && <div style={{ fontSize: 11, color: "#9e9a90", marginTop: "8px" }}>Source: {spec.source}</div>}
      <Tooltip data={tooltip} containerRef={containerRef} />
    </div>
  );
}
