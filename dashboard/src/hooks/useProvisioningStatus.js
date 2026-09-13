import { useEffect, useRef, useState } from "react";
import { fetchProvisioningStatus } from "../api/client";

// Interroge GET /vms/{name}/provisioning (installation automatisee ISO en
// cours -- Kickstart/autoinstall, voir app/core/unattended_install.py) pour
// afficher une barre de progression indeterminee tant que le terminal SSH
// n'est pas encore joignable. S'arrete de lui-meme des que le backend
// signale la fin (provisioning: false).
const POLL_MS = 6000;

export function useProvisioningStatus(vmName, active) {
  const [status, setStatus] = useState(null); // null = pas encore interroge
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
      } catch (e) {
        // VM peut-etre supprimee entre-temps, ou API momentanement indisponible :
        // pas grave, on arrete juste de poller silencieusement.
      }
    }
    poll();

    return () => { cancelled = true; clearTimeout(timerRef.current); };
  }, [vmName, active]);

  return { status, justFinished };
}
