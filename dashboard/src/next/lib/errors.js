// FastAPI answers with `detail` as a string, a list of strings, or (validation, 422) a
// list of {loc, msg, type}. The legacy client joins whatever it gets, which printed
// "[object Object]" for validation errors.
export function normalizeDetail(detail) {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(normalizeDetail).filter(Boolean).join(" ; ");
  if (typeof detail === "object") {
    if (detail.msg) {
      const where = Array.isArray(detail.loc) ? detail.loc.filter((p) => p !== "body").join(".") : "";
      return where ? `${where}: ${detail.msg}` : detail.msg;
    }
    try { return JSON.stringify(detail); } catch { return String(detail); }
  }
  return String(detail);
}

export function errorMessage(err, fallback = "Unknown error") {
  const m = err?.message;
  if (!m) return fallback;
  return m.includes("[object Object]") ? fallback : m;
}
