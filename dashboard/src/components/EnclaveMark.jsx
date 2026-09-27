// Enclave, the HyperLite mark: two side rails that frame a modular core (isolated workloads held by a
// stable structure). Drawn on a 32-unit grid with even coordinates only, so every edge lands on a whole
// pixel at 16 and 32 px. Rails take the text colour of their surface, the core takes the brand colour.
export const ENCLAVE_RAILS = "M4 4H14V8H10V24H14V28H4Z M28 4H18V8H22V24H18V28H28Z";
export const ENCLAVE_CORE = "M12 10H20V14H12Z M12 18H20V22H12Z";

export default function EnclaveMark({ size = 20, rails = "currentColor", core = "currentColor", title, className = "" }) {
  const labelled = Boolean(title);
  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      className={className}
      shapeRendering="crispEdges"
      role={labelled ? "img" : undefined}
      aria-label={labelled ? title : undefined}
      aria-hidden={labelled ? undefined : true}
      focusable="false"
    >
      <path d={ENCLAVE_RAILS} fill={rails} />
      <path d={ENCLAVE_CORE} fill={core} />
    </svg>
  );
}
