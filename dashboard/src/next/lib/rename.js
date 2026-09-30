import { promptText } from "../../store/usePromptStore";

// The same object name rule as the API for VMs, containers, networks, storage pools and templates.
export const OBJECT_NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/;

// Asks for an object's new name: resolves to it (trimmed), or null when cancelled or unchanged.
// rule: a regular expression the name must match, or null for a free label (1 to 100 characters).
export async function askNewName(t, { title, message, current, rule = OBJECT_NAME_RE, ruleText }) {
  const name = await promptText({
    title, message, label: t("rn.newName"), defaultValue: current, confirmLabel: t("vx.rename"),
    validate: (v) => {
      const s = v.trim();
      if (rule ? !rule.test(s) : !s || s.length > 100) return ruleText || t(rule ? "rn.nameRule" : "rn.labelRule");
      return s === current ? t("vx.renameSame") : "";
    },
  });
  return name ? name.trim() : null;
}
