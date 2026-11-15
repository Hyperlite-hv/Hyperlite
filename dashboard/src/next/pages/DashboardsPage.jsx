import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, Copy, Home as HomeIcon, LayoutDashboard, PencilLine, Plus, Share2, Trash2, X } from "lucide-react";
import { copyDashboard, createDashboard, deleteDashboard, fetchGroups, fetchSavedQueries, updateDashboard } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { promptText } from "../../store/usePromptStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { newTileId, useDashboards } from "../lib/dashboards";
import { Empty, Field, PageHeader, SideDrawer } from "../components/ui";
import { TileBody } from "../components/DashboardTile";

// Dashboards (docs/design/console-2.md, section 2): named lists of tiles, private or shared, drawn with the reader's
// own rights. Tiles are pinned from the metrics explorer, the search page and a VM's availability, or added here.
export function DashboardGrid({ dash, editing, onChange }) {
  const t = useT();
  const tiles = dash.tiles;
  const move = (i, d) => { const next = [...tiles]; [next[i], next[i + d]] = [next[i + d], next[i]]; onChange(next); };
  const resize = (i) => onChange(tiles.map((x, j) => (j === i ? { ...x, size: (x.size % 3) + 1 } : x)));
  const retitle = async (i) => { const v = await promptText({ title: t("dsh.tileTitle"), label: t("dsh.title"), defaultValue: tiles[i].title }); if (v != null) onChange(tiles.map((x, j) => (j === i ? { ...x, title: v.slice(0, 120) } : x))); };
  const remove = (i) => onChange(tiles.filter((_, j) => j !== i));
  if (!tiles.length) return <Empty icon={LayoutDashboard} title={t("dsh.emptyTitle")} text={t("dsh.emptyText")} />;
  return (
    <div className="hc-dash">
      {tiles.map((tile, i) => (
        <article key={tile.id} className="hc-tile" data-size={tile.size} aria-labelledby={`tile-${tile.id}`}>
          <header className="hc-tile-h">
            <h3 id={`tile-${tile.id}`}>{tile.title || t(`dsh.type.${tile.type}`)}</h3>
            {editing && <div className="hc-tile-tools">
              <button type="button" className="hc-ibtn" aria-label={t("dsh.moveBack", { name: tile.title })} disabled={i === 0} onClick={() => move(i, -1)}><ArrowLeft size={14} aria-hidden="true" /></button>
              <button type="button" className="hc-ibtn" aria-label={t("dsh.moveOn", { name: tile.title })} disabled={i === tiles.length - 1} onClick={() => move(i, 1)}><ArrowRight size={14} aria-hidden="true" /></button>
              <button type="button" className="hc-ibtn hc-tile-size" aria-label={t("dsh.resize", { name: tile.title, n: tile.size })} onClick={() => resize(i)}>{tile.size}×</button>
              <button type="button" className="hc-ibtn" aria-label={t("dsh.retitle", { name: tile.title })} onClick={() => retitle(i)}><PencilLine size={14} aria-hidden="true" /></button>
              <button type="button" className="hc-ibtn" aria-label={t("dsh.removeTile", { name: tile.title })} onClick={() => remove(i)}><X size={14} aria-hidden="true" /></button>
            </div>}
          </header>
          <TileBody tile={tile} />
        </article>
      ))}
    </div>
  );
}

