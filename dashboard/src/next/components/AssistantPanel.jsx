import { useEffect, useRef, useState } from "react";
import { MessageCircleQuestion, Send, Trash2 } from "lucide-react";
import { askAssistant, fetchAssistantStatus } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { SideDrawer } from "./ui";

// The assistant (docs/design/console-2.md, section 10): questions in plain words, answered from Hyperlite's data read
// with this account's own rights. It only reads; the links it gives open a VM, a node or a page.
const KEY = "hl.assistant.chat";
const load = () => { try { return JSON.parse(sessionStorage.getItem(KEY)) || []; } catch { return []; } };
const save = (m) => { try { sessionStorage.setItem(KEY, JSON.stringify(m.slice(-40))); } catch { /* this page only */ } };

// The answer as paragraphs and lists, its hl: links as buttons; anything else stays plain text (never HTML).
function Answer({ text, onLink }) {
  const parts = (line) => {
    const out = [];
    let rest = line;
    const re = /\[([^\]]{1,120})\]\((hl:[a-z]+\/[^)\s]{1,120})\)|\*\*([^*]{1,200})\*\*|`([^`]{1,200})`/;
    let m;
    let i = 0;
    while ((m = re.exec(rest))) {
      if (m.index) out.push(rest.slice(0, m.index));
      if (m[1]) out.push(<button key={i++} type="button" className="nx-lnk" onClick={() => onLink(m[2])}>{m[1]}</button>);
      else if (m[3]) out.push(<b key={i++}>{m[3]}</b>);
      else out.push(<code key={i++} className="nx-mono">{m[4]}</code>);
      rest = rest.slice(m.index + m[0].length);
    }
    if (rest) out.push(rest);
    return out;
  };
  const blocks = [];
  let list = null;
  text.split("\n").forEach((line, n) => {
    const item = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (item) { (list ||= []).push(<li key={n}>{parts(item[1])}</li>); return; }
    if (list) { blocks.push(<ul key={`l${n}`}>{list}</ul>); list = null; }
    const h = line.match(/^#{1,4}\s+(.*)$/);
    if (h) blocks.push(<p key={n}><b>{parts(h[1])}</b></p>);
    else if (line.trim()) blocks.push(<p key={n}>{parts(line)}</p>);
  });
  if (list) blocks.push(<ul key="lend">{list}</ul>);
  return <div className="hc-as-answer">{blocks}</div>;
}

export function AssistantPanel({ open, onClose }) {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const [messages, setMessages] = useState(load);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef(null);
  useEffect(() => { save(messages); end.current?.scrollIntoView({ block: "end" }); }, [messages]);
  const onLink = (href) => {
    const [kind, ...rest] = href.slice(3).split("/");
    const target = decodeURIComponent(rest.join("/"));
    if (kind === "vm") navigateTo("vm", target, "summary");
    else if (kind === "node") navigateTo("node", target, "summary");
    else if (kind === "page") navigateTo("datacenter", null, target);
    onClose();
  };
  async function send(e) {
    e?.preventDefault();
    const question = q.trim();
    if (!question || busy) return;
    const history = messages.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.content }));
    setMessages((m) => [...m, { role: "user", content: question }]);
    setQ(""); setBusy(true);
    try {
      const r = await askAssistant(question, history);
      setMessages((m) => [...m, { role: "assistant", content: r.answer, tools: r.tools }]);
    } catch (err) { setMessages((m) => [...m, { role: "assistant", content: errorMessage(err), error: true }]); }
    setBusy(false);
  }
  return (
    <SideDrawer open={open} title={t("as2.title")} onClose={onClose} footer={
      <form className="hc-as-ask" onSubmit={send}>
        <textarea className="nx-inp" rows={2} maxLength={4000} value={q} placeholder={t("as2.ph")} aria-label={t("as2.ph")}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) send(e); }} />
        <button type="submit" className="nx-btn nx-btn--primary nx-btn--icon" aria-label={t("as2.send")} disabled={busy || !q.trim()}><Send size={15} aria-hidden="true" /></button>
      </form>
    }>
      <p className="nx-muted" style={{ marginTop: 0 }}>{t("as2.note")}</p>
      {messages.length > 0 && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" onClick={() => setMessages([])}><Trash2 size={14} aria-hidden="true" /> {t("as2.clear")}</button>}
      <ol className="hc-as-log" aria-live="polite">
        {!messages.length && ["as2.ex1", "as2.ex2", "as2.ex3"].map((k) => <li key={k}><button type="button" className="nx-lnk" onClick={() => setQ(t(k))}>{t(k)}</button></li>)}
        {messages.map((m, i) => (
          <li key={i} className="hc-as-msg" data-role={m.role} data-error={m.error || undefined}>
            {m.role === "user" ? <p>{m.content}</p> : <Answer text={m.content} onLink={onLink} />}
            {m.tools?.length > 0 && <small className="nx-muted">{t("as2.read", { tools: [...new Set(m.tools)].join(", ") })}</small>}
          </li>
        ))}
        {busy && <li className="hc-as-msg" data-role="assistant"><p className="nx-muted">{t("as2.thinking")}</p></li>}
      </ol>
      <div ref={end} />
    </SideDrawer>
  );
}

// The top bar's button: shown only when an administrator turned the assistant on.
export function AssistantButton() {
  const t = useT();
  const [on, setOn] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => { fetchAssistantStatus().then((s) => setOn(!!s.enabled)).catch(() => setOn(false)); }, []);
  if (!on) return null;
  return (
    <>
      <button type="button" className="hc-hb" onClick={() => setOpen(true)} aria-label={t("as2.open")} title={t("as2.open")}><MessageCircleQuestion size={18} aria-hidden="true" /></button>
      <AssistantPanel open={open} onClose={() => setOpen(false)} />
    </>
  );
}
