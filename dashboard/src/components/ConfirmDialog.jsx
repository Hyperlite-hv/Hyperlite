import { AlertTriangle } from "lucide-react";
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle,
  AlertDialogDescription, AlertDialogFooter, AlertDialogAction, AlertDialogCancel,
} from "@/components/ui/alert-dialog";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Radix's AlertDialog already provides the focus trap / focus-restore / Escape
// behavior this component used to implement by hand (see git history): trapping
// focus, defaulting to the safe choice, and restoring focus to the trigger on
// close.
export default function ConfirmDialog({ open, title, message, confirmLabel = "Confirm", danger = true, onConfirm, onCancel }) {
  return (
    <AlertDialog open={open} onOpenChange={(next) => { if (!next) onCancel(); }}>
      <AlertDialogContent className="max-w-sm">
        <AlertDialogHeader className="flex-row items-start gap-3 space-y-0">
          <div className={`mt-0.5 rounded-full p-1.5 ${danger ? "bg-status-error/15 text-status-error" : "bg-accent-blue/15 text-accent-blue"}`}>
            <AlertTriangle size={18} />
          </div>
          <div className="flex-1 text-left">
            <AlertDialogTitle className="text-sm">{title}</AlertDialogTitle>
            <AlertDialogDescription className="mt-1 text-sm">{message}</AlertDialogDescription>
          </div>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onCancel}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            className={danger ? cn(buttonVariants({ variant: "destructive" }), "bg-status-error/10 text-status-error border border-status-error/30 hover:bg-status-error/20") : undefined}
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
