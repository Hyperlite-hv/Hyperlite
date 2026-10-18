import { renameContainer } from "../../api/client";
import { promptText } from "../../store/usePromptStore";
import { errorMessage } from "./errors";
import { NAME_RE } from "./containerImages";

// A root shell inside the container (docker exec), for Docker and LXC containers alike.
export const openShell = (name) => window.open(`/container-terminal/${encodeURIComponent(name)}?shell=1`, `hyperlite-ct-shell-${name}`, "width=1000,height=700,noopener");

// Asks for the new name of a stopped container and renames it; resolves to the new name, or null.
export async function renameContainerFlow(ct, t, pushToast) {
  const name = await promptText({ title: t("ct.renameTitle", { name: ct.name }), message: t("ct.renameMsg"), label: t("ct.renameName"), defaultValue: ct.name, confirmLabel: t("vx.rename"), validate: (v) => (!NAME_RE.test(v) ? t("ct.nameRule") : v === ct.name ? t("vx.renameSame") : "") });
  if (!name) return null;
  try {
    await renameContainer(ct.name, name.trim());
    pushToast({ kind: "success", title: t("ct.renamed"), message: `${ct.name} → ${name.trim()}` });
    return name.trim();
  } catch (e) { pushToast({ kind: "error", title: t("ct.renameFailed"), message: errorMessage(e) }); return null; }
}
