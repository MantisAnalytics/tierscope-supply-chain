import type { Network } from "@/api/client";
import { PanelStack } from "@/components/PanelStack";
import { useDiscuss } from "./discuss";
import type { EditIntent } from "./EditDialogs";
import { LinkPanel } from "./LinkPanel";
import { NodePanel } from "./NodePanel";
import { FACILITY, nodeKey, type NetworkView, type NodeRef } from "./view";

/** The crumbs below Summary: the selected facility or link, then the drill-in trail. Links are `Link:supplier>site`. */
export function crumbsOf(view: NetworkView): NodeRef[] {
  const base = view.sel ? [view.sel] : view.link ? [{ type: "Link", id: `${view.link.supplier}>${view.link.site}` }] : [];
  return [...base, ...view.trail];
}

const asLink = (crumb: NodeRef) => {
  const [supplier, site] = crumb.id.split(">");
  return { supplier, site };
};

/** How the panel moves: a site or supplier becomes the new selection; anything else is pushed onto the trail, or
 *  the trail goes back to it when it is already a crumb. */
export function panelMoves(view: NetworkView, update: (patch: Partial<NetworkView>) => void) {
  const crumbs = crumbsOf(view);
  const base = crumbs.length - view.trail.length;
  const go = (i: number) => update({ trail: view.trail.slice(0, Math.max(0, i - base + 1)) });
  const push = (crumb: NodeRef) => {
    const at = crumbs.findIndex((c) => nodeKey(c) === nodeKey(crumb));
    if (at >= 0) go(at);
    else if (crumbs.length === 0 && crumb.type === "Link") update({ link: asLink(crumb), sel: null, trail: [] });
    else update({ trail: [...view.trail, crumb] });
  };
  return {
    go,
    summary: () => update({ sel: null, link: null, trail: [] }),
    open: (type: string, id: string) => (FACILITY(type) ? update({ sel: { type, id }, link: null, trail: [] }) : push({ type, id })),
    openLink: (supplier: string, site: string) => push({ type: "Link", id: `${supplier}>${site}` }),
    /** The deepest crumb became another node (a merge): show that one in its place. */
    replace: (type: string, id: string) =>
      FACILITY(type) || view.trail.length === 0 ? update({ sel: FACILITY(type) ? { type, id } : null, link: null, trail: FACILITY(type) ? [] : [{ type, id }] }) : update({ trail: [...view.trail.slice(0, -1), { type, id }] }),
  };
}

/** The side panel when something is selected: a breadcrumb from Summary to the deepest crumb, and that crumb. */
export function SidePanel({ view, update, network, onEdit }: {
  view: NetworkView;
  update: (patch: Partial<NetworkView>) => void;
  network: Network | undefined;
  onEdit: (intent: EditIntent) => void;
}) {
  const crumbs = crumbsOf(view);
  const moves = panelMoves(view, update);
  const discuss = useDiscuss();
  const name = (c: NodeRef) => {
    if (c.type === "Link") {
      const { supplier, site } = asLink(c);
      const n = (t: string, i: string) => network?.entities.find((e) => e.type === t && e.id === i)?.name ?? i;
      return `${n("Supplier", supplier)} → ${n("Site", site)}`;
    }
    return network?.entities.find((e) => e.type === c.type && e.id === c.id)?.name ?? (c.type === "RiskIndicator" ? c.id.split("::").at(-1)! : c.id);
  };
  const deepest = crumbs.at(-1)!;
  return (
    <PanelStack root="Summary" crumbs={crumbs.map((c) => ({ key: nodeKey(c), label: name(c) }))} onRoot={moves.summary} onGo={moves.go}>
      {deepest.type === "Link" ? (
        network && <LinkPanel link={asLink(deepest)} network={network} onSelect={moves.open} onOpenPart={(id) => moves.open("Component", id)} />
      ) : (
        <NodePanel key={nodeKey(deepest)} type={deepest.type} id={deepest.id} network={network} onEdit={onEdit} onOpen={moves.open} onOpenLink={moves.openLink} onDiscuss={discuss} />
      )}
    </PanelStack>
  );
}