export default function DashboardsPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const { list, load, put, drop, home, setHome } = useDashboards();
  const [current, setCurrent] = useState(null);
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [sharing, setSharing] = useState(false);
  useEffect(() => { load(); }, [load]);
  const dash = useMemo(() => (list || []).find((d) => d.id === current) || (list || []).find((d) => d.id === home) || (list || [])[0] || null, [list, current, home]);
  const fail = (e) => pushToast({ kind: "error", title: t("ac.failed"), message: errorMessage(e) });
  const save = async (patch) => { try { put(await updateDashboard(dash.id, patch)); } catch (e) { fail(e); load(); } };
  async function make() {
    const name = await promptText({ title: t("dsh.new"), label: t("dsh.name"), defaultValue: "" });
    if (!name?.trim()) return;
    try { const d = await createDashboard({ name: name.trim(), tiles: [] }); put(d); setCurrent(d.id); setEditing(true); } catch (e) { fail(e); }
  }
  async function rename() {
    const name = await promptText({ title: t("dsh.rename"), label: t("dsh.name"), defaultValue: dash.name });
    if (name?.trim()) save({ name: name.trim() });
  }
  async function copy() { try { const d = await copyDashboard(dash.id); put(d); setCurrent(d.id); pushToast({ kind: "success", title: t("dsh.copied"), message: d.name }); } catch (e) { fail(e); } }
  async function remove() {
    if (!(await confirmAction({ title: t("dsh.deleteTitle", { name: dash.name }), message: t("dsh.deleteMsg"), confirmLabel: t("dsh.delete"), danger: true }))) return;
    try { await deleteDashboard(dash.id); drop(dash.id); setCurrent(null); } catch (e) { fail(e); }
  }
  const actions = (
    <>
      {dash && <button type="button" className="nx-btn nx-btn--sm" aria-pressed={home === dash.id} onClick={() => setHome(home === dash.id ? null : dash.id)}><HomeIcon size={14} aria-hidden="true" /> {home === dash.id ? t("dsh.onHome") : t("dsh.showOnHome")}</button>}
      {dash && <button type="button" className="nx-btn nx-btn--sm" onClick={copy}><Copy size={14} aria-hidden="true" /> {t("dsh.copy")}</button>}
      {dash?.editable && <button type="button" className="nx-btn nx-btn--sm" onClick={() => setSharing(true)}><Share2 size={14} aria-hidden="true" /> {t("dsh.share")}</button>}
      {dash?.editable && <button type="button" className="nx-btn nx-btn--sm" aria-pressed={editing} onClick={() => setEditing(!editing)}><PencilLine size={14} aria-hidden="true" /> {editing ? t("dsh.done") : t("dsh.edit")}</button>}
      <button type="button" className="nx-btn nx-btn--primary nx-btn--sm" onClick={make}><Plus size={14} aria-hidden="true" /> {t("dsh.new")}</button>
    </>
  );
  if (!list) return <><PageHeader title={t("tab.dashboards")} desc={t("dsh.desc")} /><p className="nx-muted">{t("loading")}</p></>;
  return (
    <>
      <PageHeader title={t("tab.dashboards")} desc={t("dsh.desc")} actions={actions} />
      {!dash ? <Empty icon={LayoutDashboard} title={t("dsh.noneTitle")} text={t("dsh.noneText")} /> : (
        <>
          <div className="hc-dash-bar">
            <label className="hc-dash-pick"><span className="nx-sr">{t("dsh.pick")}</span>
              <select className="nx-sel" value={dash.id} onChange={(e) => { setCurrent(e.target.value); setEditing(false); }}>
                {list.map((d) => <option key={d.id} value={d.id}>{d.name}{d.owner ? ` · ${d.shared === "private" ? t("dsh.private") : t("dsh.sharedBy", { who: d.owner })}` : ""}</option>)}
              </select>
            </label>
            {editing && <>
              <button type="button" className="nx-btn nx-btn--sm" onClick={rename}>{t("dsh.rename")}</button>
              <button type="button" className="nx-btn nx-btn--sm" onClick={() => setAdding(true)}><Plus size={14} aria-hidden="true" /> {t("dsh.addTile")}</button>
              <button type="button" className="nx-btn nx-btn--danger nx-btn--sm" onClick={remove}><Trash2 size={14} aria-hidden="true" /> {t("dsh.delete")}</button>
            </>}
          </div>
          <DashboardGrid dash={dash} editing={editing} onChange={(tiles) => { put({ ...dash, tiles }); save({ tiles }); }} />
          <AddTile open={adding} onClose={() => setAdding(false)} onAdd={(tile) => { const tiles = [...dash.tiles, { ...tile, id: newTileId() }]; put({ ...dash, tiles }); save({ tiles }); setAdding(false); }} />
          <ShareDrawer open={sharing} dash={dash} onClose={() => setSharing(false)} onSave={(shared) => { save({ shared }); setSharing(false); }} />
        </>
      )}
    </>
  );
}
DashboardsPage.ownHeader = true;

