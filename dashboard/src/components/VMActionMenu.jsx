import { useEffect, useRef } from "react";
import { useInfraStore } from "../store/useInfraStore";
import { statusColor } from "../theme/colors";

// Menu d'actions ancre pres de la carte VM cliquee (ecran 6a de la refonte
// 2026-09-13) : memes entrees que la palette ⌘K existante (SearchBar),
// affichees au clic plutot que de forcer a connaitre les raccourcis --
// "⌘K devient une commodite, plus un prerequis" (note de la maquette).
export default function VMActionMenu({ vm, anchorRect, onClose }) {
  const runVMAction = useInfraStore((s) => s.runVMAction);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const menuRef = useRef(null);
  const active = vm.etat === "actif";

  useEffect(() => {
    function onDocClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) onClose();
    }
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  function go(tab) {
    navigateTo("vm", vm.nom, tab);
    onClose();
  }
  function run(action, opts) {
    runVMAction(vm.nom, action, opts).catch(() => {});
    onClose();
  }

  const items = active
    ? [
        { label: "Ouvrir la console", key: "C", onClick: () => go("console") },
        { label: "Créer un instantané", key: "S", onClick: () => go("snapshots") },
        { label: "Modifier le matériel", onClick: () => go("hardware") },
        { label: "Cloner", onClick: () => go("options") },
        { divider: true },
        { label: "Redémarrer", onClick: () => run("restart") },
        { label: "Arrêter", danger: true, onClick: () => run("stop") },
      ]
    : [
        { label: "Démarrer", primary: true, onClick: () => run("start") },
        { label: "Modifier le matériel", onClick: () => go("hardware") },
        { label: "Cloner", onClick: () => go("options") },
        { divider: true },
        { label: "Supprimer…", danger: true, onClick: () => go("summary") },
      ];

  const top = anchorRect ? anchorRect.bottom + 6 : 0;
  const left = anchorRect ? Math.min(anchorRect.left, window.innerWidth - 300) : 0;

  return (
    <div
      ref={menuRef}
      style={{ position: "fixed", top, left, width: 260 }}
      className="z-50 flex flex-col rounded-lg border border-anthracite-500 bg-anthracite-700 py-1.5 shadow-xl"
    >
      <div className="flex items-center gap-2 px-3.5 py-1.5">
        <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: statusColor(vm.etat) }} />
        <span className="text-[12.5px] font-semibold text-anthracite-100">{vm.nom}</span>
        <span className="ml-auto font-mono text-[10px] text-anthracite-400">{vm.vcpu} vCPU · {vm.memoire_mo} Mo</span>
      </div>
      <div className="my-0.5 h-px bg-anthracite-600" />
      {items.map((item, i) =>
        item.divider ? (
          <div key={i} className="my-0.5 h-px bg-anthracite-600" />
        ) : (
          <button
            key={item.label}
            onClick={item.onClick}
            className={`flex items-center gap-2.5 px-3.5 py-2 text-left text-[13px] hover:bg-anthracite-600 ${
              item.danger ? "text-status-error" : item.primary ? "text-accent-blue" : "text-anthracite-200"
            }`}
          >
            {item.label}
            {item.key && (
              <span className="ml-auto rounded border border-anthracite-500 px-1 font-mono text-[10px] text-anthracite-400">{item.key}</span>
            )}
          </button>
        )
      )}
      <div className="mt-0.5 h-px bg-anthracite-600" />
      <div className="px-3.5 pt-1.5 font-mono text-[10px] text-anthracite-400">Les mêmes actions sous ⌘K</div>
    </div>
  );
}
