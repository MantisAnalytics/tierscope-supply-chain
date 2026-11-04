import { Inbox } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useProposals } from "@/api/queries";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { ProposalDetail } from "./ProposalDetail";
import { Queue } from "./Queue";

export default function ReviewPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState("pending");
  const [kind, setKind] = useState("");
  const { data = [], error, isLoading } = useProposals(status, kind || undefined);
  const selected = id ? Number(id) : data[0]?.id;
  return (
    <div className="grid h-full grid-cols-[420px_1fr]">
      <aside className="min-h-0 border-r bg-surface">
        {isLoading ? (
          <Loading />
        ) : error ? (
          <ErrorNote error={error} className="m-3" />
        ) : (
          <Queue proposals={data} selected={selected} onSelect={(p) => navigate(`/data/review/${p}`)} status={status} kind={kind} onStatus={setStatus} onKind={setKind} />
        )}
      </aside>
      <section className="min-h-0 overflow-auto">
        {selected ? (
          <ProposalDetail id={selected} />
        ) : (
          !isLoading && (
            <Empty className="flex h-full flex-col items-center justify-center gap-2">
              <Inbox className="size-8" /> No proposals {status === "all" ? "" : status}.
            </Empty>
          )
        )}
      </section>
    </div>
  );
}
