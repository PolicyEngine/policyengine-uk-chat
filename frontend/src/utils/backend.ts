function getPreviewBackendBase(): string | null {
  if (typeof window === "undefined") return null;

  const match = window.location.hostname.match(
    /^policyengine-uk-chat-git-(.+)-policy-engine\.vercel\.app$/,
  );
  if (!match) return null;

  return `https://policyengine--peukchat-${match[1]}-web.modal.run`;
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
