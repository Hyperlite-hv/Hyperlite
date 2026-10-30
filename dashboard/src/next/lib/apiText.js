// The API speaks English. A few of its texts are shown as they are, in place, for as long as a state lasts (why
// automatic HA cannot restart, why the console needs a restart, the predefined roles): those are translated here
// by their exact wording or their key. Any other text still shows as the API wrote it.
const KNOWN = {
  "Only one node: there is nowhere to restart a VM": "api.haOneNode",
  "Two nodes and no witness: automatic restart stays off (a network cut would look like a failure on both sides)": "api.haTwoNoWitness",
  "This VM was created before the console was added. Stop it, then start it once again to enable the console.": "api.consoleNeedsRestart",
};

export function localizeApiText(t, text) {
  if (typeof text !== "string") return text;
  const key = KNOWN[text.trim()] || KNOWN[text.trim().replace(/\.$/, "")];
  return key ? t(key) : text;
}

// The predefined scoped roles (app/core/permissions.py ROLES) by their key; a custom role keeps the name it was given.
const PRESET_ROLES = ["drive", "operator", "manager", "store_reader"];

export function localizeRoles(t, roles) {
  return Object.fromEntries(Object.entries(roles || {}).map(([key, r]) => [
    key,
    PRESET_ROLES.includes(key) ? { ...r, label: t(`sec.preset.${key}`), description: t(`sec.presetHelp.${key}`) } : r,
  ]));
}
