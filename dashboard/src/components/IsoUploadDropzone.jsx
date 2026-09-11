import { useCallback, useRef, useState } from "react";
import { UploadCloud, FileText } from "lucide-react";
import ProgressBar from "./ProgressBar";
import { useInfraStore } from "../store/useInfraStore";

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} Ko`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} Mo`;
  return `${(bytes / 1024 ** 3).toFixed(2)} Go`;
}

function formatEta(seconds) {
  if (!isFinite(seconds) || seconds <= 0) return "--";
  if (seconds < 60) return `${Math.ceil(seconds)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

// Simule un televersement avec vitesse variable + ETA recalcule en direct.
// A REMPLACER par un vrai XMLHttpRequest avec upload.onprogress (voir
// app/static/app.js: la version vanilla-JS de Hyperlite fait deja ca pour /isos,
// c'est le meme principe ici mais pilote depuis React).
export default function IsoUploadDropzone() {
  const [dragOver, setDragOver] = useState(false);
  const [upload, setUpload] = useState(null); // { file, loaded, speed, etaS, statut }
  const inputRef = useRef(null);
  const addTask = useInfraStore((s) => s.addTask);
  const completeTask = useInfraStore((s) => s.completeTask);
  const updateTaskProgress = useInfraStore((s) => s.updateTaskProgress);

  const startUpload = useCallback((file) => {
    if (!file) return;
    const taskId = addTask({ type: "upload_iso", cible: file.name, node: "kvm-lab" });
    setUpload({ file, loaded: 0, speed: 0, etaS: Infinity, statut: "en_cours" });

    let loaded = 0;
    const total = file.size || 500 * 1024 * 1024;
    const startedAt = Date.now();
    const id = setInterval(() => {
      const speedBps = (8 + Math.random() * 22) * 1024 * 1024; // 8-30 Mo/s simule
      loaded = Math.min(total, loaded + speedBps * 0.4);
      const elapsedS = (Date.now() - startedAt) / 1000;
      const avgSpeed = loaded / elapsedS;
      const etaS = (total - loaded) / avgSpeed;
      const pct = Math.round((loaded / total) * 100);
      updateTaskProgress(taskId, pct);
      setUpload({ file, loaded, speed: avgSpeed, etaS, statut: pct >= 100 ? "termine" : "en_cours" });
      if (loaded >= total) {
        clearInterval(id);
        completeTask(taskId, "termine");
      }
    }, 400);
  }, [addTask, completeTask, updateTaskProgress]);

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
        <p className="text-sm text-anthracite-200">Glissez une image ISO ici, ou cliquez pour parcourir</p>
        <p className="text-xs text-anthracite-400">Simulation locale -- aucun fichier n'est reellement envoye</p>
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
            <span>{upload.statut === "termine" ? "Televersement termine" : `${(upload.speed / 1024 / 1024).toFixed(1)} Mo/s`}</span>
            <span>{upload.statut === "termine" ? "" : `Temps restant estime : ${formatEta(upload.etaS)}`}</span>
          </div>
        </div>
      )}
    </div>
  );
}
