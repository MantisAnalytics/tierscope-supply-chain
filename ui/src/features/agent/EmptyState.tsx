import { useQueryClient } from "@tanstack/react-query";
import { MessageSquare, Newspaper, Send, Sparkles } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { agentKeys, threadOf, useStartThread } from "@/api/agent";
import { draftFromNews, opsKeys, useCreateDisruption, useLibrary, type LibraryEntry } from "@/api/ops";
import { useAuthor } from "@/app/author";
import { ErrorNote, Loading } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { formatEur } from "@/lib/utils";
import { agentPath } from "./address";
import { TypeIcon } from "./shared";

export const SUGGESTIONS = [
  "Which single-source parts carry the most revenue?",
  "Which suppliers have the largest Business Impact, and where are they?",
  "What would a six-week stop at our biggest Chinese supplier cost?",
];

const SAMPLE_NEWS =
  "Example alert (made up for this demo): The Suez Canal Authority has suspended container transits after a grounding blocked the channel. " +
  "Carriers are rerouting Asia–Europe services around the Cape of Good Hope, adding 10–14 days, and port congestion in " +
  "Rotterdam and Valencia is expected for at least six weeks. Spot freight rates on Asia–North Europe rose 35%.";

/** A new thread: ask a question (a general conversation), paste a news or alert text (a draft disruption the agent
 *  plans), or pick a ready scenario from the library (a disruption the planner starts on). */
export function EmptyState({ hasModel }: { hasModel: boolean }) {
  const [text, setText] = useState("");
  const [news, setNews] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const start = useStartThread();
  const create = useCreateDisruption();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { requireAuthor } = useAuthor();

  const ask = (q: string) => requireAuthor(() => start.mutate(q.trim(), { onSuccess: (r) => navigate(agentPath(r.thread_id)) }));
  const fromNews = () =>
    requireAuthor(async () => {
      setDrafting(true);
      setError(null);
      try {
        const draft = await draftFromNews(news);
        await Promise.all([qc.invalidateQueries({ queryKey: opsKeys.all }), qc.invalidateQueries({ queryKey: agentKeys.all })]);
        navigate(agentPath(draft.thread_id));
      } catch (e) {
        setError(e);
      } finally {
        setDrafting(false);
      }
    });
  const fromLibrary = (d: LibraryEntry) =>
    requireAuthor(() =>
      create.mutate(d, {
        onSuccess: async (r) => {
          const thread = await threadOf(r.id);
          navigate(thread ? agentPath(thread.id) : "/agent");
        },
      }),
    );

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-6 py-8">
      <section aria-label="Ask" className="flex flex-col gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold"><MessageSquare className="size-4 text-brand" /> Ask about your supply chain</h2>
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((q) => (
            <Button key={q} size="sm" variant="outline" disabled={!hasModel || start.isPending} onClick={() => ask(q)}>
              {q}
            </Button>
          ))}
        </div>
        <div className="flex items-end gap-2">
          <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} disabled={!hasModel} aria-label="First message"
            placeholder={hasModel ? "Or write your own question…" : "No model is configured: set MODEL_API_KEY (Muse Spark) or MUSE_API_KEY (Muse Glimmer) to talk to the agent."} />
          <Button variant="accent" aria-label="Start" disabled={!hasModel || !text.trim() || start.isPending} onClick={() => ask(text)}>
            <Send />
          </Button>
        </div>
        {start.error ? <ErrorNote error={start.error} /> : null}
      </section>

      <section aria-label="Paste news" className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold"><Newspaper className="size-4 text-brand" /> Paste a news item or an alert</h2>
          <Button variant="link" size="xs" onClick={() => setNews(SAMPLE_NEWS)}>Use a made-up example</Button>
        </div>
        <Textarea value={news} onChange={(e) => setNews(e.target.value)} rows={4} aria-label="News text" placeholder="Paste the text here; the model drafts the disruption on your graph and the agent plans it." />
        <Button variant="accent" className="w-fit" disabled={!hasModel || drafting || !news.trim()} onClick={fromNews}>
          <Sparkles /> {drafting ? "Reading…" : "Draft the disruption"}
        </Button>
        {error ? <ErrorNote error={error} /> : null}
      </section>

      <Library onPick={fromLibrary} busy={create.isPending} />
      {create.error ? <ErrorNote error={create.error} /> : null}
    </div>
  );
}

function Library({ onPick, busy }: { onPick: (d: LibraryEntry) => void; busy: boolean }) {
  const { data, error, isLoading } = useLibrary();
  return (
    <section aria-label="Library" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">Scenario library</h2>
      {isLoading ? (
        <Loading label="Simulating the library on your graph…" />
      ) : error || !data ? (
        <ErrorNote error={error ?? "no library"} />
      ) : (
        <ul className="flex flex-col divide-y rounded-lg border bg-surface">
          {data.map((d) => (
            <li key={d.name} className="flex items-center gap-3 px-3 py-2">
              <TypeIcon type={d.type} className="size-4 shrink-0 text-muted" />
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate text-sm font-medium">{d.name}</span>
                <span className="text-xs text-muted">
                  {d.type_label} · {d.affected_components} parts · <span className="font-semibold text-risk tabular">{formatEur(d.do_nothing_cost)}</span> if nothing is done
                </span>
              </span>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => onPick(d)} aria-label={`Start ${d.name}`}>
                Start
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
