"use client";

import { useRef, useEffect, useState, useCallback } from "react";
import { useInView } from "./useInView";
import * as d3 from "d3";
import { LineChartSpec, TooltipData, CHART_COLORS, CHART_TYPOGRAPHY } from "./types";
import { formatValue, getSeriesColor, getDashArray, CHART_MARGINS, getNiceDomain } from "./utils";
import { Tooltip } from "./Tooltip";

interface LineChartProps {
  spec: LineChartSpec;
  width?: number;
  height?: number;
}

export function LineChart({ spec, width = 540, height = 340 }: LineChartProps) {
  const [containerRef, inView] = useInView(0.15);
  const svgRef = useRef<SVGSVGElement>(null);
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  const margins = CHART_MARGINS;
  const innerWidth = width - margins.left - margins.right;
  const innerHeight = height - margins.top - margins.bottom;

  const firstXValue = spec.data[0]?.[spec.x.field];
  const isXCategorical = typeof firstXValue === "string" && isNaN(Number(firstXValue));

  const yValues = spec.series.flatMap((s) => spec.data.map((d) => Number(d[s.field])).filter((v) => !isNaN(v)));
  const yDomain = getNiceDomain(yValues, spec.y.min, spec.y.max, 0.05);
  const yScale = d3.scaleLinear().domain(yDomain).range([innerHeight, 0]);

  const xScalePoint = isXCategorical
    ? d3.scalePoint<string>().domain(spec.data.map((d) => String(d[spec.x.field]))).range([0, innerWidth]).padding(0.5)
    : null;

  const xScaleLinear = !isXCategorical
    ? (() => { const xv = spec.data.map((d) => Number(d[spec.x.field])); return d3.scaleLinear().domain(getNiceDomain(xv, spec.x.min, spec.x.max, 0)).range([0, innerWidth]); })()
    : null;

  const getX = useCallback((d: Record<string, unknown>): number => {
    if (isXCategorical && xScalePoint) return xScalePoint(String(d[spec.x.field])) ?? 0;
    if (xScaleLinear) return xScaleLinear(Number(d[spec.x.field]));
    return 0;
  }, [isXCategorical, xScalePoint, xScaleLinear, spec.x.field]);

  const handleMouseMove = useCallback((event: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const mouseX = event.clientX - rect.left - margins.left;
    const mouseY = event.clientY - rect.top - margins.top;
    if (mouseX < 0 || mouseX > innerWidth || mouseY < 0 || mouseY > innerHeight) { setTooltip(null); return; }
    let closest: Record<string, unknown> | null = null;
    let closestDist = Infinity;
    for (const d of spec.data) { const dist = Math.abs(getX(d) - mouseX); if (dist < closestDist) { closestDist = dist; closest = d; } }
    if (!closest) { setTooltip(null); return; }
    setTooltip({
      x: getX(closest) + margins.left,
      y: mouseY + margins.top,
      title: isXCategorical ? String(closest[spec.x.field]) : formatValue(Number(closest[spec.x.field]), spec.x.format),
      values: spec.series.map((s, i) => ({ label: s.label || s.field, value: formatValue(Number(closest![s.field]), spec.y.format), color: getSeriesColor(i, s.color) })),
    });
  }, [spec, getX, innerWidth, innerHeight, margins, isXCategorical]);

  const handleMouseLeave = useCallback(() => setTooltip(null), []);

  useEffect(() => {
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    const g = svg.append("g").attr("transform", `translate(${margins.left},${margins.top})`);

    if (spec.showGrid !== false) {
      g.append("g").selectAll("line").data(yScale.ticks(spec.y.tickCount || 5)).join("line")
        .attr("x1", 0).attr("x2", innerWidth).attr("y1", (d) => yScale(d)).attr("y2", (d) => yScale(d))
        .attr("stroke", CHART_COLORS.grid).attr("stroke-dasharray", "2,3").attr("stroke-width", 1);
    }

    if (isXCategorical && xScalePoint) {
      g.append("g").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(xScalePoint).tickSize(0).tickPadding(10))
        .call((ax) => ax.select(".domain").attr("stroke", CHART_COLORS.axis))
        .call((ax) => ax.selectAll(".tick text").attr("fill", CHART_TYPOGRAPHY.tickLabel.color).attr("font-size", CHART_TYPOGRAPHY.tickLabel.fontSize).attr("font-family", CHART_TYPOGRAPHY.fontFamily));
    } else if (xScaleLinear) {
      g.append("g").attr("transform", `translate(0,${innerHeight})`).call(d3.axisBottom(xScaleLinear).ticks(spec.x.tickCount || 6).tickFormat((d) => formatValue(d as number, spec.x.format)).tickSize(0).tickPadding(10))
        .call((ax) => ax.select(".domain").attr("stroke", CHART_COLORS.axis))
        .call((ax) => ax.selectAll(".tick text").attr("fill", CHART_TYPOGRAPHY.tickLabel.color).attr("font-size", CHART_TYPOGRAPHY.tickLabel.fontSize).attr("font-family", CHART_TYPOGRAPHY.fontFamily));
    }

    g.append("g").call(d3.axisLeft(yScale).ticks(spec.y.tickCount || 5).tickFormat((d) => formatValue(d as number, spec.y.format)).tickSize(0).tickPadding(10))
      .call((ax) => ax.select(".domain").remove())
      .call((ax) => ax.selectAll(".tick text").attr("fill", CHART_TYPOGRAPHY.tickLabel.color).attr("font-size", CHART_TYPOGRAPHY.tickLabel.fontSize).attr("font-family", CHART_TYPOGRAPHY.fontFamily));

    const getCurve = (c?: "smooth" | "step" | "linear") => c === "step" ? d3.curveStepAfter : c === "smooth" ? d3.curveMonotoneX : d3.curveLinear;

    if (spec.areaFill && spec.series.length === 1) {
      const s = spec.series[0];
      g.append("path").datum(spec.data).attr("fill", getSeriesColor(0, s.color)).attr("fill-opacity", 0.1)
        .attr("d", d3.area<Record<string, unknown>>().x((d) => getX(d)).y0(innerHeight).y1((d) => yScale(Number(d[s.field]))).curve(getCurve(s.curve)));
    }

    spec.series.forEach((s, i) => {
      g.append("path").datum(spec.data).attr("class", `line-path-${i}`).attr("fill", "none")
        .attr("stroke", getSeriesColor(i, s.color)).attr("stroke-width", s.lineWidth || 2.5)
        .attr("stroke-dasharray", getDashArray(s.lineStyle)).attr("stroke-linejoin", "round").attr("stroke-linecap", "round")
        .attr("d", d3.line<Record<string, unknown>>().x((d) => getX(d)).y((d) => yScale(Number(d[s.field]))).curve(getCurve(s.curve)).defined((d) => !isNaN(Number(d[s.field]))));
    });

    g.append("line").attr("class", "hover-line").attr("y1", 0).attr("y2", innerHeight).attr("stroke", CHART_COLORS.axis).attr("stroke-width", 1).attr("stroke-dasharray", "3,3").attr("opacity", 0);
    spec.series.forEach((s, i) => { g.append("circle").attr("class", `hover-dot-${i}`).attr("r", 4).attr("fill", "white").attr("stroke", getSeriesColor(i, s.color)).attr("stroke-width", 2).attr("opacity", 0); });
  }, [spec, innerWidth, innerHeight, margins, isXCategorical, xScalePoint, xScaleLinear, yScale, getX]);

  useEffect(() => {
    if (!inView) return;
    requestAnimationFrame(() => {
      const svg = d3.select(svgRef.current);
      spec.series.forEach((s, i) => {
        const path = svg.select<SVGPathElement>(`.line-path-${i}`);
        if (path.empty() || getDashArray(s.lineStyle) !== "none") return;
        const node = path.node();
        if (!node) return;
        const totalLength = node.getTotalLength();
        if (totalLength <= 0) return;
        path.attr("stroke-dasharray", `${totalLength} ${totalLength}`).attr("stroke-dashoffset", totalLength)
          .transition().duration(1000).delay(i * 150).ease(d3.easeCubicInOut).attr("stroke-dashoffset", 0);
      });
    });
  }, [inView]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const svg = d3.select(svgRef.current);
    if (tooltip) {
      const xPos = tooltip.x - margins.left;
      let closest: Record<string, unknown> | null = null;
      let closestDist = Infinity;
      for (const d of spec.data) { const dist = Math.abs(getX(d) - xPos); if (dist < closestDist) { closestDist = dist; closest = d; } }
      if (closest) {
        const cx = getX(closest);
        svg.select(".hover-line").attr("x1", cx).attr("x2", cx).attr("opacity", 1);
        spec.series.forEach((s, i) => { svg.select(`.hover-dot-${i}`).attr("cx", cx).attr("cy", yScale(Number(closest![s.field]))).attr("opacity", 1); });
      }
    } else {
      svg.select(".hover-line").attr("opacity", 0);
      spec.series.forEach((_, i) => svg.select(`.hover-dot-${i}`).attr("opacity", 0));
    }
  }, [tooltip, spec, getX, yScale, margins]);

  return (
    <div ref={containerRef} style={{ position: "relative", fontFamily: CHART_TYPOGRAPHY.sansFamily, width, borderTop: `2px solid ${CHART_COLORS.axis}`, paddingTop: "12px" }}>
      {spec.title && <div style={{ fontSize: CHART_TYPOGRAPHY.title.fontSize, fontWeight: CHART_TYPOGRAPHY.title.fontWeight, color: CHART_TYPOGRAPHY.title.color, marginBottom: "12px" }}>{spec.title}</div>}
      <svg ref={svgRef} width={width} height={height} onMouseMove={handleMouseMove} onMouseLeave={handleMouseLeave} style={{ overflow: "visible", display: "block" }} />
      {spec.showLegend !== false && spec.series.length > 1 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "16px", marginTop: "8px", paddingTop: "8px", paddingLeft: margins.left, borderTop: "1px solid #e5e7eb" }}>
          {spec.series.map((s, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <svg width="20" height="3"><line x1="0" y1="1.5" x2="20" y2="1.5" stroke={getSeriesColor(i, s.color)} strokeWidth="2.5" strokeDasharray={getDashArray(s.lineStyle)} /></svg>
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
