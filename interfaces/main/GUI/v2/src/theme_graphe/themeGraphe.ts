// themeGraphe.ts — chargement et application des thèmes de graphe.
// Le rendu = graphe.yaml (données) + theme.yaml (style type → représentation).
// ThemeGraphe fournit la représentation d'un type de node/edge.

import { parse as yamlParse } from 'yaml';

export interface NodeStyle {
  shape: 'rect' | 'rounded' | 'pill' | 'diamond' | 'hex' | 'square' | 'circle';
  icon?: string;
  color: string;
  border: string;
  thick?: boolean;
}

export interface EdgeStyle {
  style: 'solid' | 'dashed' | 'dotted';
  color: string;
  arrow: boolean;
}

export interface ThemeGraphe {
  id: string;
  name: string;
  color_scheme: string;
  nodes: Record<string, NodeStyle>;
  edges: Record<string, EdgeStyle>;
}

const DEFAULT_NODE: NodeStyle = { shape: 'rounded', color: '#94a3b8', border: '#64748b' };
const DEFAULT_EDGE: EdgeStyle = { style: 'solid', color: '#64748b', arrow: true };

// Thème par défaut embarqué (chargeable depuis theme.yaml externe).
const EMBEDDED: ThemeGraphe = {
  id: 'default',
  name: 'Défaut',
  color_scheme: 'dark',
  nodes: {
    entry:  { shape: 'pill', icon: '▶', color: '#a6e3a1', border: '#4ade80', thick: true },
    exit:   { shape: 'diamond', icon: '⏹', color: '#f38ba8', border: '#fb7185' },
    call:   { shape: 'rounded', icon: '🧩', color: '#a6e3a1', border: '#4ade80' },
    tool:   { shape: 'rounded', icon: '🔧', color: '#94e2d5', border: '#2dd4bf' },
    llm:    { shape: 'rect', icon: '🧠', color: '#89b4fa', border: '#60a5fa' },
    sleep:  { shape: 'rounded', icon: '💤', color: '#7f849c', border: '#94a3b8' },
    switch: { shape: 'diamond', icon: '🔀', color: '#cba6f7', border: '#c084fc' },
    if:     { shape: 'diamond', icon: '🔀', color: '#cba6f7', border: '#c084fc' },
    group:  { shape: 'rect', icon: '📁', color: '#6c7086', border: '#475569' },
    agent:  { shape: 'rounded', icon: '🤖', color: '#a6e3a1', border: '#4ade80' },
    handoff:{ shape: 'rounded', icon: '🤝', color: '#f5c2e7', border: '#f0abfc' },
    spawn:  { shape: 'rounded', icon: '🐣', color: '#fab387', border: '#fb923c' },
    end:    { shape: 'diamond', icon: '⏹', color: '#f38ba8', border: '#fb7185' },
    token:  { shape: 'circle', icon: '●', color: '#f9e2af', border: '#fbbf24' },
    default:{ shape: 'rounded', icon: '●', color: '#94a3b8', border: '#64748b' },
  },
  edges: {
    flow:    { style: 'solid', color: '#64748b', arrow: true },
    next:    { style: 'solid', color: '#94a3b8', arrow: true },
    success: { style: 'solid', color: '#4ade80', arrow: true },
    error:   { style: 'dashed', color: '#f87171', arrow: true },
    handoff: { style: 'dashed', color: '#f0abfc', arrow: true },
    token:   { style: 'dotted', color: '#fbbf24', arrow: true },
    default: { style: 'solid', color: '#64748b', arrow: true },
  },
};

/** Charge un thème depuis un objet parsé (YAML) ou retourne l'embarqué. */
export function loadTheme(raw: Record<string, any> | null | undefined): ThemeGraphe {
  if (!raw) return EMBEDDED;
  const nodes: Record<string, NodeStyle> = { ...EMBEDDED.nodes };
  const edges: Record<string, EdgeStyle> = { ...EMBEDDED.edges };
  if (raw.nodes && typeof raw.nodes === 'object') {
    for (const [k, v] of Object.entries(raw.nodes)) {
      nodes[k] = { ...DEFAULT_NODE, ...(v as any) };
    }
  }
  if (raw.edges && typeof raw.edges === 'object') {
    for (const [k, v] of Object.entries(raw.edges)) {
      edges[k] = { ...DEFAULT_EDGE, ...(v as any) };
    }
  }
  return {
    id: raw.id || 'custom',
    name: raw.name || 'Personnalisé',
    color_scheme: raw.color_scheme || 'dark',
    nodes,
    edges,
  };
}

/** Parse un theme.yaml (string) en ThemeGraphe. */
export function parseThemeYaml(yaml: string): ThemeGraphe {
  try {
    return loadTheme(yamlParse(yaml));
  } catch {
    return EMBEDDED;
  }
}

/** Représentation d'un type de node (avec fallback 'default'). */
export function nodeStyleOf(theme: ThemeGraphe, type: string): NodeStyle {
  return theme.nodes[type] || theme.nodes.default || DEFAULT_NODE;
}

/** Représentation d'un type de edge (avec fallback 'default'). */
export function edgeStyleOf(theme: ThemeGraphe, type?: string): EdgeStyle {
  if (!type) return theme.edges.default || DEFAULT_EDGE;
  return theme.edges[type] || theme.edges.default || DEFAULT_EDGE;
}

export { EMBEDDED as DEFAULT_THEME };
