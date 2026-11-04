import { Undo2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { useJournal, useUndo } from "@/api/queries";
import { useAuthor } from "@/app/author";
import { ErrorNote, Loading } from "@/components/States";
import { VirtualList } from "@/components/VirtualList";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatTime } from "@/lib/utils";

const UNDOABLE = ["accept", "reject", "edit"];

export default function JournalPage() {
  const { data, error, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } = useJournal();
  const undo = useUndo();
  const { requireAuthor } = useAuthor();
  const [refusal, setRefusal] = useState<{ id: number; message: string } | null>(null);
  if (isLoading) return <Loading />;
  if (error) return <ErrorNote error={error} className="m-6" />;
  const entries = data?.pages.flat() ?? [];
  const run = (id: number) =>
    requireAuthor(() => {
      setRefusal(null);
      undo.mutate(id, {
        onSuccess: (r) => toast.success(`Undone: ${r.journal.summary}`),
        onError: (e) => setRefusal({ id, message: e.message }),
      });
    });
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-3 p-6">
      <h1 className="text-xl font-semibold">Journal</h1>
      <VirtualList
        label="Journal entries"
        className="max-h-[calc(100vh-14rem)] card rounded-xl border bg-surface"
        items={entries}
        estimate={46}
        getKey={(e) => e.id}
        render={(e) => (
          <div className="flex flex-col gap-1 border-b px-4 py-2.5">
            <div className="flex items-center gap-3 text-sm">
              <span className="w-12 text-xs text-muted tabular">#{e.id}</span>
              <span className="w-40 shrink-0 text-xs text-muted">{formatTime(e.ts)}</span>
              <Badge className="border-border">{e.action}</Badge>
              <span className="min-w-0 flex-1 truncate" title={e.summary}>{e.summary}</span>
              <span className="shrink-0 text-xs text-muted">{e.actor}</span>
              {e.undone_by ? (
                <span className="w-20 shrink-0 text-right text-xs text-muted">undone by #{e.undone_by}</span>
              ) : UNDOABLE.includes(e.action) ? (
                <Button size="xs" variant="outline" className="w-20 shrink-0" onClick={() => run(e.id)} disabled={undo.isPending} aria-label={`Undo #${e.id}`}>
                  <Undo2 /> Undo
                </Button>
              ) : (
                <span className="w-20 shrink-0" />
              )}
            </div>
            {refusal?.id === e.id && <ErrorNote error={`Undo refused: ${refusal.message}`} className="ml-15" />}
          </div>
        )}
      />
      {hasNextPage && (
        <Button variant="outline" className="self-center" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
          {isFetchingNextPage ? "Loading…" : "Load older entries"}
        </Button>
      )}
    </div>
  );
}
