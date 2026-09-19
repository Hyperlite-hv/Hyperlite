import { useCallback, useRef, useState } from "react";
import { UploadCloud, FileText } from "lucide-react";
import ProgressBar from "./ProgressBar";
import { getAuthToken } from "../api/client";
import { useInfraStore } from "../store/useInfraStore";

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} Ko`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function formatEta(seconds) {
  if (!isFinite(seconds) || seconds <= 0) return "--";
  if (seconds < 60) return `${Math.ceil(seconds)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

// Real upload through XMLHttpRequest (the only API with a reliable progress event
// on upload) to POST /isos.
export default function IsoUploadDropzone({ onDone }) {
  const [dragOver, setDragOver] = useState(false);
  const [upload, setUpload] = useState(null); // { file, loaded, speed, etaS, statut }
  const inputRef = useRef(null);
  const addTask = useInfraStore((s) => s.addTask);
  const updateTaskProgress = useInfraStore((s) => s.updateTaskProgress);
  const completeTask = useInfraStore((s) => s.completeTask);

  const startUpload = useCallback((file) => {
    if (!file) return;
    const taskId = addTask({ type: "upload_iso", cible: file.name, node: "local" });
    setUpload({ file, loaded: 0, speed: 0, etaS: Infinity, statut: "en_cours" });

    const fd = new FormData();
    fd.append("file", file);
    const startedAt = Date.now();
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/isos");
    const t = getAuthToken();
    if (t) xhr.setRequestHeader("Authorization", `Bearer ${t}`);
    xhr.upload.addEventListener("progress", (ev) => {
      if (!ev.lengthComputable) return;
      const elapsedS = (Date.now() - startedAt) / 1000;
      const speed = ev.loaded / Math.max(elapsedS, 0.1);
      const etaS = (ev.total - ev.loaded) / Math.max(speed, 1);
      const pct = Math.round((ev.loaded / ev.total) * 100);
      updateTaskProgress(taskId, pct);
      setUpload({ file, loaded: ev.loaded, speed, etaS, statut: pct >= 100 ? "termine" : "en_cours" });
    });
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        completeTask(taskId, "termine");
        setUpload((u) => (u ? { ...u, statut: "termine" } : u));
        onDone?.();
      } else {
        let msg = `HTTP error ${xhr.status}`;
        try { msg = JSON.parse(xhr.responseText).detail || msg; } catch { /* ignore */ }
        completeTask(taskId, "echec", msg);
      }
    });
    xhr.addEventListener("error", () => completeTask(taskId, "echec", "Network error during upload"));
    xhr.send(fd);
  }, [addTask, updateTaskProgress, completeTask, onDone]);

  const onDrop = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) startUpload(file);
  }, [startUpload]);

  return (
    <div>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-8 cursor-pointer transition-colors ${
          dragOver ? "border-accent-blue bg-accent-blue/5" : "border-anthracite-500 hover:border-anthracite-400"
        }`}
      >
        <UploadCloud size={28} className="text-anthracite-300" />
        <p className="text-sm text-anthracite-200">Drop an ISO image here, or click to browse</p>
        <input
          ref={inputRef}
          type="file"
          accept=".iso"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && startUpload(e.target.files[0])}
        />
      </div>

      {upload && (
        <div className="mt-4 card p-3">
          <div className="flex items-center gap-2 text-sm text-anthracite-100">
            <FileText size={15} className="text-anthracite-300 shrink-0" />
            <span className="truncate">{upload.file.name}</span>
            <span className="ml-auto text-xs text-anthracite-400 shrink-0">{formatBytes(upload.loaded)} / {formatBytes(upload.file.size)}</span>
          </div>
          <div className="mt-2">
            <ProgressBar value={(upload.loaded / (upload.file.size || 1)) * 100} statut={upload.statut} />
          </div>
          <div className="mt-1.5 flex justify-between text-xs text-anthracite-400">
            <span>{upload.statut === "termine" ? "Upload complete" : `${(upload.speed / 1024 / 1024).toFixed(1)} MB/s`}</span>
            <span>{upload.statut === "termine" ? "" : `Estimated time remaining: ${formatEta(upload.etaS)}`}</span>
          </div>
        </div>
      )}
    </div>
  );
}
