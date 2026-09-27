import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

// Class-name helper of the shadcn components: joins conditional classes AND resolves Tailwind
// conflicts (the last one wins), so a `className="max-w-2xl"` given to a component really overrides the
// component's own default. The previous helper (the `cn` npm package) only joined strings, which
// silently kept the default (e.g. dialogs stayed 24rem wide whatever width they asked for).
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
