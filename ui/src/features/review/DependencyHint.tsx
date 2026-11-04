import { Link2 } from "lucide-react";
import { Link } from "react-router-dom";

/** A proposal whose exposure effect waits for other pending proposals (revenue), with links to them. */
export function DependencyHint({ dependsOn, className }: { dependsOn: number[] | undefined; className?: string }) {
  if (!dependsOn?.length) return null;
  return (
    <p className={className} data-testid="dependency-hint">
      <Link2 className="mr-1 inline size-3.5 text-proposed" aria-hidden />
      Its effect appears once{" "}
      {dependsOn.map((id, i) => (
        <span key={id}>
          {i > 0 && ", "}
          <Link to={`/data/review/${id}`} className="text-interactive hover:underline" onClick={(e) => e.stopPropagation()}>
            proposal #{id}
          </Link>
        </span>
      ))}{" "}
      (assembly revenue) is accepted.
    </p>
  );
}
