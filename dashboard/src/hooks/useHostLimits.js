import { useEffect, useState } from "react";
import { fetchHostLimits } from "../api/client";

// Limites de ressources derivees de l'hote (GET /host/limits). En cas
// d'echec (ex. hote injoignable), renvoie null : les champs restent sans
// borne haute cote UI et c'est la validation backend, precise, qui tranche
// -- degradation controlee plutot qu'un formulaire bloque.
let cached = null;

export function useHostLimits() {
  const [limits, setLimits] = useState(cached);
  useEffect(() => {
    let alive = true;
    fetchHostLimits()
      .then((l) => { cached = l; if (alive) setLimits(l); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  return limits;
}
