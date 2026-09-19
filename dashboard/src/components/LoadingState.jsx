import { useEffect, useState } from "react";

// A loading placeholder that never spins forever silently: after a while it says so and
// offers to reload the page (the current tab is kept in the URL).
export default function LoadingState({ label = "Loading..." }) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), 10000);
    return () => clearTimeout(timer);
  }, []);
  return (
    <span role="status">
      {label}
      {slow && (
        <>
          {" "}This is taking longer than expected.{" "}
          <button type="button" className="underline" onClick={() => window.location.reload()}>Reload the page</button>
        </>
      )}
    </span>
  );
}
