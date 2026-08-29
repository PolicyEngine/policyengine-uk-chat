import { describe, expect, it } from "vitest";

import { resolveBackendUrl } from "./backend-url";

describe("resolveBackendUrl", () => {
  it("uses the PR-scoped Modal backend for Vercel previews", () => {
    expect(
      resolveBackendUrl({
        VERCEL_ENV: "preview",
        VERCEL_GIT_PULL_REQUEST_ID: "233",
        VERCEL_GIT_COMMIT_REF: "agent/first-name",
        POLICYENGINE_UK_CHAT_BACKEND_URL:
          "https://policyengine--policyengine-uk-chat-web.modal.run",
      }),
    ).toBe("https://policyengine--pe-uk-chat-233-web.modal.run");
  });

  it("uses the configured backend outside Vercel previews", () => {
    expect(
      resolveBackendUrl({
        VERCEL_ENV: "production",
        POLICYENGINE_UK_CHAT_BACKEND_URL:
          "https://production-backend.example/",
      }),
    ).toBe("https://production-backend.example");
  });

  it("fails closed when preview PR metadata is missing", () => {
    expect(() =>
      resolveBackendUrl({
        VERCEL_ENV: "preview",
        VERCEL_GIT_COMMIT_REF: "agent/first-name",
        POLICYENGINE_UK_CHAT_BACKEND_URL:
          "https://production-backend.example",
      }),
    ).toThrow("VERCEL_GIT_PULL_REQUEST_ID is required");
  });

  it.each(["0", "-1", "233-preview", "  "])(
    "rejects malformed preview PR id %j",
    (pullRequestId) => {
      expect(() =>
        resolveBackendUrl({
          VERCEL_ENV: "preview",
          VERCEL_GIT_PULL_REQUEST_ID: pullRequestId,
        }),
      ).toThrow("VERCEL_GIT_PULL_REQUEST_ID is required");
    },
  );

  it("uses the same backend after a branch rename", () => {
    const beforeRename = resolveBackendUrl({
      VERCEL_ENV: "preview",
      VERCEL_GIT_PULL_REQUEST_ID: "233",
      VERCEL_GIT_COMMIT_REF: "agent/old-name",
    });
    const afterRename = resolveBackendUrl({
      VERCEL_ENV: "preview",
      VERCEL_GIT_PULL_REQUEST_ID: "233",
      VERCEL_GIT_COMMIT_REF: "agent/new-name",
    });

    expect(afterRename).toBe(beforeRename);
  });

  it("requires an explicit production backend", () => {
    expect(() => resolveBackendUrl({ VERCEL_ENV: "production" })).toThrow(
      "POLICYENGINE_UK_CHAT_BACKEND_URL is required",
    );
  });

  it("defaults to the local backend outside Vercel", () => {
    expect(resolveBackendUrl({})).toBe("http://localhost:8080");
  });
});
