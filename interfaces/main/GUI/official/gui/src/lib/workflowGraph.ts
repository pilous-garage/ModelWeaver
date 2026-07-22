import type { Node, Edge } from '@xyflow/react';
import dagre from 'dagre';

export type StepType =
  | 'llm_call' | 'call' | 'tool_call' | 'switch'
  | 'set_variable' | 'sleep' | 'spawn' | 'handoff' | 'end'
  | 'for' | 'while' | 'break' | 'continue'
  | 'agent_call' | 'if' | 'group';

export const TERMINAL_TYPES = ['end', 'break', 'continue'];
export function isTerminal(type: string): boolean { return TERMINAL_TYPES.includes(type); }

export interface Step {
  id: string;
  type: StepType | string;
  [key: string]: any;
}

export interface FsmNodeData {
  step: Step;          // step SANS body (le body vit dans les nœuds enfants)
  isEntry?: boolean;
  collapsed?: boolean;
  [key: string]: any;
}

export type FsmNode = Node<FsmNodeData>;

export const LOOP_TYPES: StepType[] = ['for', 'while', 'if', 'group'];
export function isLoop(type: string): boolean { return type === 'for' || type === 'while' || type === 'if' || type === 'group'; }

export const STEP_TYPES: StepType[] = [
  'llm_call', 'call', 'tool_call', 'switch',
  'set_variable', 'for', 'while', 'break', 'continue',
  'sleep', 'spawn', 'handoff', 'agent_call', 'if', 'group', 'end',
];

export const STEP_META: Record<string, { label: string; color: string; icon: string }> = {
  llm_call:     { label: 'LLM',      color: '#89b4fa', icon: '🧠' },
  call:         { label: 'Skill',    color: '#a6e3a1', icon: '🧩' },
  tool_call:    { label: 'Tool',     color: '#94e2d5', icon: '🔧' },
  switch:       { label: 'Switch',   color: '#cba6f7', icon: '🔀' },
  set_variable: { label: 'Set var',  color: '#f9e2af', icon: '📝' },
  for:          { label: 'For',      color: '#eba0ac', icon: '🔁' },
  while:        { label: 'While',    color: '#eba0ac', icon: '🔄' },
  break:        { label: 'Break',    color: '#f38ba8', icon: '⏏️' },
  continue:     { label: 'Continue', color: '#fab387', icon: '⤾' },
  sleep:        { label: 'Sleep',    color: '#7f849c', icon: '💤' },
  spawn:        { label: 'Spawn',    color: '#fab387', icon: '🐣' },
  handoff:      { label: 'Handoff',  color: '#f5c2e7', icon: '🤝' },
  agent_call:   { label: 'Agent',    color: '#a6e3a1', icon: '🤖' },
  if:           { label: 'If',       color: '#cba6f7', icon: '🔀' },
  group:        { label: 'Group',    color: '#6c7086', icon: '📁' },
  end:          { label: 'End',      color: '#f38ba8', icon: '⏹️' },
};

export function stepMeta(type: string) {
  return STEP_META[type] || { label: type, color: '#89b4fa', icon: '●' };
}

function bodyStepsOf(step: Step): Step[] {
  const b = step.body;
  if (Array.isArray(b)) return b;
  if (b && Array.isArray(b.steps)) return b.steps;
  return [];
}

// ── Skills utilisés (nœuds `call`, récursif dans les corps de boucle) ──
export function collectSkillRefs(steps: Step[]): string[] {
  const refs: string[] = [];
  const walk = (list: Step[]) => {
    for (const st of list) {
      if (st.type === 'call' && typeof st.fn === 'string' && st.fn) refs.push(st.fn);
      if (isLoop(st.type)) walk(bodyStepsOf(st));
      if (st.type === 'if' && Array.isArray(st.else_body)) walk(st.else_body);
    }
  };
  walk(steps);
  return refs;
}

// Références vers agents (agent_call)
export function collectAgentRefs(steps: Step[]): string[] {
  const refs: string[] = [];
  const walk = (list: Step[]) => {
    for (const st of list) {
      if (st.type === 'agent_call' && typeof st.agent === 'string' && st.agent) refs.push(st.agent);
      if (isLoop(st.type)) walk(bodyStepsOf(st));
      if (st.type === 'if' && Array.isArray(st.else_body)) walk(st.else_body);
    }
  };
  walk(steps);
  return [...new Set(refs)];
}

export function skillCounts(steps: Step[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const ref of collectSkillRefs(steps)) counts[ref] = (counts[ref] || 0) + 1;
  return counts;
}

// ── Point d'entrée d'un niveau : premier step non référencé ─────
function findEntryId(steps: Step[]): string | null {
  const referenced = new Set<string>();
  for (const s of steps) {
    if (s.next) referenced.add(s.next);
    for (const c of s.conditions || []) if (c.next) referenced.add(c.next);
    if (s.default) referenced.add(s.default);
    if (s.on_error) referenced.add(s.on_error);
  }
  for (const s of steps) if (!referenced.has(s.id)) return s.id;
  return steps[0]?.id ?? null;
}

