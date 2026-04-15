export function getBackendBase(): string {
  return process.env.NEXT_PUBLIC_BACKEND_URL || "/api/proxy";
}

export function getBackendEndpoint(path: string): string {
  const base = getBackendBase().replace(/\/$/, "");
  const cleanPath = path.replace(/^\//, "");
  return `${base}/${cleanPath}`;
}
