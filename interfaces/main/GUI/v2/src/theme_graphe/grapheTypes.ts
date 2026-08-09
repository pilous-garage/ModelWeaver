// grapheTypes.ts — types partagés du module graphe_subpanel.
// Un graphe = nodes + edges (+ meta). Chaque node/edge a un type → le thème
// fournit sa représentation. `pos` (center/orientation/size) est OPTIONNEL :
// absent → layout auto (dagre) ; présent → position fixe.

import { parse as yamlParse } from 'yaml';

export interface GraphNodePos {
  center: [number, number];      // [x, y] centre du nœud
  orientation?: 'north' | 'south' | 'east' | 'west';
  size?: [number, number];       // [w, h]
}

export interface GraphNode {
  id: string;
  type: string;
  label: string;
  ref?: string;                   // référence (ex: agent-codeur-2, task_claim_next)
  tags?: string[];                // ex: ['agentic', 'llm', 'heavy', 'entrypoint']
  vars?: Record<string, any>;     // options (pos, structure custom UML…)
}

export interface GraphEdge {
  from: string;
  to: string;
  label?: string;
  type?: string;
  vars?: Record<string, any>;     // ex: { label_in, label_out }
}

export interface GraphDoc {
  id?: string;
  title?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  meta?: Record<string, any>;     // version, source, auteur…
}

// ── Parsing depuis YAML/JSON ────────────────────────────────────────

export function parseGraph(doc: string | Record<string, any>): GraphDoc {
  if (typeof doc === 'string') {
    doc = yamlParse(doc) as Record<string, any>;
  }
  const g = (doc || {}) as any;
  return {
    id: g.id,
    title: g.title,
    nodes: Array.isArray(g.nodes) ? g.nodes : [],
    edges: Array.isArray(g.edges) ? g.edges : [],
    meta: g.meta || {},
  };
}

// ── Gestion des positions ───────────────────────────────────────────

/** Retire les positions (pour un rendu auto via layout dagre). */
export function stripPositions(graph: GraphDoc): GraphDoc {
  return {
    ...graph,
    nodes: graph.nodes.map((n) => ({
      ...n,
      vars: n.vars ? { ...n.vars, pos: undefined } : {},
    })),
  };
}

/** Met à jour la position d'un nœud (drag en live). */
export function setNodePos(graph: GraphDoc, nodeId: string, pos: GraphNodePos): GraphDoc {
  return {
    ...graph,
    nodes: graph.nodes.map((n) =>
      n.id === nodeId ? { ...n, vars: { ...(n.vars || {}), pos } } : n),
  };
}

export function nodePosOf(n: GraphNode): GraphNodePos | undefined {
  return n.vars?.pos;
}
