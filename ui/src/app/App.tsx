import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { threadOf } from "@/api/agent";
import { explorerPath } from "@/lib/utils";
import { Loading } from "@/components/States";
import { OverviewPage } from "@/features/overview/OverviewPage";
import { Shell } from "./Shell";

// Screens other than the Overview load on first visit; the graph and map code are separate chunks too.
const DataPage = lazy(() => import("@/features/data/DataPage"));
const AgentPage = lazy(() => import("@/features/agent/AgentPage"));
const ReportsPage = lazy(() => import("@/features/reports/ReportsPage"));

/** Earlier Explorer addresses open the node on the Overview: sites and suppliers selected, others in the side panel. */
function ExplorerMoved() {
  const { type } = useParams();
  const id = useParams()["*"];
  return <Navigate to={type && id ? explorerPath(type, id) : "/"} replace />;
}

/** Earlier addresses of the data screens open the matching Data tab. */
function Moved({ to }: { to: string }) {
  const { id, name } = useParams();
  const tail = id ?? name;
  return <Navigate to={tail ? `${to}/${encodeURIComponent(tail)}` : to} replace />;
}

/** An earlier disruption address: its thread in Agent, or Reports once it is resolved; the list without a thread. */
function DisruptionMoved() {
  const { id } = useParams();
  const did = id && /^\d+$/.test(id) ? Number(id) : undefined;
  const { data, isLoading } = useQuery({ queryKey: ["agent", "moved", did], queryFn: () => threadOf(did!), enabled: did !== undefined });
  if (did === undefined) return <Navigate to="/agent" replace />;
  if (isLoading) return <Loading />;
  if (data?.disruption_status === "resolved") return <Navigate to={`/reports/d/${did}`} replace />;
  return <Navigate to={data && data.status === "active" ? `/agent/t/${data.id}` : "/agent"} replace />;
}

export function App() {
  return (
    <Shell>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/explorer/:type?/*" element={<ExplorerMoved />} />
          <Route path="/agent" element={<AgentPage />} />
          <Route path="/agent/t/:threadId" element={<AgentPage />} />
          <Route path="/reports/disruption/:id" element={<Moved to="/reports/d" />} />
          <Route path="/reports/:kind?/:id?" element={<ReportsPage />} />
          <Route path="/disruptions/:id" element={<DisruptionMoved />} />
          <Route path="/disruptions/*" element={<Navigate to="/agent" replace />} />
          <Route path="/actions" element={<Navigate to="/agent" replace />} />
          <Route path="/data" element={<Navigate to="/data/ingest" replace />} />
          <Route path="/data/ingest" element={<DataPage tab="ingest" />} />
          <Route path="/data/review/:id?" element={<DataPage tab="review" />} />
          <Route path="/data/specs/:name?" element={<DataPage tab="specs" />} />
          <Route path="/data/history" element={<DataPage tab="history" />} />
          <Route path="/review/:id?" element={<Moved to="/data/review" />} />
          <Route path="/ingest" element={<Moved to="/data/ingest" />} />
          <Route path="/specs/:name?" element={<Moved to="/data/specs" />} />
          <Route path="/journal" element={<Moved to="/data/history" />} />
          <Route path="*" element={<OverviewPage />} />
        </Routes>
      </Suspense>
    </Shell>
  );
}
