// Fitted to the reference mark (99% pixel overlap): ring,
// triangle and dot share the centre (50, 50). The triangle is an outer and an inner rounded triangle filled even-odd,
// so its band is thicker at the bottom tip as in the mark; its top edge is level.
const TRIANGLE =
  "M28.67 26.63L71.33 26.63A10.88 10.88 0 0 1 80.74 42.97L59.41 79.73A10.88 10.88 0 0 1 40.59 79.73L19.26 42.97A10.88 10.88 0 0 1 28.67 26.63ZM37.36 35.28L62.64 35.28A6.73 6.73 0 0 1 68.48 45.35L55.84 67.48A6.73 6.73 0 0 1 44.16 67.48L31.52 45.35A6.73 6.73 0 0 1 37.36 35.28Z";

/** The Mantis Analytics mark (ring, rounded triangle, dot), drawn in `currentColor` so it follows the theme. */
export function Logo({ className, title = "Mantis Analytics" }: { className?: string; title?: string }) {
  return (
    <svg viewBox="0 0 100 100" className={className} role="img" aria-label={title}>
      <title>{title}</title>
      <circle cx="50" cy="50" r="46.5" fill="none" stroke="currentColor" strokeWidth="7" />
      <path d={TRIANGLE} fill="currentColor" fillRule="evenodd" />
      <circle cx="50" cy="50" r="5.27" fill="currentColor" />
    </svg>
  );
}
