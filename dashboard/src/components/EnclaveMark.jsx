// The Hyperlite mark: an open cube, its top face lit (a machine in its box). Lines take the colour of the text
// around them; the top face is the same colour, lighter.
export default function EnclaveMark({ size = 20, rails = "currentColor", core = rails, title, className = "" }) {
  const labelled = Boolean(title);
  return (
    <svg viewBox="0 0 48 48" width={size} height={size} className={className} role={labelled ? "img" : undefined}
      aria-label={labelled ? title : undefined} aria-hidden={labelled ? undefined : true} focusable="false">
      <path d="M24 7l15 8.6L24 24 9 15.6z" fill={core} fillOpacity="0.55" />
      <path d="M24 7l15 8.6v16.8L24 41 9 32.4V15.6zM9 15.6L24 24l15-8.4M24 24v17" fill="none" stroke={rails} strokeWidth="3.6" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
