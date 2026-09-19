import { useConfirmStore } from "../store/useConfirmStore";
import ConfirmDialog from "./ConfirmDialog";

export default function ConfirmHost() {
  const request = useConfirmStore((s) => s.request);
  const answer = useConfirmStore((s) => s.answer);
  return (
    <ConfirmDialog
      open={!!request}
      title={request?.title ?? ""}
      message={request?.message ?? ""}
      confirmLabel={request?.confirmLabel ?? "Confirm"}
      danger={request?.danger ?? true}
      onConfirm={() => answer(true)}
      onCancel={() => answer(false)}
    />
  );
}
