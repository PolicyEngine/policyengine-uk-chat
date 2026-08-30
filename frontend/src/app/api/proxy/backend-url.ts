const LOCAL_BACKEND_URL = "http://localhost:8080";
const PULL_REQUEST_NUMBER = /^[1-9]\d*$/;

export function resolveBackendUrl(
  env: Readonly<Record<string, string | undefined>> = process.env,
): string {
  if (env.VERCEL_ENV === "preview") {
    // The GitHub preview workflow names each Modal app pe-uk-chat-<PR>.
    // Vercel exposes the same PR number at runtime, so branch renames and
    // Modal's hostname-length limit cannot make the two deployments diverge.
    const pullRequestNumber = env.VERCEL_GIT_PULL_REQUEST_ID?.trim();
    if (!pullRequestNumber || !PULL_REQUEST_NUMBER.test(pullRequestNumber)) {
      throw new Error(
        "A valid VERCEL_GIT_PULL_REQUEST_ID is required for preview backend routing.",
      );
    }
    return `https://policyengine--pe-uk-chat-${pullRequestNumber}-web.modal.run`;
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
