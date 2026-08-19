import { describe, expect, it } from "vitest";

import {
  requestLocalArtifactText,
  stripRequestLocalChartData,
} from "./requestLocalArtifacts";

describe("request-local response artifacts", () => {
  it("extracts only typed chart artifacts from a final event", () => {
    expect(
      requestLocalArtifactText([
        {
          kind: "chart",
          artifact_id: "chart_1",
          content: '```chart\n{"type":"bar","data":[{"secret":91}]}\n```',
        },
        { kind: "unknown", artifact_id: "other", content: "do not include" },
      ]),
    ).toContain('"secret":91');
  });

  it("removes complete and incomplete chart data before persistence", () => {
    const complete =
      'Before\n```chart\n{"type":"bar","data":[{"secret":91}]}\n```\nAfter';
    const incomplete = 'Before\n```chart\n{"type":"bar","secret":91}';

    expect(stripRequestLocalChartData(complete)).not.toContain("secret");
    expect(stripRequestLocalChartData(complete)).toContain("original response");
    expect(stripRequestLocalChartData(incomplete)).not.toContain("secret");
  });
});

