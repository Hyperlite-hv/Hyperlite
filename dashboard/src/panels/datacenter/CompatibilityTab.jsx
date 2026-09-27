import { useEffect, useMemo, useState } from "react";
import { useInfraStore } from "../../store/useInfraStore";
import { fetchNodeCapabilitiesById } from "../../api/client";
import { compareNodes, NA } from "../../lib/capabilitiesView";
import DeploymentProfileCard from "../../components/DeploymentProfileCard";
import { Card } from "@/components/ui/card";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

// Node comparison table: highlights what DIFFERS between machines, a prerequisite
// to a migration or to adding a node. The "informational" rows (RAM, CPU model...)
// always differ from one machine to another and are reported separately.
export default function CompatibilityTab() {
  const nodes = useInfraStore((s) => s.nodes);
  const [profiles, setProfiles] = useState({});
  const [errors, setErrors] = useState({});
  const [onlyDiff, setOnlyDiff] = useState(true);

  // Keyed on the node ids, not on the `nodes` array: the store replaces that array on every 6 s refresh,
  // which used to reset the table and re-request every node's capabilities each time.
  const nodeKey = nodes.map((n) => n.id).join("|");
  useEffect(() => {
    setProfiles({});
    setErrors({});
    nodeKey.split("|").filter(Boolean).forEach((id) => {
      fetchNodeCapabilitiesById(id)
        .then((p) => setProfiles((prev) => ({ ...prev, [id]: p })))
        .catch((e) => setErrors((prev) => ({ ...prev, [id]: e.message })));
    });
  }, [nodeKey]);

  const loaded = useMemo(() => nodes.filter((n) => profiles[n.id]), [nodes, profiles]);
  const rows = useMemo(() => compareNodes(Object.fromEntries(loaded.map((n) => [n.id, profiles[n.id]]))), [loaded, profiles]);
  const shown = onlyDiff && loaded.length > 1 ? rows.filter((r) => r.differe) : rows;
  const blocking = rows.filter((r) => r.differe && !r.informatif);

  return (
    <div className="space-y-4">
      <DeploymentProfileCard />
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-foreground/80">
          Comparison of the capabilities detected on each node of the cluster.
          {loaded.length === 1 && " Only one node: add another (Nodes tab) to see the differences."}
        </p>
        <Label className="flex items-center gap-2 text-sm text-foreground/80">
          <Checkbox checked={onlyDiff} onCheckedChange={(v) => setOnlyDiff(!!v)} />
          Differences only
        </Label>
      </div>

      {Object.entries(errors).map(([id, msg]) => (
        <Card key={id} className="px-4 py-3 text-sm text-status-error">Node {id} : {msg}</Card>
      ))}

      {loaded.length > 1 && (
        <Card className={`px-4 py-3 text-sm ${blocking.length ? "text-status-warning" : "text-status-running"}`}>
          {blocking.length
            ? `${blocking.length} capability difference(s) between nodes that may affect migration or HA: ${blocking.map((r) => r.label).join(", ")}.`
            : "No functional capability differences between the nodes."}
        </Card>
      )}

      <Card className="p-0 overflow-x-auto" tabIndex={0} role="region" aria-label="Node comparison">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Capability</TableHead>
              {loaded.map((n) => <TableHead key={n.id}>{n.nom}</TableHead>)}
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.length === 0 && (
              <TableRow><TableCell className="text-muted-foreground" colSpan={loaded.length + 1}>
                {loaded.length === 0 ? "Detection in progress..." : "No differences."}
              </TableCell></TableRow>
            )}
            {shown.map((r) => (
              <TableRow key={r.key} className={r.differe && !r.informatif ? "bg-status-warning/10" : ""}>
                <TableCell className="text-foreground/80">
                  {r.section} : {r.label}{r.differe && r.informatif ? " (informational)" : ""}
                </TableCell>
                {loaded.map((n) => (
                  <TableCell key={n.id} className={`font-mono ${r.values[n.id] === NA ? "text-muted-foreground" : "text-foreground"}`}>
                    {String(r.values[n.id])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
