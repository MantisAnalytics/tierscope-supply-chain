import { lazy, Suspense } from "react";
import { NavLink } from "react-router-dom";
import { useStats } from "@/api/queries";
import { Loading } from "@/components/States";
import { cn } from "@/lib/utils";

const IngestPage = lazy(() => import("@/features/ingest/IngestPage"));
const ReviewPage = lazy(() => import("@/features/review/ReviewPage"));
const SpecsPage = lazy(() => import("@/features/specs/SpecsPage"));
const JournalPage = lazy(() => import("@/features/journal/JournalPage"));

export type DataTab = "ingest" | "review" | "specs" | "history";

const TABS: { id: DataTab; label: string; hint: string }[] = [
  { id: "ingest", label: "Ingest", hint: "Upload files and archives" },
  { id: "review", label: "Review", hint: "Decide on what the model was unsure about" },
  { id: "specs", label: "Specs", hint: "Mapping specs the model wrote per file layout" },
  { id: "history", label: "History", hint: "Every change, with undo" },
];

/** Getting data in and keeping it right: ingest, review, mapping specs and the change history. */
export default function DataPage({ tab }: { tab: DataTab }) {
  const { data: stats } = useStats();
  const pending = stats ? Object.values(stats.proposals).reduce((a, b) => a + b, 0) : 0;
  return (
    <div className="flex h-full flex-col">
      <nav aria-label="Data" className="flex h-11 shrink-0 items-center gap-1 border-b bg-surface px-5">
        {TABS.map((t) => (
          <NavLink
            key={t.id}
            to={`/data/${t.id}`}
            title={t.hint}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm text-muted transition-colors hover:text-fg",
                (isActive || tab === t.id) && "bg-surface-2 font-medium text-fg",
              )
            }
          >
            {t.label}
            {t.id === "review" && pending > 0 && (
              <span className="rounded-full bg-brand px-1.5 text-[11px] leading-4 font-semibold text-brand-fg tabular">{pending}</span>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-auto">
        <Suspense fallback={<Loading />}>
          {tab === "ingest" && <IngestPage />}
          {tab === "review" && <ReviewPage />}
          {tab === "specs" && <SpecsPage />}
          {tab === "history" && <JournalPage />}
        </Suspense>
      </div>
    </div>
  );
}
