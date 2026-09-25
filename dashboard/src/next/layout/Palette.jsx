import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT } from "../i18n";
import { normalize } from "../lib/format";
import { capabilities, vmActionState } from "../lib/capabilities";
import { useVmActions } from "../lib/vmActions";
import StatusIndicator from "../components/StatusIndicator";
import { stateInfo } from "../lib/enums";

// Command palette (Ctrl/Cmd+K): resources + actions + navigation, one keyboard-first listbox.
export default function Palette({ open, onClose, setWizards }) {
  const t = useT();
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const inputRef = useRef(null);
  const { nodes, vms, navigateTo, select } = useInfraStore(useShallow((s) => ({ nodes: s.nodes, vms: s.vms, navigateTo: s.navigateTo, select: s.select })));
  const role = useAuthStore((s) => s.role);
  const caps = capabilities(role);
  const vmActions = useVmActions();

  useEffect(() => { if (open) { setQ(""); setIdx(0); setTimeout(() => inputRef.current?.focus(), 0); } }, [open]);

  const items = useMemo(() => {
    const nq = normalize(q.trim());
    const match = (s) => !nq || normalize(s).includes(nq);
    const res = [
      { group: "cmd.group.resources", id: "dc", label: t("inv.datacenter"), hint: null, state: null, hay: t("inv.datacenter"), run: () => select("datacenter", null) },
      ...nodes.map((n) => ({ group: "cmd.group.resources", id: `n:${n.id}`, label: n.nom, hint: t("res.node"), state: stateInfo("node", n.etat), hay: `${n.nom} ${n.ip || ""}`, run: () => select("node", n.id) })),
      ...vms.map((v) => ({ group: "cmd.group.resources", id: `v:${v.node}:${v.nom}`, label: v.nom, hint: [v.ip, nodes.find((n) => n.id === v.node)?.nom].filter(Boolean).join(" · "), state: stateInfo("vm", v.etat), hay: `${v.nom} ${v.ip || ""} ${v.os || ""} ${v.etat} ${v.node}`, run: () => select("vm", v.nom) })),
    ].filter((i) => match(i.hay)).slice(0, 30);
    const actions = [];
    if (caps.create) {
      actions.push({ group: "cmd.group.actions", id: "a:vm", label: `${t("action.create")} · ${t("action.createVm")}`, run: () => setWizards({ vm: true }) });
      actions.push({ group: "cmd.group.actions", id: "a:ct", label: `${t("action.create")} · ${t("action.createContainer")}`, run: () => setWizards({ container: true }) });
    }
    // Contextual VM actions only when the query names a VM exactly enough to be unambiguous.
    if (nq) {
      for (const v of vms.filter((x) => normalize(x.nom).includes(nq)).slice(0, 3)) {
        for (const a of ["start", "stop", "restart"]) {
          const st = vmActionState(a, v, caps);
          if (st.enabled) actions.push({ group: "cmd.group.actions", id: `a:${a}:${v.nom}`, label: `${t(`menu.${a}`)} ${v.nom}`, run: () => vmActions.run(v, a) });
        }
      }
    }
    const go = [
      ["tab.summary", () => navigateTo("datacenter", null, "summary")], ["tab.monitor", () => navigateTo("datacenter", null, "activity")],
      ["tab.configure", () => navigateTo("datacenter", null, "nodes")], ["tab.permissions", () => navigateTo("datacenter", null, "permissions")],
      ["tab.containers", () => navigateTo("datacenter", null, "containers")],
    ].map(([k, run]) => ({ group: "cmd.group.go", id: `g:${k}`, label: `${t("inv.datacenter")} › ${t(k)}`, run }));
    return [...res, ...actions.filter((a) => match(a.label)), ...go.filter((g) => match(g.label))];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, nodes, vms, caps.create, caps.admin]);

  useEffect(() => { setIdx(0); }, [q]);
  if (!open) return null;

  const choose = (it) => { onClose(); it.run(); };
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); onClose(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(items.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); if (items[idx]) choose(items[idx]); }
    // The input is the dialog's only focusable element (list options aren't in tab order):
    // trap Tab/Shift+Tab here instead of letting focus escape to the scrim-covered page behind it.
    else if (e.key === "Tab") { e.preventDefault(); }
  };
  let lastGroup = null;

  return (
    <div className="nx-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="nx-palette" role="dialog" aria-modal="true" aria-label={t("find.open")} onKeyDown={onKey}>
        <input ref={inputRef} role="combobox" aria-expanded="true" aria-controls="nx-palette-list" aria-activedescendant={items[idx] ? `nx-opt-${idx}` : undefined} placeholder={t("cmd.placeholder")} value={q} onChange={(e) => setQ(e.target.value)} />
        <ul id="nx-palette-list" role="listbox">
          {items.length === 0 && <li className="nx-muted" style={{ padding: "var(--space-3)" }} role="presentation">{t("cmd.none")}</li>}
          {items.map((it, i) => {
            const header = it.group !== lastGroup ? <li key={`g-${it.group}`} className="nx-group" role="presentation">{t(it.group)}</li> : null;
            lastGroup = it.group;
            return [header, (
              <li key={it.id} id={`nx-opt-${i}`} role="option" aria-selected={i === idx} onMouseMove={() => setIdx(i)} onClick={() => choose(it)}>
                {it.state && <StatusIndicator override={it.state} compact />}
                <span>{it.label}</span>
                {it.hint && <span className="nx-muted nx-mono" style={{ marginLeft: "auto" }}>{it.hint}</span>}
              </li>
            )];
          })}
        </ul>
        <div className="nx-inv-foot">{t("cmd.hint")}</div>
      </div>
    </div>
  );
}
