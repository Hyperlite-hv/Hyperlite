import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { useAuthStore } from "./store/useAuthStore";

const NextApp = lazy(() => import("./next/NextApp"));
const NextLogin = lazy(() => import("./next/NextLogin"));
const NextCliLogin = lazy(() => import("./next/NextCliLogin"));
const ConsoleWindow = lazy(() => import("./next/windows/ConsoleWindow"));
const HostShellWindow = lazy(() => import("./next/windows/HostShellWindow"));
const ContainerTerminalWindow = lazy(() => import("./next/windows/ContainerTerminalWindow"));

// The windows opened on their own (VM console, host shell, container terminal) and the approval page of the
// workstation client have their own authentication gate, independent of the main dashboard's.
export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={null}>
        <Routes>
          <Route path="/console/:name" element={<ConsoleWindow />} />
          <Route path="/host-shell" element={<HostShellWindow />} />
          <Route path="/container-terminal/:name" element={<ContainerTerminalWindow />} />
          <Route path="/cli-login" element={<NextCliLogin />} />
          <Route path="/*" element={<MainApp />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}

function MainApp() {
  const status = useAuthStore((s) => s.status);
  const restoreSession = useAuthStore((s) => s.restoreSession);
  useEffect(() => { restoreSession(); }, [restoreSession]);
  if (status === "checking") return null;
  return status === "anonymous" ? <NextLogin /> : <NextApp />;
}
