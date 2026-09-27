import { useEffect } from "react";
import "../next.css";
import "../refonte.css";
import { useAuthStore } from "../../store/useAuthStore";
import { useLangStore } from "../i18n";
import { useThemeStore } from "../tokens/theme";
import NextLogin from "../NextLogin";

// Frame of the windows opened on their own (VM console, host shell, container terminal): the theme, language and
// session of the main window (same origin), the sign-in screen when needed, a header, and the content full height.
export default function StandaloneWindow({ title, icon: Icon, badge, children }) {
  const lang = useLangStore((s) => s.lang);
  const initTheme = useThemeStore((s) => s.init);
  const status = useAuthStore((s) => s.status);
  const restoreSession = useAuthStore((s) => s.restoreSession);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.ui = "next";
    root.lang = lang;
    const cleanup = initTheme();
    return () => cleanup?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { document.title = title; }, [title]);
  useEffect(() => { restoreSession(); }, [restoreSession]);

  if (status === "anonymous") return <NextLogin />;
  if (status !== "authenticated") return <div className="nx-root nx-console-window" />;
  return (
    <div className="nx-root nx-console-window">
      <header className="nx-console-head">
        {Icon && <Icon size={17} aria-hidden="true" />}
        <h1>{title}</h1>
        {badge}
      </header>
      {children}
    </div>
  );
}
