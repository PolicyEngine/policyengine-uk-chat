import { describe, expect, it } from "vitest";

import {
  DEFAULT_CONFLICT_CONTENT,
  reduceChatStreamControlEvent,
} from "./chatStream";

describe("reduceChatStreamControlEvent", () => {
  it("normalizes a complete retryable conflict", () => {
    expect(
      reduceChatStreamControlEvent({
        type: "conflict",
        content: "Retry this request.",
        session_id: "session-1",
        turn_id: "turn-1",
        retryable: true,
      }),
    ).toEqual({
      kind: "conflict",
      content: "Retry this request.",
      sessionId: "session-1",
      turnId: "turn-1",
      retryable: true,
    });
  });

  it("uses safe defaults for an incomplete conflict event", () => {
    expect(reduceChatStreamControlEvent({ type: "conflict" })).toEqual({
      kind: "conflict",
      content: DEFAULT_CONFLICT_CONTENT,
      sessionId: undefined,
      turnId: undefined,
      retryable: false,
    });
  });

  it("normalizes a failed turn as a final control event", () => {
    expect(
      reduceChatStreamControlEvent({
        type: "error",
        content: "The calculation failed.",
        session_id: "session-1",
        turn_id: "turn-1",
        stop_reason: "execution_failed",
        cost_gbp: 0.02,
      }),
    ).toEqual({
      kind: "failure",
      content: "The calculation failed.",
      sessionId: "session-1",
      turnId: "turn-1",
      stopReason: "execution_failed",
      costGbp: 0.02,
    });
  });

  it("ignores ordinary stream events", () => {
    expect(
      reduceChatStreamControlEvent({ type: "chunk", content: "Hello" }),
    ).toBeNull();
  });
});
