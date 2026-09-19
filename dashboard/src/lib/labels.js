// Display labels for the backend wire values (French identifiers kept as-is on
// the API; only what the user reads is translated here).
const STATUS = {
  en_cours: "Running",
  termine: "Completed",
  echec: "Failed",
  succes: "Success",
};

const FREQUENCY = {
  quotidien: "daily",
  hebdomadaire: "weekly",
  mensuel: "monthly",
};

const BACKUP_MODE = {
  chaud: "hot",
  froid: "cold",
};

export const statusLabel = (v) => STATUS[v] ?? v;
export const frequencyLabel = (v) => FREQUENCY[v] ?? v;
export const backupModeLabel = (v) => BACKUP_MODE[v] ?? v;
