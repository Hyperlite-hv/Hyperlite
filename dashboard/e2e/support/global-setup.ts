import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Safety guard: destructive scenarios must never run against a real installation.
export default async function globalSetup() {
  const port = process.env.E2E_PORT ?? "8011";
  const base = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${port}`;
  const host = new URL(base).hostname;
  const isLoopback = ["127.0.0.1", "localhost", "::1"].includes(host);
  if (!isLoopback && process.env.E2E_ALLOW_REMOTE !== "1") {
    throw new Error(
      `Refusing to run end-to-end tests against ${base}: not a loopback address. ` +
        "Set E2E_ALLOW_REMOTE=1 only for a dedicated test installation.",
    );
  }
  if (!process.env.E2E_BASE_URL && !existsSync(fileURLToPath(new URL("../../dist/index.html", import.meta.url)))) {
    throw new Error("dashboard/dist is missing: run `npm run build` first.");
  }
}
