import { useState } from "react";
import { Monitor, TerminalSquare, ExternalLink } from "lucide-react";
import { useAuthStore, selectIsAdmin } from "../../store/useAuthStore";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

// Opens the console/terminal in a separate browser window (like Proxmox/vSphere)
// rather than in the current tab: the dashboard stays usable while a console
// remains open next to it. The real connection logic (WebSocket + noVNC/xterm.js)
// lives in ConsolePanel, rendered by the dedicated page src/console/ConsoleWindow.jsx
// that this window opens.
export default function VMConsoleTab({ resource: vm }) {
  const isAdmin = useAuthStore(selectIsAdmin);
  const [mode, setMode] = useState("vnc"); // "vnc" | "terminal"

  if (!isAdmin) {
    return <p className="text-sm text-muted-foreground">Console reserved for the admin role.</p>;
  }
  if (!vm) return null;

  function openWindow() {
    const url = `/console/${encodeURIComponent(vm.nom)}?mode=${mode}`;
    // Stable window name (per VM + mode): clicking several times refocuses the same
    // window instead of opening a new one each time.
    window.open(url, `hyperlite-console-${vm.nom}-${mode}`, "width=1100,height=750,noopener");
  }

  return (
    <div className="space-y-4">
      <Tabs value={mode} onValueChange={setMode}>
        <TabsList className="w-fit">
          <TabsTrigger value="vnc"><Monitor /> Graphical console (VNC)</TabsTrigger>
          <TabsTrigger value="terminal"><TerminalSquare /> SSH terminal</TabsTrigger>
        </TabsList>
      </Tabs>

      <Card className="flex flex-col items-center gap-3 p-10 text-center">
        {mode === "vnc" ? <Monitor size={28} className="text-muted-foreground" /> : <TerminalSquare size={28} className="text-muted-foreground" />}
        <p className="text-sm text-foreground/80">
          {mode === "vnc" ? "The graphical console" : "The SSH terminal"} opens in a separate window, to keep this dashboard usable while the connection stays open.
        </p>
        <Button disabled={vm.etat !== "actif"} onClick={openWindow}>
          <ExternalLink /> Open in a new window
        </Button>
        {vm.etat !== "actif" && <p className="text-xs text-muted-foreground">The VM must be started.</p>}
      </Card>
    </div>
  );
}
