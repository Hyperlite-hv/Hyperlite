import { useEffect, useState } from "react";
import { useT } from "../next/i18n";

// A loading placeholder that never spins forever silently: after a while it says so and
// offers to reload the page (the current tab is kept in the URL).
export default function LoadingState({ label }) {
  const t = useT();
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), 10000);
    return () => clearTimeout(timer);
  }, []);
  return (
    <span role="status">
      {label ?? t("loading.label")}
      {slow && (
        <>
          {" "}{t("loading.slow")}{" "}
          <button type="button" className="underline" onClick={() => window.location.reload()}>{t("loading.reload")}</button>
        </>
      )}
    </span>
  );
}
