import { describe, expect, it } from "vitest";

import { resolveBackendUrl } from "./backend-url";

describe("resolveBackendUrl", () => {
  it("uses the branch-scoped Modal backend for Vercel previews", () => {
    expect(
      resolveBackendUrl({
        VERCEL_ENV: "preview",
        VERCEL_GIT_COMMIT_REF: "agent/pepy-migration",
        POLICYENGINE_UK_CHAT_BACKEND_URL:
          "https://policyengine--policyengine-uk-chat-web.modal.run",
      }),
    ).toBe(
      "https://policyengine--peukchat-agent-pepy-migration-web.modal.run",
    );
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

  it("slugifies non-alphanumeric branch characters", () => {
    expect(
      resolveBackendUrl({
        VERCEL_ENV: "preview",
        VERCEL_GIT_COMMIT_REF: "Feature/Preview_URL",
      }),
    ).toBe("https://policyengine--peukchat-feature-preview-url-web.modal.run");
  });

  it("fails closed when preview branch metadata is missing", () => {
    expect(() =>
      resolveBackendUrl({
        VERCEL_ENV: "preview",
        POLICYENGINE_UK_CHAT_BACKEND_URL:
          "https://production-backend.example",
      }),
    ).toThrow("VERCEL_GIT_COMMIT_REF is required");
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
