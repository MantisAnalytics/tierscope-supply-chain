/** The Meta mark (an infinity loop) for the Agent button, drawn in `currentColor`. The real model and provider are
 *  always named in the Agent workspace's header. */
export function MetaLogo({ className, title = "Meta" }: { className?: string; title?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} role="img" aria-label={title}>
      <title>{title}</title>
      <path
        d="M12 12C10 8.8 8.2 7 6.2 7 3.9 7 2.5 9.3 2.5 12s1.4 5 3.7 5c2 0 3.8-1.8 5.8-5 2-3.2 3.8-5 5.8-5 2.3 0 3.7 2.3 3.7 5s-1.4 5-3.7 5c-2 0-3.8-1.8-5.8-5Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinejoin="round"
      />
    </svg>
  );
}
