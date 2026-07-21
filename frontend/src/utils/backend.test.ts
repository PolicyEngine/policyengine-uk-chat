import { afterEach, describe, expect, it, vi } from "vitest";

import { getBackendBase, getBackendEndpoint } from "./backend";

const ORIGINAL_BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL;

afterEach(() => {
  vi.unstubAllGlobals();
  if (ORIGINAL_BACKEND_URL === undefined) {
    delete process.env.NEXT_PUBLIC_BACKEND_URL;
  } else {
    process.env.NEXT_PUBLIC_BACKEND_URL = ORIGINAL_BACKEND_URL;
  }
});

describe("getBackendBase", () => {
  it.each([
    "policyengine-uk-chat-git-chart-tests-policy-engine.vercel.app",
    "policyengine-uk-chat-np3428jxf-policy-engine.vercel.app",
    "policyengine-uk-chat.vercel.app",
  ])("routes Vercel hostname %s through the same-origin proxy", (hostname) => {
    vi.stubGlobal("window", {
      location: { hostname },
    });
    process.env.NEXT_PUBLIC_BACKEND_URL = "https://production-backend.example";

    expect(getBackendBase()).toBe("/api/proxy");
  });

  it("uses the configured backend outside preview deployments", () => {
    vi.stubGlobal("window", { location: { hostname: "localhost" } });
    process.env.NEXT_PUBLIC_BACKEND_URL = "https://backend.example";

    expect(getBackendBase()).toBe("https://backend.example");
  });

  it("falls back to the local proxy", () => {
    vi.stubGlobal("window", { location: { hostname: "localhost" } });
    delete process.env.NEXT_PUBLIC_BACKEND_URL;

    expect(getBackendBase()).toBe("/api/proxy");
  });
});

describe("getBackendEndpoint", () => {
  it("normalizes slashes between the base URL and path", () => {
    vi.stubGlobal("window", { location: { hostname: "localhost" } });
    process.env.NEXT_PUBLIC_BACKEND_URL = "https://backend.example/";

    expect(getBackendEndpoint("/chat")).toBe("https://backend.example/chat");
  });
});
