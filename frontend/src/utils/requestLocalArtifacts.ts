export interface PublicResponseArtifact {
  kind: "chart";
  artifact_id: string;
  content: string;
}

const PERSISTED_CHART_PLACEHOLDER =
  "[Chart data was available only in the original response.]";

export function requestLocalArtifactText(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value
    .filter(
      (artifact): artifact is PublicResponseArtifact =>
        typeof artifact === "object" &&
        artifact !== null &&
        (artifact as Record<string, unknown>).kind === "chart" &&
        typeof (artifact as Record<string, unknown>).artifact_id === "string" &&
        typeof (artifact as Record<string, unknown>).content === "string",
    )
    .map((artifact) => artifact.content)
    .join("\n\n");
}

/** Remove request-local chart datasets before a message is saved or titled. */
export function stripRequestLocalChartData(text: string): string {
  return text
    .replace(/```chart\s*[\s\S]*?```/g, PERSISTED_CHART_PLACEHOLDER)
    .replace(/```chart[\s\S]*$/g, PERSISTED_CHART_PLACEHOLDER);
}

