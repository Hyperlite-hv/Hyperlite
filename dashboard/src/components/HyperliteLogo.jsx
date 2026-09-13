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
      <rect x="0" y="0" width="100" height="100" rx="18" fill="#201a52" />
      <g transform="skewX(-20)">
        <rect x="20" y="13" width="60" height="10" fill="#ffffff" />
        <rect x="20" y="29" width="60" height="10" fill="#8778f5" />
        <rect x="20" y="45" width="60" height="10" fill="#2fd9c4" />
        <rect x="20" y="61" width="60" height="10" fill="#8778f5" />
        <rect x="20" y="77" width="60" height="10" fill="#ffffff" />
      </g>
    </svg>
  );
}
