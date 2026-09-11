export default function StepNode({ form, patch, nodes }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-anthracite-300 mb-3">Choisissez le noeud qui hebergera la VM.</p>
      {nodes.map((n) => (
        <label key={n.id} className={`flex items-center gap-3 rounded-md border px-3 py-2.5 cursor-pointer ${form.node === n.id ? "border-accent-blue bg-accent-blue/10" : "border-anthracite-600 hover:border-anthracite-500"}`}>
          <input type="radio" name="node" checked={form.node === n.id} onChange={() => patch({ node: n.id })} className="accent-accent-blue" />
          <div>
            <div className="text-sm text-anthracite-100">{n.nom}{!n.reel && <span className="ml-2 text-[10px] text-accent-orange border border-accent-orange/40 rounded px-1 py-0.5">fictif</span>}</div>
            <div className="text-xs text-anthracite-400">{n.cpu_coeurs} coeurs -- {Math.round((1 - n.memoire_utilisee_mo / n.memoire_totale_mo) * 100)}% RAM libre</div>
          </div>
        </label>
      ))}
    </div>
  );
}
