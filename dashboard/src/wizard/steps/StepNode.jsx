export default function StepNode({ form, patch, nodes }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-anthracite-300 mb-3">Choisissez le nœud qui hébergera la VM.</p>
      {nodes.map((n) => (
        <label key={n.id} className={`flex items-center gap-3 rounded-md border px-3 py-2.5 cursor-pointer ${form.node === n.id ? "border-accent-blue bg-accent-blue/10" : "border-anthracite-600 hover:border-anthracite-500"}`}>
          <input type="radio" name="node" checked={form.node === n.id} onChange={() => patch({ node: n.id })} className="accent-accent-blue" />
          <div>
            <div className="text-sm text-anthracite-100">{n.nom}</div>
            <div className="text-xs text-anthracite-400">
              {n.vms_actives ?? 0} VM active(s)
              {n.memoire_disponible_mo != null ? ` -- ${Math.round(n.memoire_disponible_mo)} Mo de RAM disponible` : ""}
            </div>
          </div>
        </label>
      ))}
    </div>
  );
}
