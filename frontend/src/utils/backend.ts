function slugifyBranchName(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-");
}

// Match the slug derivation in app/api/proxy/[...slug]/route.ts and in
// .github/workflows/pr-beta-deploy.yml — all three must agree on the Modal
// app name for a given branch.
function getPreviewBackendBase(): string | null {
  if (process.env.NEXT_PUBLIC_VERCEL_ENV !== "preview") return null;
  const gitRef = process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_REF;
  if (!gitRef) return null;
  return `https://policyengine--peukchat-${slugifyBranchName(gitRef)}-web.modal.run`;
}

export function getBackendBase(): string {
  const previewBackend = getPreviewBackendBase();
  if (previewBackend) return previewBackend;

  return process.env.NEXT_PUBLIC_BACKEND_URL || "/api/proxy";
}

export function getBackendEndpoint(path: string): string {
  const base = getBackendBase().replace(/\/$/, "");
  const cleanPath = path.replace(/^\//, "");
  return `${base}/${cleanPath}`;
}
