import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AppShell from "./layout/AppShell";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/datacenter" replace />} />
        <Route path="/datacenter" element={<AppShell />} />
        <Route path="/node/:id" element={<AppShell />} />
        <Route path="/vm/:id" element={<AppShell />} />
        <Route path="/container/:id" element={<AppShell />} />
        <Route path="*" element={<Navigate to="/datacenter" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
