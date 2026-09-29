import { useCallback, useEffect, useState } from "react";
import { Pencil, Tag } from "lucide-react";
import { fetchMeta, saveMeta } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { TAG_RE, parseTags, useMetaStore } from "../lib/meta";
import { InlineError } from "./States";
import { Card, Loading } from "./ui";

const MAX_NOTES = 20000;

// Tags as small chips; a click filters the VM list by that tag when onTag is given.
export function TagChips({ tags, onTag }) {
  if (!tags?.length) return null;
  return (
    <span className="nx-tags">
      {tags.map((tg) => (onTag
        ? <button key={tg} type="button" className="nx-tag" onClick={(e) => { e.stopPropagation(); onTag(tg); }}>{tg}</button>
        : <span key={tg} className="nx-tag">{tg}</span>))}
    </span>
  );
}

// Notes and tags of a VM, a container or a node. The notes are shown as plain text (never as HTML), keeping the
// line breaks the author typed.
export default function NotesCard({ kind, name, node = null, canEdit }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const reloadTags = useMetaStore((s) => s.load);
  const [meta, setMeta] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);
  const [notes, setNotes] = useState("");
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setMeta(await fetchMeta(kind, name, node)); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, [kind, name, node]);
  useEffect(() => { setMeta(null); setEditing(false); load(); }, [load]);

  const typed = parseTags(tags);
  const badTag = typed.find((tg) => !TAG_RE.test(tg));
  const tooMany = typed.length > 16;
  const tooLong = notes.length > MAX_NOTES;

  function edit() { setNotes(meta.notes); setTags(meta.tags.join(", ")); setEditing(true); }
  async function save() {
    if (badTag || tooMany || tooLong) return;
    setBusy(true);
    try {
      const saved = await saveMeta(kind, name, { notes, tags: typed }, node);
      setMeta(saved); setEditing(false);
      pushToast({ kind: "success", title: t("notes.saved"), message: name });
      reloadTags(true);
    } catch (e) { pushToast({ kind: "error", title: t("notes.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }

  const actions = canEdit && meta && !editing
    ? <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={edit}><Pencil size={14} aria-hidden="true" />{t("notes.edit")}</button>
    : null;
  return (
    <Card title={t("notes.title")} actions={actions}>
      {error && !meta ? <InlineError message={error} onRetry={load} /> : !meta ? <Loading style={{ margin: 0 }} /> : editing ? (
        <div className="nx-stack" style={{ gap: "var(--space-3)" }}>
          <label className="nx-dialog-field">{t("notes.notes")}
            <textarea className="nx-inp nx-notes-input" rows={6} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={t("notes.placeholder")} aria-invalid={tooLong || undefined} />
          </label>
          {tooLong && <p className="nx-hint nx-hint--error" role="alert" style={{ margin: 0 }}>{t("notes.tooLong", { n: MAX_NOTES })}</p>}
          <label className="nx-dialog-field">{t("notes.tags")}
            <input className="nx-inp nx-mono" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="prod, db, client-x" aria-invalid={Boolean(badTag || tooMany) || undefined} aria-describedby="nx-tags-hint" />
          </label>
          <p id="nx-tags-hint" className={`nx-hint${badTag || tooMany ? " nx-hint--error" : ""}`} style={{ margin: 0 }}>
            {badTag ? t("notes.badTag", { tag: badTag }) : tooMany ? t("notes.tooManyTags") : t("notes.tagsHelp")}
          </p>
          <div className="nx-fa">
            <button type="button" className="nx-btn" onClick={() => setEditing(false)} disabled={busy}>{t("action.cancel")}</button>
            <button type="button" className="nx-btn nx-btn--primary" onClick={save} disabled={busy || Boolean(badTag) || tooMany || tooLong}>{t("notes.save")}</button>
          </div>
        </div>
      ) : (
        <>
          {meta.tags.length > 0 && <div style={{ marginBottom: "var(--space-3)" }}><Tag size={14} aria-hidden="true" className="nx-muted" style={{ marginRight: "var(--space-2)", verticalAlign: "-2px" }} /><TagChips tags={meta.tags} /></div>}
          {meta.notes.trim() ? <p className="nx-notes">{meta.notes}</p> : <p className="nx-muted" style={{ margin: 0 }}>{canEdit ? t("notes.emptyEdit") : t("notes.empty")}</p>}
        </>
      )}
    </Card>
  );
}

