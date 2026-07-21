export function getBackendBase(): string {
  if (
    typeof window !== "undefined" &&
    window.location.hostname.endsWith(".vercel.app")
  ) {
    return "/api/proxy";
  }

  return process.env.NEXT_PUBLIC_BACKEND_URL || "/api/proxy";
}

export function getBackendEndpoint(path: string): string {
  const base = getBackendBase().replace(/\/$/, "");
  const cleanPath = path.replace(/^\//, "");
  return `${base}/${cleanPath}`;
}
