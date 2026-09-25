import EnclaveMark from "./EnclaveMark";

// HyperLite app icon: the Enclave mark on a graphite tile, readable on any background. Used by the
// sign-in screen and the classic interface; the rebuilt interface draws the bare mark in its sidebar.
export default function HyperliteLogo({ size = 28, className = "" }) {
  return (
    <span
      className={className}
      role="img"
      aria-label="Hyperlite"
      style={{
        display: "inline-grid",
        placeItems: "center",
        width: size,
        height: size,
        flex: "none",
        background: "#19161A",
        borderRadius: Math.round(size * 0.22),
      }}
    >
      <EnclaveMark size={Math.round(size * 0.64)} rails="#F1ECEE" core="#CE9DB2" />
    </span>
  );
}
