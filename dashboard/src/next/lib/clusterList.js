// A list the API gives for the whole cluster (GET /tasks, /audit, /backups with cluster=true): { items, unreachable }.
// A plain list (an answer without the cluster shape) is read as it is rather than shown as empty.
export const listOf = (r) => (Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : []);
