/**
 * Renders `backtick`-delimited spans as <code>, matching the console's own
 * convention of marking up exact technical terms (pytest, apply_patch,
 * gates) rather than leaving them as unstyled prose. Deliberately not a
 * markdown parser — this project's copy only ever needs one construct.
 */
export function InlineText({ text }) {
  const parts = String(text).split(/`([^`]+)`/g);
  return parts.map((part, i) => (i % 2 === 1 ? <code key={i}>{part}</code> : part));
}
