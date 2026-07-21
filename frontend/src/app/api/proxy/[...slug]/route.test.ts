import { afterEach, describe, expect, it } from "vitest";

import { getBackendUrl } from "./route";

const ORIGINAL_ENV = {
  BACKEND_URL: process.env.BACKEND_URL,
  VERCEL_ENV: process.env.VERCEL_ENV,
  VERCEL_GIT_COMMIT_REF: process.env.VERCEL_GIT_COMMIT_REF,
};

afterEach(() => {
  for (const [name, value] of Object.entries(ORIGINAL_ENV)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
});

describe("getBackendUrl", () => {
  it("uses the branch-scoped Modal backend for Vercel previews", () => {
    process.env.VERCEL_ENV = "preview";
    process.env.VERCEL_GIT_COMMIT_REF = "agent/pepy-migration";
    process.env.BACKEND_URL =
      "https://policyengine--policyengine-uk-chat-web.modal.run";

    expect(getBackendUrl()).toBe(
      "https://policyengine--peukchat-agent-pepy-migration-web.modal.run",
    );
  });

  it("uses BACKEND_URL outside Vercel previews", () => {
    process.env.VERCEL_ENV = "production";
    process.env.VERCEL_GIT_COMMIT_REF = "main";
    process.env.BACKEND_URL = "https://production-backend.example";

    expect(getBackendUrl()).toBe("https://production-backend.example");
  });

  it("slugifies non-alphanumeric branch characters", () => {
    process.env.VERCEL_ENV = "preview";
    process.env.VERCEL_GIT_COMMIT_REF = "Feature/Preview_URL";
    delete process.env.BACKEND_URL;

    expect(getBackendUrl()).toBe(
      "https://policyengine--peukchat-feature-preview-url-web.modal.run",
    );
  });
});
