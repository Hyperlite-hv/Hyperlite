import { useRef, useState } from "react";
import { Plus, Box, Bell, Sun, Moon, LogOut, ShieldCheck as ShieldIcon, RefreshCw } from "lucide-react";
import SearchBar from "../components/SearchBar";
import VMWizard from "../wizard/VMWizard";
import ContainerWizard from "../wizard/ContainerWizard";
import UpdateModal from "../components/UpdateModal";
import AccountSecurityModal from "../components/AccountSecurityModal";
import { useInfraStore } from "../store/useInfraStore";
import { useAuthStore, selectIsAdmin } from "../store/useAuthStore";
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent,
  DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";

function initials(name) {
  if (!name) return "?";
  return name.slice(0, 2).toUpperCase();
}

// Breadcrumb: the Datacenter root is now clickable (it was purely decorative
// before the redesign — a real UX gap found at audit: users expect a breadcrumb
// segment to navigate). The leaf segment (current selection) stays plain text,
// matching the usual "you are here, everything before this is a link" pattern.
function Breadcrumb({ selection, nodes, vms, navigateTo }) {
  const crumb = selection.type === "node" ? nodes.find((n) => n.id === selection.id)?.nom || selection.id
    : selection.type === "vm" ? vms.find((v) => v.nom === selection.id)?.nom || selection.id
    : selection.type === "storage" ? selection.id
    : null;

  return (
    <div className="flex shrink-0 items-center gap-1.5 text-[13px] font-bold text-foreground">
      <button
        className="hover:text-primary transition-colors"
        onClick={() => navigateTo("datacenter", null, "summary")}
      >
        Datacenter
      </button>
      {crumb && (
        <>
          <span className="text-muted-foreground font-normal">/</span>
          <span>{crumb}</span>
        </>
      )}
    </div>
  );
}

export default function Header() {
  const [wizardOpen, setWizardOpen] = useState(false);
  const createVmTriggerRef = useRef(null);
  const [containerWizardOpen, setContainerWizardOpen] = useState(false);
  const createContainerTriggerRef = useRef(null);
  const [updateOpen, setUpdateOpen] = useState(false);
  const updateTriggerRef = useRef(null);
  const [securityOpen, setSecurityOpen] = useState(false);
  const securityTriggerRef = useRef(null);

  const theme = useInfraStore((s) => s.theme);
  const toggleTheme = useInfraStore((s) => s.toggleTheme);
  const tasks = useInfraStore((s) => s.tasks);
  const selection = useInfraStore((s) => s.selection);
  const nodes = useInfraStore((s) => s.nodes);
  const vms = useInfraStore((s) => s.vms);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const username = useAuthStore((s) => s.username);
  const isAdmin = useAuthStore(selectIsAdmin);
  const logout = useAuthStore((s) => s.logout);
  const { isMobile } = useSidebar();

  const runningCount = tasks.filter((t) => t.statut === "en_cours").length;
  const recentTasks = tasks.slice(0, 5);

  return (
    <header className="flex h-[60px] shrink-0 items-center gap-3 border-b border-border bg-card px-4 md:gap-4 md:px-6">
      <SidebarTrigger aria-label="Open navigation" className="md:hidden" />

      {/* Not just CSS-hidden below md: conditionally UNMOUNTED, so its "Datacenter"
          root-crumb text does not sit ahead of the always-visible page title (see
          CentralPanel.jsx's <h2>) in DOM order and get matched first (and found
          hidden) by anything that queries by that exact text on a narrow screen. */}
      {!isMobile && <Breadcrumb selection={selection} nodes={nodes} vms={vms} navigateTo={navigateTo} />}

      <Separator orientation="vertical" className="hidden h-5 md:block" />

      <div className="hidden w-[240px] shrink-0 sm:block">
        <SearchBar />
      </div>

      <div className="flex-1" />

      <div className="flex items-center gap-2 shrink-0">
        {isAdmin && (
          <Button ref={createVmTriggerRef} aria-label="Create VM" className="rounded-full" onClick={() => setWizardOpen(true)}>
            <Plus /> <span className="hidden sm:inline">Create VM</span>
          </Button>
        )}
        {isAdmin && (
          <Button ref={createContainerTriggerRef} variant="secondary" className="hidden sm:inline-flex" onClick={() => setContainerWizardOpen(true)}>
            <Box /> Create container
          </Button>
        )}

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="Recent tasks" className="relative">
              <Bell />
              {runningCount > 0 && (
                <Badge className="absolute -top-1 -right-1 h-4 w-4 justify-center rounded-full p-0 text-[10px] bg-accent-orange text-white">
                  {runningCount}
                </Badge>
              )}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-72">
            <DropdownMenuLabel>Recent tasks</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {recentTasks.length === 0 && <div className="px-2 py-2 text-sm text-muted-foreground">No tasks.</div>}
            {recentTasks.map((t) => (
              <div key={t.id} className="px-2 py-1.5 text-sm">
                <div className="flex justify-between text-foreground">
                  <span>{t.type}</span>
                  <span className="text-xs text-muted-foreground">{t.cible}</span>
                </div>
              </div>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" className="flex items-center gap-2 rounded-full py-1 pl-1 pr-2.5">
              <Avatar className="h-8 w-8">
                <AvatarFallback className="bg-linear-to-br from-accent-blue to-[#4338CA] text-xs font-bold text-white">
                  {initials(username)}
                </AvatarFallback>
              </Avatar>
              <span className="hidden text-[12.5px] font-semibold text-foreground sm:inline">{username}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuLabel>
              {username} <span className="text-xs font-normal text-muted-foreground">({isAdmin ? "admin" : "observer"})</span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem role="button" onClick={toggleTheme}>
              {theme === "dark" ? <Sun /> : <Moon />}
              {theme === "dark" ? "Light mode" : "Dark mode"}
            </DropdownMenuItem>
            {isAdmin && (
              <DropdownMenuItem ref={updateTriggerRef} role="button" onClick={() => setUpdateOpen(true)}>
                <RefreshCw /> Check for updates
              </DropdownMenuItem>
            )}
            <DropdownMenuItem ref={securityTriggerRef} role="button" onClick={() => setSecurityOpen(true)}>
              <ShieldIcon /> Account security
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem role="button" onClick={logout} variant="destructive">
              <LogOut /> Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <VMWizard open={wizardOpen} onClose={() => setWizardOpen(false)} triggerRef={createVmTriggerRef} />
      <ContainerWizard open={containerWizardOpen} onClose={() => setContainerWizardOpen(false)} triggerRef={createContainerTriggerRef} />
      <UpdateModal open={updateOpen} onClose={() => setUpdateOpen(false)} triggerRef={updateTriggerRef} />
      <AccountSecurityModal open={securityOpen} onClose={() => setSecurityOpen(false)} triggerRef={securityTriggerRef} />
    </header>
  );
}
