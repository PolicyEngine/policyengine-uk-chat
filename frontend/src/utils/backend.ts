import appPaths from "../../app-paths.json";

export const APP_BASE_PATH = appPaths.basePath;
const BACKEND_PROXY_PATH = `${APP_BASE_PATH}/api/proxy`;

export function getAppBaseUrl(origin: string): string {
  return `${origin.replace(/\/$/, "")}${APP_BASE_PATH}`;
}

export function getBackendEndpoint(path: string): string {
  const cleanPath = path.replace(/^\//, "");
  return `${BACKEND_PROXY_PATH}/${cleanPath}`;
}
