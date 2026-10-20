// Display labels for the backend wire values (French identifiers kept as-is on
// the API; only what the user reads is translated here).
const STATUS = {
  running: "Running",
  succeeded: "Completed",
  failed: "Failed",
  success: "Success",
};

const FREQUENCY = {
  daily: "daily",
  weekly: "weekly",
  monthly: "monthly",
};

const BACKUP_MODE = {
  live: "hot",
  cold: "cold",
};

export const statusLabel = (v) => STATUS[v] ?? v;
export const frequencyLabel = (v) => FREQUENCY[v] ?? v;
export const backupModeLabel = (v) => BACKUP_MODE[v] ?? v;
