import { useEffect, useRef, useState } from "react";
import { fetchProvisioningStatus } from "../api/client";

// Polls GET /vms/{name}/provisioning (unattended ISO installation in progress:
// Kickstart/autoinstall, see app/core/unattended_install.py) to show an
// indeterminate progress bar while the SSH terminal is not yet reachable. Stops on
// its own as soon as the backend reports the end (provisioning: false).
const POLL_MS = 6000;

export function useProvisioningStatus(vmName, active) {
  const [status, setStatus] = useState(null); // null = not queried yet
  const [justFinished, setJustFinished] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    setStatus(null);
    setJustFinished(false);
    if (!vmName || !active) return undefined;

    let cancelled = false;
    async function poll() {
      try {
        const s = await fetchProvisioningStatus(vmName);
        if (cancelled) return;
        setStatus(s);
        if (s.just_finished) setJustFinished(true);
        if (s.provisioning) {
          timerRef.current = setTimeout(poll, POLL_MS);
        }
      } catch {
        // The VM may have been deleted in the meantime, or the API momentarily
        // unavailable: no big deal, we just stop polling silently.
      }
    }
    poll();

    return () => { cancelled = true; clearTimeout(timerRef.current); };
  }, [vmName, active]);

  return { status, justFinished };
}
