import { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { usePromptStore } from "../store/usePromptStore";

// Name / value prompt in the same accessible dialog system as every other dialog: labelled field, Enter
// submits, Escape cancels, empty values are refused with a message instead of being silently ignored.
// option: an optional checkbox under the field ({ label, hint, checked }); a request with one resolves to
// { value, option } instead of the text alone, as ConfirmDialog's option does.
export default function PromptDialog({ cancelLabel = "Cancel" }) {
  const request = usePromptStore((s) => s.request);
  const answer = usePromptStore((s) => s.answer);
  const [value, setValue] = useState("");
  const [touched, setTouched] = useState(false);
  const [checked, setChecked] = useState(false);

  useEffect(() => { setValue(request?.defaultValue ?? ""); setTouched(false); setChecked(Boolean(request?.option?.checked)); }, [request]);

  // A password is taken as typed (spaces included) and masked.
  const secret = request?.type === "password";
  const trimmed = secret ? value : value.trim();
  const invalid = (secret ? value === "" : trimmed === "") ? "This value is required." : request?.validate?.(trimmed) || "";
  const submit = () => { setTouched(true); if (!invalid) answer(request?.option ? { value: trimmed, option: checked } : trimmed); };

  return (
    <Dialog open={!!request} onOpenChange={(o) => { if (!o) answer(null); }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{request?.title}</DialogTitle>
          {request?.message && <DialogDescription>{request.message}</DialogDescription>}
        </DialogHeader>
        <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="space-y-2">
          <Label htmlFor="prompt-value" className="text-xs font-medium">{request?.label}</Label>
          <Input id="prompt-value" aria-label={request?.label} type={secret ? "password" : "text"} autoComplete={secret ? "current-password" : undefined} autoFocus value={value} onChange={(e) => setValue(e.target.value)} aria-invalid={touched && !!invalid} aria-describedby={touched && invalid ? "prompt-error" : undefined} />
          {touched && invalid && <p id="prompt-error" role="alert" className="text-xs text-status-error">{invalid}</p>}
          {request?.option && (
            <label className="flex items-start gap-2 pt-1 text-sm">
              <input type="checkbox" className="mt-0.5" checked={checked} onChange={(e) => setChecked(e.target.checked)} aria-describedby={request.option.hint ? "prompt-option-hint" : undefined} />
              <span>{request.option.label}{request.option.hint && <span id="prompt-option-hint" className="block text-xs text-muted-foreground">{request.option.hint}</span>}</span>
            </label>
          )}
          <DialogFooter className="pt-2">
            <Button type="button" variant="secondary" onClick={() => answer(null)}>{cancelLabel}</Button>
            <Button type="submit">{request?.confirmLabel || "OK"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
