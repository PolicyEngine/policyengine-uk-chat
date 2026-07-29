import { describe, expect, it } from "vitest";

import {
  formatValue,
  getDashArray,
  getNiceDomain,
  getSeriesColor,
} from "./utils";

describe("formatValue", () => {
  it("formats currency at each supported magnitude", () => {
    expect(formatValue(999, "currency")).toBe("£999");
    expect(formatValue(1_250, "currency")).toBe("£1.3k");
    expect(formatValue(2_500_000, "currency")).toBe("£2.5m");
    expect(formatValue(-3_200_000_000, "currency")).toBe("£-3.2bn");
  });

  it("formats percentages, compact values, years, and ordinary numbers", () => {
    expect(formatValue(1.234, "percent")).toBe("1.2%");
    expect(formatValue(0.1234, "percent_decimal")).toBe("12.3%");
    expect(formatValue(1_500, "compact")).toBe("1.5k");
    expect(formatValue(2_500_000, "compact")).toBe("2.5m");
    expect(formatValue(3_200_000_000, "compact")).toBe("3.2bn");
    expect(formatValue(2025.8, "year")).toBe("2026");
    expect(formatValue(12.345)).toBe("12.35");
    expect(formatValue(Number.NaN)).toBe("—");
  });

  it("renders missing values as a dash rather than zero", () => {
    expect(formatValue(null, "currency")).toBe("—");
    expect(formatValue(undefined, "percent")).toBe("—");
  });
});

describe("chart style helpers", () => {
  it("uses custom colors and wraps the default palette", () => {
    expect(getSeriesColor(0)).toBe("#2C7A7B");
    expect(getSeriesColor(8)).toBe("#2C7A7B");
    expect(getSeriesColor(3, "#ffffff")).toBe("#ffffff");
  });

  it("maps line styles to SVG dash arrays", () => {
    expect(getDashArray()).toBe("none");
    expect(getDashArray("solid")).toBe("none");
    expect(getDashArray("dashed")).toBe("6,4");
    expect(getDashArray("dotted")).toBe("2,3");
  });
});

describe("getNiceDomain", () => {
  it("includes zero for non-negative data and snaps the upper bound", () => {
    expect(getNiceDomain([0, 10])).toEqual([0, 20]);
  });

  it("snaps mixed-sign data outwards", () => {
    expect(getNiceDomain([-3, 7])).toEqual([-5, 10]);
  });

  it("respects explicit bounds", () => {
    expect(getNiceDomain([2, 8], 1, 9)).toEqual([1, 9]);
  });

  it("handles a zero-width domain", () => {
    expect(getNiceDomain([0, 0])).toEqual([0, 0]);
  });
});
