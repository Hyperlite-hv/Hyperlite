import GaugeRing from "../../components/GaugeRing";
import { formatUptime, formatMo, formatGo } from "../../utils/format";

// CPU/RAM totale et historique ne sont pas encore exposes par GET /dashboard
// (item 5 de la roadmap Hyperlite -- dashboard avec historique persiste --
// pas encore construit cote backend). En attendant, ce noeud reel affiche ce
// qui est disponible aujourd'hui (stockage, VMs actives/arretees, RAM
// disponible) plutot que de simuler des graphiques CPU/RAM qui ne
// correspondraient a rien de reel.
export default function NodeSummaryTab({ resource: node }) {
  if (!node) return null;

  const diskRatio = node.stockage_total_go != null && node.stockage_utilise_go != null
    ? node.stockage_utilise_go / node.stockage_total_go : null;

  return (
    <div className="space-y-5">
      {!node.reel && (
        <div className="rounded-md border border-accent-orange/40 bg-accent-orange/10 px-3 py-2 text-xs text-accent-orange">
          Noeud fictif -- illustre le multi-node vise par la structure Proxmox. Seul <code>kvm-lab</code> correspond a un vrai serveur Hyperlite aujourd'hui.
        </div>
      )}

      {node.reel && node.cpu_coeurs == null && (
        <div className="rounded-md border border-anthracite-500 bg-anthracite-700/50 px-3 py-2 text-xs text-anthracite-300">
          CPU/RAM total et historique pas encore exposes par le backend (GET /dashboard) -- seuls le stockage et le compte de VMs sont reels ici.
        </div>
      )}

      <div className="card grid grid-cols-1 gap-6 p-5 sm:grid-cols-3">
        <div className="flex flex-col items-center justify-center gap-1 text-center">
          <div className="text-2xl font-semibold text-anthracite-100">{node.vms_actives ?? "--"}</div>
          <div className="text-xs text-anthracite-400">VMs actives</div>
        </div>
        <div className="flex flex-col items-center justify-center gap-1 text-center">
          <div className="text-2xl font-semibold text-anthracite-100">{node.vms_arretees ?? "--"}</div>
          <div className="text-xs text-anthracite-400">VMs arretees</div>
        </div>
        {diskRatio != null ? (
          <GaugeRing label="Stockage" ratio={diskRatio} valueLabel={`${formatGo(node.stockage_utilise_go)} / ${formatGo(node.stockage_total_go)}`} colorClass="text-accent-green" />
        ) : (
          <div className="flex flex-col items-center justify-center gap-1 text-center">
            <div className="text-sm text-anthracite-400">Stockage n/a</div>
          </div>
        )}
      </div>

      <div className="card p-5">
        <h3 className="mb-3 text-sm font-semibold text-anthracite-100">Statut</h3>
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-anthracite-400 text-xs">Uptime</dt><dd className="text-anthracite-100">{formatUptime(node.uptime_s)}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">RAM disponible</dt><dd className="text-anthracite-100">{node.memoire_disponible_mo != null ? formatMo(node.memoire_disponible_mo) : "--"}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Adresse IP</dt><dd className="text-anthracite-100">{node.ip || "--"}</dd></div>
          <div><dt className="text-anthracite-400 text-xs">Version</dt><dd className="text-anthracite-100">{node.version || "--"}</dd></div>
        </dl>
      </div>
    </div>
  );
}
