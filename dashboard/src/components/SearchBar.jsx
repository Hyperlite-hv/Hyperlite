import { Search, X } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";

// Instant filter of the resource tree (left column): the value is read directly
// by ResourceTree.jsx to hide the elements that do not match, keeping the parent
// groups of a match visible.
export default function SearchBar() {
  const searchQuery = useInfraStore((s) => s.searchQuery);
  const setSearchQuery = useInfraStore((s) => s.setSearchQuery);

  return (
    <div className="relative w-full max-w-md">
      <Search size={15} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-anthracite-300" />
      <input aria-label="Search for a node, a VM, a storage..."
        className="input pl-8 pr-8"
        placeholder="Search for a node, a VM, a storage..."
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
      />
      {searchQuery && (
        <button aria-label="Close"
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-anthracite-400 hover:text-anthracite-100"
          onClick={() => setSearchQuery("")}
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}
