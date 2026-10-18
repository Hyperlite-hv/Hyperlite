// Every route of the API is under one versioned prefix (docs/design/api-v1.md). What a browser opens rather than
// calls (the sign-in with SSO, the client download, the dashboard's own pages) and /health stay at the root.
export const API = "/api/v1";

export function apiPath(path) {
  return typeof path === "string" && path.startsWith("/") && !path.startsWith("/api/") && path !== "/health" ? API + path : path;
}