// ── steps[] (imbriqués) → { nodes, edges } ──────────────────────
export function stepsToGraph(steps: Step[]): { nodes: FsmNode[]; edges: Edge[] } {
  const nodes: FsmNode[] = [];
  const edges: Edge[] = [];

  const buildLevel = (list: Step[], parentId?: string) => {
    const entry = findEntryId(list);
    const ids = new Set(list.map(s => s.id));

    for (const st of list) {
      const loop = isLoop(st.type);
      const hasBody = loop || Array.isArray(st.body) || (st.body && Array.isArray(st.body.steps));
      const { body, ...stripped } = st;   // body extrait → enfants
      const node: FsmNode = {
        id: st.id,
        type: loop ? 'loop' : 'fsm',
        position: { x: 0, y: 0 },
        data: { step: loop ? stripped : st, isEntry: st.id === entry },
        ...(parentId ? { parentId, extent: 'parent' as const } : {}),
      };
      nodes.push(node);
      if (hasBody) buildLevel(bodyStepsOf(st), st.id);
    }

    const push = (source: string, target: string, kind: string, label?: string) => {
      if (!target || !ids.has(target)) return;
      edges.push({
        id: `${source}::${kind}::${target}`,
        source, target, label,
        data: { kind },
        type: 'smoothstep',
        style: { stroke: kind === 'on_error' ? '#f38ba8' : '#6c7086' },
        labelStyle: { fill: '#cdd6f4', fontSize: 10 },
        labelBgStyle: { fill: '#181825' },
        zIndex: parentId ? 1 : 0,
      });
    };

    for (const st of list) {
      if (st.type === 'switch') {
        (st.conditions || []).forEach((c: any, i: number) =>
          push(st.id, c.next, `cond${i}`, `${c.operator || 'EQUALS'} ${c.value ?? ''}`.trim()));
        if (st.default) push(st.id, st.default, 'default', 'default');
      } else if (st.next) {
        push(st.id, st.next, 'next');
      }
      if (st.on_error) push(st.id, st.on_error, 'on_error', 'on_error');
    }
  };

  buildLevel(steps, undefined);
  return { nodes, edges };
}

// ── { nodes } → steps[] (reconstitue l'imbrication du body) ─────
export function graphToSteps(nodes: FsmNode[]): Step[] {
  const byParent = new Map<string | undefined, FsmNode[]>();
  for (const n of nodes) {
    const p = (n as any).parentId as string | undefined;
    if (!byParent.has(p)) byParent.set(p, []);
    byParent.get(p)!.push(n);
  }
  const level = (parentId: string | undefined): Step[] => {
    const lvl = byParent.get(parentId) || [];
    const steps = lvl.map(n => {
      const st: Step = { ...n.data.step, id: n.id };
      const kids = byParent.get(n.id);
      if (kids && kids.length > 0) st.body = { steps: level(n.id) };
      return st;
    });
    const entry = findEntryId(steps);
    steps.sort((a, b) => (a.id === entry ? -1 : b.id === entry ? 1 : 0));
    return steps;
  };
  return level(undefined);
}

// ── Auto-layout dagre récursif (imbrication via parentId) ───────
const NODE_W = 200;
const NODE_H = 70;
const HEADER_H = 44;
const PAD = 20;

export function layout(
  nodes: FsmNode[],
  edges: Edge[],
  direction: 'TB' | 'LR' = 'TB',
): FsmNode[] {
  const childrenOf = new Map<string | undefined, FsmNode[]>();
  for (const n of nodes) {
    const p = (n as any).parentId as string | undefined;
    if (!childrenOf.has(p)) childrenOf.set(p, []);
    childrenOf.get(p)!.push(n);
  }
  const sizes = new Map<string, { w: number; h: number }>();
  const out = new Map<string, FsmNode>();

  // Positionne un niveau (relatif au parent) ; renvoie sa taille englobante.
  const layoutLevel = (parentId: string | undefined): { w: number; h: number } => {
    const kids = childrenOf.get(parentId) || [];
    if (kids.length === 0) return { w: NODE_W, h: NODE_H };

    const g = new dagre.graphlib.Graph();
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: direction, nodesep: 36, ranksep: 60, marginx: 8, marginy: 8 });

    for (const n of kids) {
      let w = NODE_W, h = NODE_H;
      if (n.type === 'loop') {
        const inner = layoutLevel(n.id);
        w = Math.max(NODE_W, inner.w);
        h = HEADER_H + inner.h;
        sizes.set(n.id, { w, h });
      }
      g.setNode(n.id, { width: w, height: h });
    }
    for (const e of edges) {
      if (g.hasNode(e.source) && g.hasNode(e.target)) g.setEdge(e.source, e.target);
    }
    dagre.layout(g);

    let maxX = 0, maxY = 0;
    for (const n of kids) {
      const p = g.node(n.id);
      const w = (n.type === 'loop' ? sizes.get(n.id)!.w : NODE_W);
      const h = (n.type === 'loop' ? sizes.get(n.id)!.h : NODE_H);
      const x = (p?.x ?? 0) - w / 2 + PAD;
      const y = (p?.y ?? 0) - h / 2 + (parentId ? HEADER_H : 0) + PAD;
      out.set(n.id, { ...n, position: { x, y }, style: n.type === 'loop' ? { ...(n.style || {}), width: w, height: h } : n.style });
      maxX = Math.max(maxX, x + w);
      maxY = Math.max(maxY, y + h);
    }
    return { w: maxX + PAD, h: maxY + PAD };
  };

  layoutLevel(undefined);
  return nodes.map(n => out.get(n.id) || n);
}
