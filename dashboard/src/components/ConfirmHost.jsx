import { useEffect, useState } from "react";
import { useConfirmStore } from "../store/useConfirmStore";
import ConfirmDialog from "./ConfirmDialog";
import PromptDialog from "./PromptDialog";

// The labels default to English for the historical interface; the rebuilt one passes its translations.
export default function ConfirmHost({ confirmLabel = "Confirm", cancelLabel = "Cancel" }) {
  const request = useConfirmStore((s) => s.request);
  const answer = useConfirmStore((s) => s.answer);
  // A request with an option resolves to { option } when confirmed (false when cancelled), others to true.
  const [checked, setChecked] = useState(false);
  useEffect(() => { setChecked(Boolean(request?.option?.checked)); }, [request]);
  return (
    <>
    <PromptDialog cancelLabel={cancelLabel} />
    <ConfirmDialog
      open={!!request}
      title={request?.title ?? ""}
      message={request?.message ?? ""}
      confirmLabel={request?.confirmLabel ?? confirmLabel}
      cancelLabel={cancelLabel}
      danger={request?.danger ?? true}
      onConfirm={() => answer(request?.option ? { option: checked } : true)}
      option={request?.option ?? null}
      optionChecked={checked}
      onOptionChange={setChecked}
      onCancel={() => answer(false)}
    />
    </>
  );
}
