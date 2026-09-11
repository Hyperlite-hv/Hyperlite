import { useEffect } from "react";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { useInfraStore } from "../store/useInfraStore";

const PATH_TYPES = { node: "node", vm: "vm" };

// Fait correspondre l'URL (react-router) et la selection dans le store Zustand,
// dans les deux sens : cliquer dans l'arbre change l'URL (partageable, boutons
// precedent/suivant du navigateur fonctionnels), et charger /vm/web-01
// directement selectionne bien cette VM au demarrage.
export function useUrlParamsToSelection() {
  const params = useParams();
  const { pathname } = useLocation();
  const select = useInfraStore((s) => s.select);

  useEffect(() => {
    const type = Object.keys(PATH_TYPES).find((t) => pathname.startsWith(`/${t}/`));
    if (type && params.id) select(type, params.id);
    else if (pathname.startsWith("/datacenter")) select("datacenter", null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, params.id]);
}

export function useSelectionToUrl() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const selection = useInfraStore((s) => s.selection);

  useEffect(() => {
    // "storage" n'a pas de route dediee (CentralPanel l'affiche inline sans
    // onglets) : on laisse l'URL telle quelle plutot que de naviguer vers un
    // chemin que le routeur ne connait pas.
    if (selection.type === "storage") return;
    const target = selection.type === "datacenter" ? "/datacenter" : `/${selection.type}/${encodeURIComponent(selection.id)}`;
    if (target !== pathname) navigate(target, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection]);
}
