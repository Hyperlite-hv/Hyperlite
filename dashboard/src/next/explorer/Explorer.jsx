import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { useExplorerStore } from "./store";
import { buildServerTree, buildPoolTree, filterTree, flatten, defaultOpen } from "./buildTree";
import Tree from "./Tree";
import Menu, { MenuItem } from "../components/Menu";
import { Skeleton } from "../components/States";
import { useFreshness, refreshInventory } from "../lib/inventory";
import { capabilities, vmActionState } from "../lib/capabilities";
import { relativeTime } from "../lib/format";
import { selectionToPath } from "../lib/urls";
import { useVmActions } from "../lib/vmActions";

function selectionMatches(row, sel) {
  return !!row.selection && row.selection.type === sel.type && (row.selection.id ?? null) === (sel.id ?? null);
}

export default function Explorer({ open, onNavigate, onCreateVm }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const { nodes, vms, storagePools, networks, loading, error, selection } = useInfraStore(useShallow((s) => ({
    nodes: s.nodes, vms: s.vms, storagePools: s.storagePools, networks: s.networks, loading: s.loading, error: s.error, selection: s.selection,
  })));
  const role = useAuthStore((s) => s.role);
  const caps = capabilities(role);
  const { containers, pools, poolsState, updatedAt, failing } = useFreshness(useShallow((s) => ({
    containers: s.containers, pools: s.pools, poolsState: s.poolsState, updatedAt: s.updatedAt, failing: s.failing,
  })));
  const { mode, setMode, toggled, setOpen, query, setQuery, density, pushRecent } = useExplorerStore(useShallow((s) => ({
    mode: s.mode, setMode: s.setMode, toggled: s.toggled, setOpen: s.setOpen, query: s.query, setQuery: s.setQuery, density: s.density, pushRecent: s.pushRecent,
  })));
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const select = useInfraStore((s) => s.select);
  const pushToast = useInfraStore((s) => s.pushToast);

  const [focusKey, setFocusKey] = useState("dc");
  const [menu, setMenu] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  const searchRef = useRef(null);
  const menuReturn = useRef(null);
  const [draft, setDraft] = useState(query);

  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 10000); return () => clearInterval(id); }, []);
  useEffect(() => { const h = setTimeout(() => setQuery(draft), 120); return () => clearTimeout(h); }, [draft, setQuery]);
  useEffect(() => {
    const focus = () => { searchRef.current?.focus(); searchRef.current?.select(); };
    window.addEventListener("nx:focus-search", focus);
    return () => window.removeEventListener("nx:focus-search", focus);
  }, []);

  const poolBlocked = mode === "pool" && pools == null;
  const built = useMemo(() => {
    const data = { nodes, vms, containers, storagePools, networks, pools };
    if (mode === "pool") return pools == null ? [] : buildPoolTree(data, t);
    return buildServerTree(data, t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, vms, containers, storagePools, networks, pools, mode, lang]);

  const { rows, matches } = useMemo(() => filterTree(built, query), [built, query]);
  const searching = query.trim().length > 0;
  const isOpen = useCallback((row, level) => (searching ? true : (toggled[row.key] ?? defaultOpen(row, level))), [searching, toggled]);
  const flat = useMemo(() => flatten(rows, isOpen), [rows, isOpen]);
  const selectionKey = useMemo(() => flat.find((f) => selectionMatches(f.row, selection))?.row.key ?? null, [flat, selection]);

  function activate(row) {
    if (row.selection) {
      select(row.selection.type, row.selection.id);
      if (row.selection.type === "vm") pushRecent(row.selection.id);
    } else if (row.open) {
      navigateTo(row.open.type, row.open.id, row.open.tab);
    } else {
      setOpen(row.key, !isOpen(row, 1));
      return;
    }
    onNavigate?.();
  }

  const vmActions = useVmActions();
  const vmAction = (row, action) => vmActions.run(row.resource, action);

  function copy(text) { navigator.clipboard?.writeText(text).then(() => pushToast({ kind: "success", title: t("action.copied") })); }

  const total = matches;
  const resultsText = searching ? (total === 1 ? t("find.result1") : t("find.results", { n: total })) : "";
  const stale = failing && updatedAt;
  const nothingLoaded = loading && nodes.length === 0;

  return (
    <aside id="nx-inventory" className="nx-inv" data-open={open ? "true" : "false"} aria-label={t("inv.title")}>
      <div className="nx-inv-head">
        <span className="nx-inv-title">{t("inv.title")}</span>
        <div className="nx-seg" role="group" aria-label={t("inv.mode")}>
          <button type="button" aria-pressed={mode === "server"} onClick={() => setMode("server")}>{t("inv.server")}</button>
          <button type="button" aria-pressed={mode === "pool"} onClick={() => setMode("pool")}>{t("inv.pool")}</button>
        </div>
      </div>

      <div className="nx-search" role="search">
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="m11 11 3.5 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
        <input
          ref={searchRef}
          type="search"
          role="searchbox"
          aria-label={t("find.placeholder")}
          aria-controls="nx-inventory-tree"
          placeholder={t("find.placeholder")}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") { e.preventDefault(); if (draft) { setDraft(""); setQuery(""); } else e.currentTarget.blur(); }
            else if (e.key === "Enter") { const first = flat.find((f) => f.row.kind === "vm" || f.row.kind === "node"); if (first) activate(first.row); }
            else if (e.key === "ArrowDown") { e.preventDefault(); document.querySelector('#nx-inventory-tree [tabindex="0"]')?.focus(); }
          }}
        />
      </div>
      <div className="nx-results" role="status" aria-live="polite">{resultsText}</div>

      {nothingLoaded ? (
        <div style={{ padding: "var(--space-2) var(--space-3)", display: "grid", gap: 10 }} aria-busy="true">
          <span className="nx-sr">{t("inv.loading")}</span>
          {[70, 55, 85, 60, 75].map((w, i) => <Skeleton key={i} width={`${w}%`} />)}
        </div>
      ) : error && nodes.length === 0 ? (
        <div className="nx-inv-empty" role="alert">
          <strong>{t("err.title")}</strong>
          <span className="nx-mono" style={{ overflowWrap: "anywhere" }}>{error}</span>
          <button type="button" className="nx-btn" onClick={() => refreshInventory({ initial: true }).catch(() => {})}>{t("inv.retry")}</button>
        </div>
      ) : poolBlocked ? (
        <div className="nx-inv-empty" role="status">
          <span>{poolsState === "forbidden" ? t("inv.poolsForbidden") : t("inv.poolsFailed")}</span>
          <button type="button" className="nx-btn" onClick={() => setMode("server")}>{t("inv.server")}</button>
        </div>
      ) : searching && total === 0 ? (
        <div className="nx-inv-empty" role="status">
          <span>{t("find.none", { q: query.trim() })}</span>
          <span className="nx-muted">{t("find.hint")}</span>
          <button type="button" className="nx-btn" onClick={() => { setDraft(""); setQuery(""); searchRef.current?.focus(); }}>{t("find.clear")}</button>
        </div>
      ) : (
        <div id="nx-inventory-tree" style={{ display: "contents" }}>
          <Tree
            flat={flat} selectionKey={selectionKey} focusKey={focusKey} setFocusKey={setFocusKey}
            onActivate={activate} onToggle={(row, o) => setOpen(row.key, o)} query={query} density={density} label={t("inv.tree")}
            onContext={(row, pos) => { if (row.kind === "vm" || row.kind === "node") { menuReturn.current = pos.el || null; setMenu({ row, pos }); } }}
          />
        </div>
      )}

      {!nothingLoaded && !error && vms.length === 0 && !searching && mode === "server" && (
        <div className="nx-inv-empty" role="status">
          <span>{t("inv.noVms")}</span>
          {caps.create && <button type="button" className="nx-btn nx-btn--primary" onClick={onCreateVm}>{t("action.createVm")}</button>}
        </div>
      )}

      <div className="nx-inv-foot" role="status">
        {mode === "pool" && !poolBlocked ? <div>{t("inv.poolNote")}</div> : null}
        {stale ? <span className="nx-tone-warning">▲ {t("inv.stale")} · {t("inv.updated", { t: relativeTime(updatedAt, lang, now) })}</span>
          : updatedAt ? t("inv.updated", { t: relativeTime(updatedAt, lang, now) }) : ""}
      </div>

      <Menu open={!!menu} onClose={() => setMenu(null)} label={menu?.row.label} returnFocusRef={menuReturn} style={menu ? { position: "fixed", left: Math.min(menu.pos.x, window.innerWidth - 220), top: Math.min(menu.pos.y, window.innerHeight - 260) } : undefined}>
        {menu && menu.row.kind === "vm" && (() => {
          const vm = menu.row.resource;
          const st = (a) => vmActionState(a, vm, caps);
          const item = (a, label) => { const s = st(a); return <MenuItem key={a} disabled={!s.enabled} reason={s.reason ? t(s.reason) : undefined} onSelect={() => { setMenu(null); if (a === "console") vmActions.openConsole(vm); else vmAction(menu.row, a); }}>{label}</MenuItem>; };
          return (
            <>
              <MenuItem onSelect={() => { setMenu(null); activate(menu.row); }}>{t("menu.open")}</MenuItem>
              {item("start", t("menu.start"))}{item("stop", t("menu.stop"))}{item("restart", t("menu.restart"))}{item("console", t("menu.console"))}
              <hr />
              {vm.ip && <MenuItem onSelect={() => { setMenu(null); copy(vm.ip); }}>{t("menu.copyIp")}</MenuItem>}
              <MenuItem onSelect={() => { setMenu(null); copy(`${window.location.origin}${selectionToPath({ type: "vm", id: vm.nom })}`); }}>{t("menu.copyLink")}</MenuItem>
            </>
          );
        })()}
        {menu && menu.row.kind === "node" && (
          <>
            <MenuItem onSelect={() => { setMenu(null); activate(menu.row); }}>{t("menu.open")}</MenuItem>
            <MenuItem onSelect={() => { setMenu(null); copy(`${window.location.origin}${selectionToPath({ type: "node", id: menu.row.selection.id })}`); }}>{t("menu.copyLink")}</MenuItem>
          </>
        )}
      </Menu>
    </aside>
  );
}
