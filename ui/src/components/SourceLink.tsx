import { FileText, Link2, MessageSquare, Paperclip, User } from "lucide-react";

export interface SourceLocation {
  file?: string;
  sheet?: string;
  row?: number;
  column?: string;
  page?: number;
  lines?: number[];
  span?: number[];
  quote?: string;
  url?: string;
  note?: string;
  author?: string;
  evidence_id?: string;
}

/** Where a value came from: file and cell, page or lines, quote, evidence, link, comment and author. */
export function SourceDetails({ source, label }: { source: SourceLocation; label?: string }) {
  const place = [
    source.sheet && `${source.sheet}!${source.column ?? ""}${source.row ?? ""}`,
    source.page !== undefined && `page ${source.page}`,
    source.lines && `lines ${source.lines[0]}–${source.lines[1]}`,
  ].filter(Boolean);
  return (
    <div className="flex flex-col gap-1 text-xs">
      {(source.file || label) && (
        <div className="flex items-center gap-1.5 text-muted">
          <FileText className="size-3.5 shrink-0" aria-hidden />
          <span className="break-all">{source.file ?? label}</span>
          {place.length > 0 && <span className="text-fg">{place.join(", ")}</span>}
        </div>
      )}
      {source.quote && (
        <blockquote className="border-l-2 border-lavender pl-2 text-fg italic">“{source.quote}”</blockquote>
      )}
      {source.evidence_id && (
        <a className="flex items-center gap-1.5 text-interactive hover:underline" href={`/api/evidence/${source.evidence_id}`} download>
          <Paperclip className="size-3.5" aria-hidden /> evidence file
        </a>
      )}
      {source.url && (
        <a className="flex items-center gap-1.5 text-interactive hover:underline break-all" href={source.url} target="_blank" rel="noreferrer">
          <Link2 className="size-3.5 shrink-0" aria-hidden /> {source.url}
        </a>
      )}
      {source.note && (
        <div className="flex items-center gap-1.5">
          <MessageSquare className="size-3.5 text-muted" aria-hidden /> {source.note}
        </div>
      )}
      {source.author && (
        <div className="flex items-center gap-1.5 text-muted">
          <User className="size-3.5" aria-hidden /> {source.author}
        </div>
      )}
    </div>
  );
}
