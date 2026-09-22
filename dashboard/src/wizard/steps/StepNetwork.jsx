export default function StepNetwork({ form, patch, networks }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-foreground/80 mb-3">Choose the libvirt network of the VM (matches GET /networks).</p>
      {networks.map((n) => (
        <label key={n.nom} className={`flex items-center gap-3 rounded-md border px-3 py-2.5 cursor-pointer transition-colors duration-150 ${form.network === n.nom ? "border-accent-blue bg-accent-blue/10" : "border-border hover:border-muted-foreground/40"}`}>
          <input type="radio" checked={form.network === n.nom} onChange={() => patch({ network: n.nom })} className="accent-accent-blue" />
          <div>
            <div className="text-sm text-foreground">{n.nom} <span className="text-xs text-muted-foreground">({n.type})</span></div>
            <div className="text-xs text-muted-foreground font-mono">{n.pont} -- {n.reseau ? `${n.reseau.adresse}/${n.reseau.masque}` : "--"}</div>
          </div>
        </label>
      ))}
    </div>
  );
}
