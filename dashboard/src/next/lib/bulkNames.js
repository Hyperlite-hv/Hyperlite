// The names of N copies: prefix-01, prefix-02... (two digits at least, more when the count needs them), and why one
// cannot be used (its form, or a VM already called so).
const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/;

export function bulkNames(prefix, start, count) {
  const last = start + count - 1;
  const width = Math.max(2, String(last).length);
  return Array.from({ length: count }, (_, i) => `${prefix}-${String(start + i).padStart(width, "0")}`);
}

export function bulkProblems(names, taken) {
  const out = {};
  for (const n of names) {
    if (!NAME_RE.test(n)) out[n] = "rule";
    else if (taken.has(n)) out[n] = "taken";
  }
  return out;
}
