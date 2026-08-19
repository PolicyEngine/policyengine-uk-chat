import { afterEach, describe, expect, it, vi } from "vitest";

import { createTurnId, postChatTurnWithRetry } from "./turnId";

describe("createTurnId", () => {
  afterEach(() => vi.restoreAllMocks());

  it("reuses the same serialized turn identifier for an automatic retry", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue(
      "123e4567-e89b-12d3-a456-426614174000",
    );
    const turnId = createTurnId();
    const request = {
      method: "POST",
      body: JSON.stringify({
        messages: [{ role: "user", content: "Run it" }],
        turn_id: turnId,
      }),
    };
    const fetchImplementation = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network unavailable"))
      .mockResolvedValueOnce({ status: 200 } as Response);

    const response = await postChatTurnWithRetry(
      "/api/chat/message",
      request,
      fetchImplementation,
    );

    const firstBody = fetchImplementation.mock.calls[0][1]?.body;
    const retryBody = fetchImplementation.mock.calls[1][1]?.body;

    expect(response.status).toBe(200);
    expect(fetchImplementation).toHaveBeenCalledTimes(2);
    expect(firstBody).toBe(retryBody);
    expect(JSON.parse(String(firstBody)).turn_id).toBe(turnId);
    expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  });

  it("does not retry an aborted request", async () => {
    const abortError = Object.assign(new Error("aborted"), { name: "AbortError" });
    const fetchImplementation = vi.fn().mockRejectedValue(abortError);

    await expect(
      postChatTurnWithRetry("/api/chat/message", {}, fetchImplementation),
    ).rejects.toBe(abortError);
    expect(fetchImplementation).toHaveBeenCalledTimes(1);
  });

  it("creates a different identifier for a semantic retry", () => {
    vi.spyOn(crypto, "randomUUID")
      .mockReturnValueOnce("123e4567-e89b-12d3-a456-426614174000")
      .mockReturnValueOnce("123e4567-e89b-12d3-a456-426614174001");

    expect(createTurnId("123e4567-e89b-12d3-a456-426614174000")).toBe(
      "123e4567-e89b-12d3-a456-426614174001",
    );
    expect(crypto.randomUUID).toHaveBeenCalledTimes(2);
  });
});
