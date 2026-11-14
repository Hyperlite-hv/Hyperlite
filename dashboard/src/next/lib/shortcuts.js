// Keyboard shortcuts of the console, outside any field being typed in: "g" then a letter goes to a service, "c" opens
// Create, "?" lists them all, and in a list "j" / "k" move from row to row ("x" ticks the row, Enter opens it).
// Kept here, apart from the components, so the list the help shows is the list that works.

export const GO = {
  h: "summary", v: "vms", c: "containers", n: "nodes", s: "storage", r: "network",
  b: "backups", a: "activity", p: "perf", e: "explorer", t: "topology", l: "alerts",
};
export const SEQUENCE_MS = 1200;

export function isTyping(target) {
  const tag = (target?.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || Boolean(target?.isContentEditable);
}

// The rows of the list the focus is in, or of the first list of the page.
function rows() {
  const inList = document.activeElement?.closest?.("table");
  const table = inList || document.querySelector("#nx-main table.hc-grid--list, #nx-main table.nx-table");
  // Group headings (a node, a tag) and detail rows are not rows of the list.
  return table ? [...table.querySelectorAll("tbody tr")].filter((tr) => tr.offsetParent !== null && !tr.matches(".hc-grprow") && !tr.querySelector(".nx-detailcell") && primary(tr)) : [];
}
// What a row opens: its name link or button, else its first control that is not a checkbox.
function primary(tr) {
  return tr.querySelector("th .nx-lnk, td .nx-lnk, th button, .hc-adv-open, .hc-al-open")
    || tr.querySelector("button:not([disabled]), a[href]");
}

// Moves the focus to the next (+1) or previous (-1) row; returns whether a list was there.
export function moveRow(delta) {
  const list = rows();
  if (!list.length) return false;
  const current = list.findIndex((tr) => tr.contains(document.activeElement));
  const next = current < 0 ? (delta > 0 ? 0 : list.length - 1) : Math.max(0, Math.min(list.length - 1, current + delta));
  for (const tr of list) tr.classList.remove("is-kbd");
  list[next].classList.add("is-kbd");
  primary(list[next]).focus({ preventScroll: false });
  list[next].scrollIntoView({ block: "nearest" });
  return true;
}

// Ticks or unticks the checkbox of the focused row.
export function toggleRow() {
  const tr = document.activeElement?.closest?.("tr");
  const box = tr?.querySelector('input[type="checkbox"]');
  if (!box) return false;
  box.click();
  return true;
}
