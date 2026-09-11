import { useEffect, useRef, useState } from "react";

// Genere une serie temporelle "random walk" plausible pour simuler des metriques
// temps reel (CPU/RAM/disque/reseau) en l'absence d'historique persiste cote
// backend. A REMPLACER par un fetch vers une future route /vms/{name}/metrics/history
// (ou /nodes/{id}/metrics/history) une fois l'item 5 de la roadmap (dashboard avec
// historique persiste en base) construit cote Hyperlite.
//
// rangeHours: 1 | 6 | 24 -- determine l'espacement des points et la longueur de la fenetre.
export function useSimulatedMetrics(resourceId, rangeHours = 1, baseline = {}) {
  const pointCount = 60;
  const intervalMs = (rangeHours * 3600 * 1000) / pointCount;
  const stateRef = useRef({
    cpu: baseline.cpu ?? 0.3,
    ram: baseline.ram ?? 0.4,
    disk: baseline.disk ?? 0.5,
    netIn: baseline.netIn ?? 200,
    netOut: baseline.netOut ?? 80,
  });
  const [data, setData] = useState(() => genInitial(stateRef.current, pointCount, intervalMs));

  useEffect(() => {
    stateRef.current = {
      cpu: baseline.cpu ?? 0.3, ram: baseline.ram ?? 0.4, disk: baseline.disk ?? 0.5,
      netIn: baseline.netIn ?? 200, netOut: baseline.netOut ?? 80,
    };
    setData(genInitial(stateRef.current, pointCount, intervalMs));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resourceId, rangeHours]);

  useEffect(() => {
    const id = setInterval(() => {
      const next = step(stateRef.current);
      stateRef.current = next;
      setData((prev) => [...prev.slice(1), { t: Date.now(), ...next }]);
    }, 2000);
    return () => clearInterval(id);
  }, [resourceId, rangeHours]);

  const current = data[data.length - 1] || stateRef.current;
  return { data, current };
}

function clamp01(v) {
  return Math.max(0.02, Math.min(0.98, v));
}

function step(prev) {
  return {
    cpu: clamp01(prev.cpu + (Math.random() - 0.5) * 0.12),
    ram: clamp01(prev.ram + (Math.random() - 0.5) * 0.04),
    disk: clamp01(prev.disk + (Math.random() - 0.5) * 0.01),
    netIn: Math.max(0, prev.netIn + (Math.random() - 0.5) * 120),
    netOut: Math.max(0, prev.netOut + (Math.random() - 0.5) * 60),
  };
}

function genInitial(baseline, count, intervalMs) {
  const now = Date.now();
  let cur = { ...baseline };
  const points = [];
  for (let i = count - 1; i >= 0; i -= 1) {
    cur = step(cur);
    points.push({ t: now - i * intervalMs, ...cur });
  }
  return points;
}
