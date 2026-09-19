import { useEffect, useRef } from "react";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import { useInfraStore } from "../store/useInfraStore";

const PATH_TYPES = { node: "node", vm: "vm" };

// Maps the URL (react-router) to the selection in the Zustand store, in both
// directions: clicking in the tree changes the URL (shareable, browser
// back/forward buttons work), and loading /vm/web-01 directly selects that VM at
// startup.
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
  // On the first render, `selection` still holds the store's default value
  // ("datacenter") while useUrlParamsToSelection() hydrates from the URL (a Zustand
  // set() does not re-render within the same effects flush). Without this guard,
  // loading /vm/demo-vm directly briefly overwrote the URL with "/datacenter"
  // before correcting itself: a real visible flash, not just a theoretical one
  // (reproduced while testing).
  const skipNext = useRef(true);

  useEffect(() => {
    if (skipNext.current) { skipNext.current = false; return; }
    // "storage" has no dedicated route (CentralPanel shows it inline without tabs):
    // we leave the URL as it is rather than navigating to a path the router does not
    // know.
    if (selection.type === "storage") return;
    const target = selection.type === "datacenter" ? "/datacenter" : `/${selection.type}/${encodeURIComponent(selection.id)}`;
    if (target !== pathname) navigate(target, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection]);
}
