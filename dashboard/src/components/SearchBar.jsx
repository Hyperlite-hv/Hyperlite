import { Search, X } from "lucide-react";
import { useInfraStore } from "../store/useInfraStore";

// Filtre instantane de l'arbre de ressources (colonne gauche) : la valeur est
// lue directement par ResourceTree.jsx pour masquer les elements qui ne
// correspondent pas, en gardant visibles les groupes parents d'un match.
export default function SearchBar() {
  const searchQuery = useInfraStore((s) => s.searchQuery);
  const setSearchQuery = useInfraStore((s) => s.setSearchQuery);

  return (
    <div className="relative w-full max-w-md">
      <Search size={15} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-anthracite-300" />
      <input
        className="input pl-8 pr-8"
        placeholder="Rechercher un node, une VM, un stockage..."
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
      />
      {searchQuery && (
        <button
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-anthracite-400 hover:text-anthracite-100"
          onClick={() => setSearchQuery("")}
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}
