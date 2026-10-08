import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { detectLang, loadLang } from "./next/i18n";

// The language shown first is loaded before the first render (next/i18n: one file per language); if it cannot be,
// the dashboard still renders, with the keys.
loadLang(detectLang()).catch(() => {}).finally(() => {
  ReactDOM.createRoot(document.getElementById("root")).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
});
