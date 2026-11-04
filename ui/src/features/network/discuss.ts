import { useNavigate } from "react-router-dom";
import { useStartThread } from "@/api/agent";
import { useAuthor } from "@/app/author";
import { agentPath } from "@/features/agent/address";

/** Discuss a node: a new conversation in Agent whose first message names the node, with the node open in the detail
 *  column. The agent answers that message like any other. */
export function useDiscuss() {
  const start = useStartThread();
  const navigate = useNavigate();
  const { requireAuthor } = useAuthor();
  return (type: string, id: string, name: string) =>
    requireAuthor(() =>
      start.mutate(`About ${type} ${id}${name && name !== id ? ` (${name})` : ""}.`, {
        onSuccess: (r) => navigate(agentPath(r.thread_id, [{ kind: "node", id: `${type}:${id}` }])),
      }),
    );
}
