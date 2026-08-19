export const DEFAULT_CONFLICT_CONTENT =
  "This request could not be applied because the conversation changed while it was processing. Retry it using the latest conversation state.";

export interface ConflictStreamControl {
  kind: "conflict";
  content: string;
  sessionId?: string;
  turnId?: string;
  retryable: boolean;
}

export interface FailureStreamControl {
  kind: "failure";
  content: string;
  sessionId?: string;
  turnId?: string;
  stopReason?: string;
  costGbp?: number;
}

/** Normalize control events that end an otherwise successful SSE connection. */
export function reduceChatStreamControlEvent(
  value: unknown,
): ConflictStreamControl | FailureStreamControl | null {
  if (typeof value !== "object" || value === null) return null;
  const event = value as Record<string, unknown>;
  if (event.type === "error") {
    return {
      kind: "failure",
      content:
        typeof event.content === "string" && event.content.trim()
          ? event.content
          : "The analysis could not complete. Please try again.",
      sessionId:
        typeof event.session_id === "string" && event.session_id
          ? event.session_id
          : undefined,
      turnId:
        typeof event.turn_id === "string" && event.turn_id
          ? event.turn_id
          : undefined,
      stopReason:
        typeof event.stop_reason === "string" && event.stop_reason
          ? event.stop_reason
          : undefined,
      costGbp:
        typeof event.cost_gbp === "number" ? event.cost_gbp : undefined,
    };
  }
  if (event.type !== "conflict") return null;

  return {
    kind: "conflict",
    content:
      typeof event.content === "string" && event.content.trim()
        ? event.content
        : DEFAULT_CONFLICT_CONTENT,
    sessionId:
      typeof event.session_id === "string" && event.session_id
        ? event.session_id
        : undefined,
    turnId:
      typeof event.turn_id === "string" && event.turn_id
        ? event.turn_id
        : undefined,
    retryable: event.retryable === true,
  };
}
