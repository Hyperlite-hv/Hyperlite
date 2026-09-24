// Front-end capability rules. The backend stays the authority (role admin / ACL
// privileges); the legacy UI only knows `role === "admin"` and the new one keeps that
// exact rule until a privileges endpoint exists (improvement A-05). Never widen a right here.
export function capabilities(role) {
  const admin = role === "admin";
  return {
    admin,
    create: admin,
    power: admin,
    console: true, // every authenticated user may open a console window; the backend checks the privilege
    delete: admin,
    update: admin,
    hostShell: admin,
  };
}

// Explains why an action is unavailable instead of silently hiding/disabling it.
export function vmActionState(action, vm, caps) {
  if (!caps.power && action !== "console") return { enabled: false, reason: "menu.reason.admin" };
  const running = vm?.etat === "actif";
  switch (action) {
    case "start": return running ? { enabled: false, reason: "menu.reason.running" } : { enabled: true };
    case "stop": case "restart": return running ? { enabled: true } : { enabled: false, reason: "menu.reason.notRunning" };
    case "delete": return !caps.delete ? { enabled: false, reason: "menu.reason.admin" } : running ? { enabled: false, reason: "menu.reason.mustStop" } : { enabled: true };
    case "console": return running ? { enabled: true } : { enabled: false, reason: "menu.reason.notRunning" };
    default: return { enabled: true };
  }
}