function AddTile({ open, onClose, onAdd }) {
  const t = useT();
  const [type, setType] = useState("note");
  const [text, setText] = useState("");
  const [queries, setQueries] = useState([]);
  const [queryId, setQueryId] = useState("");
  useEffect(() => { if (open) fetchSavedQueries().then((q) => { setQueries(q || []); setQueryId((q || [])[0]?.id || ""); }).catch(() => setQueries([])); }, [open]);
  const q = queries.find((x) => x.id === queryId);
  const valid = type === "note" ? text.trim() : type === "query" ? !!q : true;
  function add() {
    if (type === "note") onAdd({ type, title: t("dsh.type.note"), size: 1, config: { text: text.trim().slice(0, 2000) } });
    else if (type === "query") onAdd({ type, title: q.name, size: 1, config: { query: q.query, view: q.view, group_by: q.group_by } });
    else onAdd({ type, title: t(`dsh.type.${type}`), size: 1, config: {} });
    setText("");
  }
  return (
    <SideDrawer open={open} title={t("dsh.addTile")} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!valid} onClick={add}>{t("dsh.add")}</button>
    </>}>
      <Field label={t("dsh.tileType")}>{(p) => <select {...p} className="nx-inp" value={type} onChange={(e) => setType(e.target.value)}>
        {["note", "query", "availability", "alerts", "resources"].map((k) => <option key={k} value={k}>{t(`dsh.type.${k}`)}</option>)}
      </select>}</Field>
      <p className="nx-muted">{t(`dsh.typeHelp.${type}`)}</p>
      {type === "note" && <Field label={t("dsh.text")}>{(p) => <textarea {...p} className="nx-inp" rows={5} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />}</Field>}
      {type === "query" && (queries.length ? <Field label={t("dsh.savedQuery")}>{(p) => <select {...p} className="nx-inp" value={queryId} onChange={(e) => setQueryId(e.target.value)}>{queries.map((x) => <option key={x.id} value={x.id}>{x.name} · {x.query}</option>)}</select>}</Field> : <p className="nx-muted">{t("dsh.noQuery")}</p>)}
      <p className="nx-muted">{t("dsh.chartHint")}</p>
    </SideDrawer>
  );
}

function ShareDrawer({ open, dash, onClose, onSave }) {
  const t = useT();
  const [groups, setGroups] = useState([]);
  const [value, setValue] = useState(dash.shared);
  useEffect(() => { if (open) { setValue(dash.shared); fetchGroups().then((g) => setGroups(Array.isArray(g) ? g : g?.items || [])).catch(() => setGroups([])); } }, [open, dash.shared]);
  return (
    <SideDrawer open={open} title={t("dsh.shareTitle", { name: dash.name })} onClose={onClose} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" onClick={() => onSave(value)}>{t("dsh.save")}</button>
    </>}>
      <p className="nx-muted" style={{ marginTop: 0 }}>{t("dsh.shareNote")}</p>
      <fieldset className="nx-fs">
        <legend>{t("dsh.sharedWith")}</legend>
        <label className="hc-radio"><input type="radio" name="share" checked={value === "private"} onChange={() => setValue("private")} /> {t("dsh.private")}</label>
        <label className="hc-radio"><input type="radio" name="share" checked={value === "everyone"} onChange={() => setValue("everyone")} /> {t("dsh.everyone")}</label>
        {groups.map((g) => <label key={g.id} className="hc-radio"><input type="radio" name="share" checked={value === `group:${g.id}`} onChange={() => setValue(`group:${g.id}`)} /> {t("dsh.group", { name: g.name })}</label>)}
      </fieldset>
    </SideDrawer>
  );
}
