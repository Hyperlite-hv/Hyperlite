// Marque Hyperlite (5 barres inclinees, blanc/violet/turquoise) -- utilisee
// dans la barre de navigation (Header) et l'ecran de connexion (LoginScreen),
// remplace l'ancien pictogramme generique (icone Server de lucide-react).
export default function HyperliteLogo({ size = 28, className = "" }) {
  return (
    <svg
      viewBox="0 0 100 100"
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label="Hyperlite"
    >
      <rect x="0" y="0" width="100" height="100" fill="#201a52" />
      <polygon points="23.9,27.6 73.7,27.6 68.8,33.5 19.0,33.5" fill="#ffffff" />
      <polygon points="23.9,37.4 73.7,37.4 68.8,43.3 19.0,43.3" fill="#8778f5" />
      <polygon points="23.9,47.1 73.7,47.1 68.8,53.0 19.0,53.0" fill="#2fd9c4" />
      <polygon points="23.9,56.9 73.7,56.9 68.8,62.8 19.0,62.8" fill="#8778f5" />
      <polygon points="23.9,66.6 73.7,66.6 68.8,72.5 19.0,72.5" fill="#ffffff" />
    </svg>
  );
}
