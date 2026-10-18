import { useCallback, useRef, useState } from "react";
import { UploadCloud, HardDrive } from "lucide-react";
import ProgressBar from "./ProgressBar";
import { useT, useLangStore } from "../next/i18n";
import { formatSizeMb, formatRate } from "../next/lib/format";
import { getAuthToken } from "../api/client";
import { useInfraStore } from "../store/useInfraStore";
import { Card } from "@/components/ui/card";

function formatEta(seconds) {
  if (!isFinite(seconds) || seconds <= 0) return "--";
  if (seconds < 60) return `${Math.ceil(seconds)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

// The same mechanism as IsoUploadDropzone.jsx (XMLHttpRequest + upload progress
// event) but to POST /vm-disks: disk files (qcow2/raw/vmdk/vdi/vhd) meant for VM
// import, distinct from ISOs (installation media).
export default function VmDiskUploadDropzone({ onDone, labels = {} }) {
  const [dragOver, setDragOver] = useState(false);
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  // Sizes and speeds in the units of the language shown (Ko/Mo/Go in French), like the rest of the dashboard.
  const formatBytes = (bytes) => (bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} ${lang === "fr" ? "Ko" : "KB"}` : formatSizeMb(bytes / 1024 / 1024, lang));
  const [upload, setUpload] = useState(null);
  const inputRef = useRef(null);
  const addTask = useInfraStore((s) => s.addTask);
  const updateTaskProgress = useInfraStore((s) => s.updateTaskProgress);
  const completeTask = useInfraStore((s) => s.completeTask);

  const startUpload = useCallback((file) => {
    if (!file) return;
    const taskId = addTask({ type: "upload_vm_disk", target: file.name, node: "local" });
    setUpload({ file, loaded: 0, speed: 0, etaS: Infinity, status: "running" });

    const fd = new FormData();
    fd.append("file", file);
    const startedAt = Date.now();
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/vm-disks");
    const token = getAuthToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.addEventListener("progress", (ev) => {
      if (!ev.lengthComputable) return;
      const elapsedS = (Date.now() - startedAt) / 1000;
      const speed = ev.loaded / Math.max(elapsedS, 0.1);
      const etaS = (ev.total - ev.loaded) / Math.max(speed, 1);
      const pct = Math.round((ev.loaded / ev.total) * 100);
      updateTaskProgress(taskId, pct);
      setUpload({ file, loaded: ev.loaded, speed, etaS, status: pct >= 100 ? "succeeded" : "running" });
    });
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        completeTask(taskId, "succeeded");
        setUpload((u) => (u ? { ...u, status: "succeeded" } : u));
        onDone?.();
      } else {
        let msg = `HTTP error ${xhr.status}`;
        try { msg = JSON.parse(xhr.responseText).detail || msg; } catch { /* ignore */ }
        completeTask(taskId, "failed", msg);
      }
    });
    xhr.addEventListener("error", () => completeTask(taskId, "failed", t("upload.networkError")));
    xhr.send(fd);
  }, [addTask, updateTaskProgress, completeTask, onDone, t]);

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
        className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-6 cursor-pointer transition-colors duration-150 ${
          dragOver ? "border-accent-blue bg-accent-blue/5" : "border-muted-foreground/40 hover:border-muted-foreground/70"
        }`}
      >
        <UploadCloud size={24} className="text-foreground/80" />
        <p className="text-sm text-foreground/90">{labels.drop ?? t("upload.diskDrop")}</p>
        <p className="text-[11px] text-muted-foreground">qcow2, raw, img, vmdk, vdi, vhd, vhdx</p>
        <input aria-label={labels.input ?? t("upload.diskFileAria")}
          ref={inputRef}
          type="file"
          accept=".qcow2,.img,.raw,.vmdk,.vdi,.vhd,.vhdx"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && startUpload(e.target.files[0])}
        />
      </div>

      {upload && (
        <Card className="mt-3 p-3 animate-in fade-in-0 duration-150">
          <div className="flex items-center gap-2 text-sm text-foreground">
            <HardDrive size={15} className="text-foreground/80 shrink-0" />
            <span className="truncate">{upload.file.name}</span>
            <span className="ml-auto text-xs text-muted-foreground shrink-0">{formatBytes(upload.loaded)} / {formatBytes(upload.file.size)}</span>
          </div>
          <div className="mt-2">
            <ProgressBar value={(upload.loaded / (upload.file.size || 1)) * 100} status={upload.status} />
          </div>
          <div className="mt-1.5 flex justify-between text-xs text-muted-foreground">
            <span>{upload.status === "succeeded" ? (labels.done ?? t("upload.done")) : formatRate(upload.speed, lang)}</span>
            <span>{upload.status === "succeeded" ? "" : `${labels.eta ?? t("upload.eta")}: ${formatEta(upload.etaS)}`}</span>
          </div>
        </Card>
      )}
    </div>
  );
}
