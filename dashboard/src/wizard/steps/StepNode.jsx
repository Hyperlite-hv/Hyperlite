export default function StepNode({ form, patch, nodes }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-foreground/80 mb-3">Choose the node that will host the VM.</p>
      {form.node && form.node !== "local" && (
        <p role="note" className="mb-2 rounded-md border border-status-warning/40 bg-status-warning/10 px-3 py-2 text-xs text-status-warning">
          Heads up: VM creation currently runs on the local host whatever node is selected here; the API does not accept a target node yet.
        </p>
      )}
      {nodes.map((n) => (
        <label key={n.id} className={`flex items-center gap-3 rounded-md border px-3 py-2.5 cursor-pointer transition-colors duration-150 ${form.node === n.id ? "border-accent-blue bg-accent-blue/10" : "border-border hover:border-muted-foreground/40"}`}>
          <input type="radio" name="node" checked={form.node === n.id} onChange={() => patch({ node: n.id })} className="accent-accent-blue" />
          <div>
            <div className="text-sm text-foreground">{n.nom}</div>
            <div className="text-xs text-muted-foreground">
              {n.vms_actives ?? 0} running VM(s)
              {n.memoire_disponible_mo != null ? ` -- ${Math.round(n.memoire_disponible_mo)} MB of RAM available` : ""}
            </div>
          </div>
        </label>
      ))}
    </div>
  );
}
