import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";

// Imported only by the lazy graph views, so Cytoscape stays out of the initial route; registered once for both.
cytoscape.use(dagre);

export { cytoscape };

/** Canvas only where the browser draws one; otherwise (tests) Cytoscape runs headless. */
export function canDraw(): boolean {
  try {
    return !!document.createElement("canvas").getContext("2d");
  } catch {
    return false;
  }
}
