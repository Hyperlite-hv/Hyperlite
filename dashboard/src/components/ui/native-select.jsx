import { ChevronDownIcon } from "lucide-react"
import { cn } from "@/lib/cn"

// A REAL <select> (not shadcn's Radix-based Select in ./select.jsx): Playwright's
// `.selectOption()`, used throughout the e2e suite, only works against a native
// <select> (it errors on Radix's role="combobox" element). Every dropdown that
// gets exercised by a test uses this instead, styled to match SelectTrigger.
function NativeSelect({ className, children, ...props }) {
  return (
    <div className="relative inline-block w-full">
      <select
        data-slot="native-select"
        className={cn(
          "h-8 w-full appearance-none rounded-lg border border-input bg-transparent py-2 pr-8 pl-2.5 text-sm outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50",
          className
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDownIcon className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
    </div>
  )
}

export { NativeSelect }
