import { describe, expect, it } from "vitest";

import {
  APP_BASE_PATH,
  getAppBaseUrl,
  getBackendEndpoint,
} from "./backend";

describe("getAppBaseUrl", () => {
  it("appends the canonical base path to an origin", () => {
    expect(getAppBaseUrl("https://chat.example/")).toBe(
      "https://chat.example/uk/chat",
    );
  });
});

describe("getBackendEndpoint", () => {
  it("always uses the same-origin proxy below the application base path", () => {
    expect(APP_BASE_PATH).toBe("/uk/chat");
    expect(getBackendEndpoint("/chat/message")).toBe(
      "/uk/chat/api/proxy/chat/message",
    );
  });
});
