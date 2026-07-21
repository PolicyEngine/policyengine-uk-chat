const LOCAL_BACKEND_URL = "http://localhost:8080";

function slugifyBranchName(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-");
}

export function resolveBackendUrl(
  env: NodeJS.ProcessEnv = process.env,
): string {
  if (env.VERCEL_ENV === "preview") {
    if (!env.VERCEL_GIT_COMMIT_REF) {
      throw new Error(
        "VERCEL_GIT_COMMIT_REF is required for preview backend routing.",
      );
    }
    const branchSlug = slugifyBranchName(env.VERCEL_GIT_COMMIT_REF);
    return `https://policyengine--peukchat-${branchSlug}-web.modal.run`;
  }

  const configuredUrl = env.POLICYENGINE_UK_CHAT_BACKEND_URL?.trim();
  if (configuredUrl) return configuredUrl.replace(/\/$/, "");

  if (env.VERCEL_ENV === "production") {
    throw new Error(
      "POLICYENGINE_UK_CHAT_BACKEND_URL is required in production.",
    );
  }

  return LOCAL_BACKEND_URL;
}
