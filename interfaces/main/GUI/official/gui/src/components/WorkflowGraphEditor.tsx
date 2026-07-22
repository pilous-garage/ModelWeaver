import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import YAML from 'yaml';
import {
  ReactFlow, Background, Controls, MiniMap, Handle, Position,
  applyNodeChanges, type NodeChange, type NodeProps, type Connection,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  stepsToGraph, graphToSteps, layout, stepMeta, STEP_TYPES, isLoop, isTerminal,
  type Step, type StepType, type FsmNode,
} from '../lib/workflowGraph.ts';
import { loadPositions, savePositions, clearPositions } from '../lib/graphLayoutStore.ts';
import { NodeInspector } from './NodeInspector.tsx';
import { CATALOGUE_MIME } from './AgentLegoPanel.tsx';
import { daemonPost } from '../useSandbox.ts';

interface Props {
  docId: string;
  steps: Step[];
  onStepsChange?: (steps: Step[]) => void;
}

// ── Nœud FSM standard (expand/collapse si a des enfants) ─────────
function FsmNodeView({ id, data, selected }: NodeProps) {
  const step = (data as any).step as Step;
  const isEntry = (data as any).isEntry as boolean;
  const collapsed = (data as any).collapsed as boolean;
  const onToggle = (data as any).onToggleCollapse as ((id: string) => void) | undefined;
  const skillInfo = (data as any).skillInfo as any;
  const meta = stepMeta(step.type);
  const summary = summaryOf(step);
  const isEmpty = !step.type || step.type === 'group';
  const showSkill = !collapsed && skillInfo && step.type === 'call';
  return (
    <div style={{
      minWidth: 150, maxWidth: isEmpty && !collapsed ? 210 : 280,
      minHeight: isEmpty && !collapsed ? 80 : undefined,
      background: collapsed ? '#181825' : 'rgba(235,160,172,0.06)',
      border: `2px ${isEmpty || collapsed ? 'solid' : 'dashed'} ${selected ? '#f9e2af' : meta.color}`,
      borderRadius: 8, padding: collapsed ? '4px 6px' : 0, fontSize: 11, color: '#cdd6f4',
      boxShadow: isEntry ? '0 0 0 2px #a6e3a1' : undefined,
    }}>
      <Handle type="target" position={Position.Top} style={{ background: meta.color }} />
      <div style={{
        display: 'flex', alignItems: 'center', gap: 5,
        padding: collapsed ? 0 : '5px 8px', borderBottom: collapsed ? 'none' : `1px dashed ${meta.color}44`,
      }}>
        {onToggle && (
          <button
            onClick={(e) => { e.stopPropagation(); onToggle(id); }}
            style={{ background: 'transparent', border: 'none', color: meta.color, cursor: 'pointer', fontSize: 11, padding: 0 }}
            title={collapsed ? 'Déplier' : 'Replier'}
          >{collapsed ? '▸' : '▾'}</button>
        )}
        <span style={{ fontSize: 13 }}>{meta.icon}</span>
        <strong style={{ color: meta.color }}>{meta.label}</strong>
        {isEntry && <span style={{ marginLeft: 'auto', fontSize: 9, color: '#a6e3a1' }}>ENTRÉE</span>}
      </div>
      {(collapsed || !isEmpty || showSkill) && (
        <div style={{ padding: collapsed ? 0 : '4px 8px' }}>
          <div style={{ fontWeight: 600, marginTop: collapsed ? 2 : 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{step.id}</div>
          {summary && <div style={{ opacity: 0.55, fontSize: 10, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{summary}</div>}
          {showSkill && (
            <div style={{ marginTop: 4, borderTop: `1px solid ${meta.color}33`, paddingTop: 4, fontSize: 10, lineHeight: 1.5 }}>
              <div style={{ color: '#a6e3a1' }}>{skillInfo.description || ''}</div>
              {skillInfo.inputs && Object.keys(skillInfo.inputs).length > 0 && (
                <div style={{ marginTop: 3 }}>
                  <span style={{ opacity: 0.5 }}>inputs: </span>
                  {Object.entries(skillInfo.inputs).map(([k, v]: any) => (
                    <span key={k} style={{ color: '#89b4fa' }}>{k}{v?.required ? '*' : ''} </span>
                  ))}
                </div>
              )}
              <div style={{ opacity: 0.5, marginTop: 2 }}>
                {(skillInfo as any)?.implementation?.type || skillInfo.category || ''}
              </div>
            </div>
          )}
        </div>
      )}
      {!isTerminal(step.type) && <Handle type="source" position={Position.Bottom} style={{ background: meta.color }} />}
    </div>
  );
}

// ── Nœud boucle (boîte réductible) ──────────────────────────────
function LoopNodeView({ id, data, selected }: NodeProps) {
  const step = (data as any).step as Step;
  const isEntry = (data as any).isEntry as boolean;
  const collapsed = (data as any).collapsed as boolean;
  const onToggle = (data as any).onToggleCollapse as (id: string) => void;
  const meta = stepMeta(step.type);
  return (
    <div style={{
      width: '100%', height: '100%', boxSizing: 'border-box',
      background: 'rgba(235,160,172,0.06)',
      border: `2px dashed ${selected ? '#f9e2af' : meta.color}`,
      borderRadius: 10,
    }}>
      <Handle type="target" position={Position.Top} style={{ background: meta.color }} />
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px',
        borderBottom: collapsed ? 'none' : `1px dashed ${meta.color}55`, fontSize: 11, color: '#cdd6f4',
      }}>
        <button
          onClick={(e) => { e.stopPropagation(); onToggle?.(id); }}
          style={{ background: 'transparent', border: 'none', color: meta.color, cursor: 'pointer', fontSize: 11, padding: 0 }}
          title={collapsed ? 'Déplier' : 'Replier'}
        >{collapsed ? '▸' : '▾'}</button>
        <span style={{ fontSize: 13 }}>{meta.icon}</span>
        <strong style={{ color: meta.color }}>{meta.label}</strong>
        <span style={{ fontWeight: 600 }}>{step.id}</span>
        <span style={{ opacity: 0.55, fontSize: 10 }}>{loopSummary(step)}</span>
        {isEntry && <span style={{ marginLeft: 'auto', fontSize: 9, color: '#a6e3a1' }}>ENTRÉE</span>}
      </div>
      <Handle type="source" position={Position.Bottom} style={{ background: meta.color }} />
    </div>
  );
}

function loopSummary(step: Step): string {
  if (step.type === 'for') {
    if ('items' in step) return `${step.var || 'i'} ∈ ${step.items}`;
    return `${step.var || 'i'} : ${step.start ?? 0}→${step.end ?? 0}${step.step && step.step !== 1 ? ` /${step.step}` : ''}`;
  }
  if (step.type === 'while') {
    const c = step.condition || {};
    return `while ${c.variable || '?'} ${c.operator || 'TRUTHY'} ${c.value ?? ''}`.trim();
  }
  if (step.type === 'if') {
    const c = step.condition || {};
    return `if ${c.variable || '?'} ${c.operator || 'TRUTHY'} ${c.value ?? ''}`.trim();
  }
  if (step.type === 'group') return '';
  return '';
}

function summaryOf(step: Step): string {
  switch (step.type) {
    case 'llm_call': return (step.skill_prompt ? String(step.skill_prompt).slice(0, 40) : '') + ' 🧠';
    case 'call': return (step.fn || '') + (step.uses_llm ? ' 🧠' : '');
    case 'tool_call': return step.tool || '';
    case 'switch': return `var: ${step.variable || '?'}`;
    case 'set_variable': return `${step.name || '?'} = ${step.value ?? ''}`;
    case 'sleep': return `${step.duration_seconds ?? 60}s`;
    case 'spawn': return step.name || '';
    case 'handoff': return `→ ${step.to || '?'}`;
    case 'end': return step.status || 'SUCCESS';
    default: return '';
  }
}

const nodeTypes = { fsm: FsmNodeView, loop: LoopNodeView };

// react-flow exige que chaque parent précède ses enfants dans le tableau.
function orderParentsFirst(nodes: FsmNode[]): FsmNode[] {
  const byId = new Map(nodes.map(n => [n.id, n]));
  const out: FsmNode[] = [];
  const seen = new Set<string>();
  const visit = (n: FsmNode) => {
    if (seen.has(n.id)) return;
    const p = (n as any).parentId as string | undefined;
    if (p && byId.has(p) && !seen.has(p)) visit(byId.get(p)!);
    seen.add(n.id);
    out.push(n);
  };
  nodes.forEach(visit);
  return out;
}

// Descendants (récursif) d'un nœud via parentId.
function descendantsOf(nodes: FsmNode[], id: string): Set<string> {
  const out = new Set<string>();
  const walk = (pid: string) => {
    for (const n of nodes) {
      if ((n as any).parentId === pid && !out.has(n.id)) { out.add(n.id); walk(n.id); }
    }
  };
  walk(id);
  return out;
}

export function WorkflowGraphEditor({ docId, steps, onStepsChange }: Props) {
  const [nodes, setNodes] = useState<FsmNode[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [direction, setDirection] = useState<'TB' | 'LR'>('TB');
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const wrapRef = useRef<HTMLDivElement>(null);
  const idCounter = useRef(0);
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;

  // ── Init depuis les steps (une fois par docId) ──
  useEffect(() => {
    const { nodes: rawNodes, edges } = stepsToGraph(steps);
    const saved = loadPositions(docId);
    const hasAllSaved = rawNodes.every(n => saved[n.id]);
    const positioned = hasAllSaved
      ? rawNodes.map(n => ({ ...n, position: saved[n.id], ...(n.type === 'loop' && saved[`${n.id}::size`] ? { style: { ...(n.style || {}), width: (saved as any)[`${n.id}::size`].x, height: (saved as any)[`${n.id}::size`].y } } : {}) }))
      : layout(rawNodes, edges, direction);
    setNodes(positioned);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  const [skillInfo, setSkillInfo] = useState<Record<string, any>>({});
  const fetchQueue = useRef<Set<string>>(new Set());
  const fetchedNodes = useRef<Set<string>>(new Set());

  // Remonte la chaîne des parentId pour détecter un cycle (même fn appelée 2× dans la chaîne)
  const callChainHasFn = (nodeId: string | undefined, targetFn: string, nodeList?: FsmNode[]): boolean => {
    const list = nodeList || nodesRef.current;
    let cur = nodeId;
    while (cur) {
      const n = list.find(x => x.id === cur);
      if (!n) break;
      const st = n.data?.step;
      if (st?.type === 'call' && st?.fn === targetFn) return true;
      cur = (n as any).parentId as string | undefined;
    }
    return false;
  };

  const toggleCollapse = useCallback((id: string) => {
    setCollapsed(prev => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }, []);

  // Extrait les steps d'un doc YAML
  const extractSteps = (doc: any): any[] => {
    if (doc?.workflow?.steps) return doc.workflow.steps;
    if (doc?.body?.steps) return doc.body.steps;
    if (Array.isArray(doc?.body)) return doc.body;
    if (Array.isArray(doc?.steps)) return doc.steps;
    return [];
  };

  // Injecte les steps enfants d'un skill dans le graphe
  const injectSkillChildren = useCallback(async (parentId: string, fn: string) => {
    if (fetchQueue.current.has(fn)) return;
    fetchQueue.current.add(fn);
    try {
      const res = await daemonPost('catalogue/skills/get', { name: fn });
      if (!res?.yaml) { console.warn(`injectSkillChildren: pas de yaml pour ${fn}`, res); setSkillInfo(p => ({ ...p, [fn]: res?.skill || true })); return; }
      const doc = YAML.parse(res.yaml) || {};
      const innerSteps = extractSteps(doc);
      console.log(`injectSkillChildren: ${fn} → ${innerSteps.length} steps`);
      if (innerSteps.length === 0) { setSkillInfo(p => ({ ...p, [fn]: res.skill || true })); return; }
      const childNodes: FsmNode[] = innerSteps.map((st: any, i: number) => ({
        id: `${parentId}__${st.id || `step_${i}`}`,
        type: 'fsm',
        position: { x: 20, y: 30 + i * 80 },
        data: { step: { id: `${parentId}__${st.id || `step_${i}`}`, type: st.type || 'call', ...st } },
        parentId,
        extent: 'parent' as const,
      }));
      // Dimensionner le parent pour contenir ses enfants
      const nRows = childNodes.length;
      const parentW = 280;
      const parentH = Math.max(80, 44 + nRows * 80 + 20);
      setNodes(prev => prev.map(n => n.id === parentId
        ? { ...n, style: { ...(n.style || {}), width: parentW, height: parentH } }
        : n
      ).concat(childNodes));
      setSkillInfo(p => ({ ...p, [fn]: res.skill || true }));
    } catch(e) { console.error(`injectSkillChildren: erreur ${fn}`, e); }
  }, []);

  const expandAll = useCallback(async () => {
    const currentNodes = nodesRef.current;
    const batch = currentNodes
      .filter(n => {
        const st = n.data?.step;
        return st?.type === 'call' && !!st?.fn && !fetchedNodes.current.has(n.id);
      })
      .map(n => n.id);
    console.log('expandAll: batch', batch, 'collapsed size', collapsed.size, 'total nodes', currentNodes.length);
    if (batch.length === 0) return console.log('expandAll: rien à déplier / déjà fetch');
    setCollapsed(new Set());

    const process = async (items: string[], depth: number) => {
      if (depth > 5) { console.log('expandAll: profondeur max atteinte'); return; }
      for (const id of items) {
        const nd = nodesRef.current.find(x => x.id === id);
        const st = nd?.data?.step;
        if (st?.type !== 'call' || !st?.fn) continue;
        if (fetchedNodes.current.has(id)) { console.log(`expandAll: déjà fetch ${id}`); continue; }
        if (callChainHasFn((nd as any).parentId, st.fn, nodesRef.current)) { console.log(`expandAll: cycle détecté pour ${id} (${st.fn})`); continue; }
        fetchedNodes.current.add(id);
        console.log(`expandAll: expand ${id} → ${st.fn} (depth ${depth})`);
        await injectSkillChildren(id, st.fn);
      }
      // Chercher les nouveaux enfants call dans l'état frais
      const freshNodes = nodesRef.current;
      const nextGen = freshNodes
        .filter(x => x.parentId && items.includes(x.parentId) && x.data?.step?.type === 'call' && !!x.data?.step?.fn)
        .map(x => x.id)
        .filter(id => !fetchedNodes.current.has(id) && !callChainHasFn(freshNodes.find(n => n.id === id)?.parentId, freshNodes.find(n => n.id === id)?.data?.step?.fn, freshNodes));
      if (nextGen.length > 0) { console.log(`expandAll: nextGen ${nextGen.length} (depth ${depth + 1})`, nextGen); await process(nextGen, depth + 1); }
    };
    await process(batch, 0);
    console.log('expandAll: terminé, total nodes', nodesRef.current.length);
  }, [collapsed, injectSkillChildren]);

  // Quand un nœud call est déplié pour la 1re fois, fetch le YAML du skill et injecte ses steps
  useEffect(() => {
    const currentNodes = nodesRef.current;
    for (const n of currentNodes) {
      const step = n.data?.step;
      if (step?.type !== 'call' || !step?.fn) continue;
      if (collapsed.has(n.id)) continue;
      if (fetchedNodes.current.has(n.id)) continue;
      // Protection cycle : vérifie qu'aucun ancêtre n'appelle déjà la même fn
      if (callChainHasFn((n as any).parentId, step.fn, currentNodes)) continue;
      fetchedNodes.current.add(n.id);

      const fn = step.fn;
      if (fetchQueue.current.has(fn)) continue;
      fetchQueue.current.add(fn);

      daemonPost('catalogue/skills/get', { name: fn })
        .then(res => {
          if (!res?.yaml) { setSkillInfo(p => ({ ...p, [fn]: res?.skill || true })); return; }
          const doc = YAML.parse(res.yaml) || {};
          const innerSteps = extractSteps(doc);
          if (innerSteps.length === 0) {
            setSkillInfo(p => ({ ...p, [fn]: res.skill || true }));
            return;
          }
          const childNodes: FsmNode[] = innerSteps.map((st: any, i: number) => ({
            id: `${n.id}__${st.id || `step_${i}`}`,
            type: 'fsm',
            position: { x: 20, y: 30 + i * 80 },
            data: { step: { id: `${n.id}__${st.id || `step_${i}`}`, type: st.type || 'call', ...st } },
            parentId: n.id,
            extent: 'parent' as const,
          }));
          setNodes(prev => [...prev, ...childNodes]);
          setSkillInfo(p => ({ ...p, [fn]: res.skill || true }));
        })
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, collapsed]);

  // ── Nœuds masqués (descendants d'une boucle repliée) ──
  const hidden = useMemo(() => {
    const h = new Set<string>();
    for (const id of collapsed) for (const d of descendantsOf(nodes, id)) h.add(d);
    return h;
  }, [collapsed, nodes]);

  // ── Nœuds rendus (injecte collapse + masque, ajuste taille repliée) ──
  const renderNodes = useMemo(() => nodes
    .filter(n => !hidden.has(n.id))
    .map(n => {
      const isC = collapsed.has(n.id);
      const st = n.data?.step?.type;
      const isAnyContainer = n.type === 'loop' || (st && ['if', 'group', 'for', 'while', 'call', 'agent_call', 'llm_call'].includes(st));
      const step = n.data?.step as any;
      const info = step?.type === 'call' && step?.fn ? skillInfo[step.fn] : undefined;
      // Taille minimale pour les conteneurs dépliés qui ont des enfants
      const hasChildren = nodes.some(x => (x as any).parentId === n.id);
      const existW = n.style && 'width' in n.style ? Number((n.style as any).width) : 0;
      const existH = n.style && 'height' in n.style ? Number((n.style as any).height) : 0;
      const minW = existW || (isC ? 220 : hasChildren ? 280 : 0);
      const minH = existH || (isC ? 44 : hasChildren ? 120 : 0);
      const style = minW ? { ...(n.style || {}), width: minW, height: minH } : n.style;
      return {
        ...n,
        data: { ...n.data, collapsed: isC, onToggleCollapse: isAnyContainer ? toggleCollapse : undefined, skillInfo: info },
        style: isC ? { ...(style || {}), width: 220, height: 44 } : style,
      };
    }), [nodes, hidden, collapsed, toggleCollapse, skillInfo]);

  // ── Arêtes dérivées (masque celles touchant un nœud caché) ──
  const edges: Edge[] = useMemo(() => {
    const curSteps = graphToSteps(nodes);
    return stepsToGraph(curSteps).edges.filter(e => !hidden.has(e.source) && !hidden.has(e.target));
  }, [nodes, hidden]);

  const selectedStep = nodes.find(n => n.id === selectedId)?.data.step ?? null;
  const nodeIds = nodes.map(n => n.id);
  const loopIds = nodes.filter(n => n.type === 'loop').map(n => n.id);

  // ── Commit : remonte les steps + sauve positions/tailles ──
  const commit = useCallback((raw: FsmNode[]) => {
    if (!onStepsChange) return;
    const next = orderParentsFirst(raw);
    setNodes(next);
    onStepsChange(graphToSteps(next));
    const positions: Record<string, { x: number; y: number }> = {};
    next.forEach(n => {
      positions[n.id] = n.position;
      if (n.type === 'loop' && n.style) positions[`${n.id}::size`] = { x: Number(n.style.width) || 220, y: Number(n.style.height) || 120 };
    });
    savePositions(docId, positions);
  }, [docId, onStepsChange]);

  const patchStep = useCallback((id: string, patch: Partial<Step>) => {
    commit(nodes.map(n => n.id === id ? { ...n, data: { ...n.data, step: { ...n.data.step, ...patch } } } : n));
  }, [nodes, commit]);

  const renameId = useCallback((oldId: string, newId: string) => {
    if (!newId || newId === oldId || nodes.some(n => n.id === newId)) return;
    const remap = (v: any) => (v === oldId ? newId : v);
    const next = nodes.map(n => {
      const st = { ...n.data.step };
      st.next = remap(st.next); st.default = remap(st.default); st.on_error = remap(st.on_error);
      if (st.conditions) st.conditions = st.conditions.map((c: any) => ({ ...c, next: remap(c.next) }));
      let node: any = { ...n, data: { ...n.data, step: st } };
      if ((n as any).parentId === oldId) node.parentId = newId;
      if (n.id === oldId) { st.id = newId; node = { ...node, id: newId }; }
      return node;
    });
    commit(next);
    setSelectedId(newId);
  }, [nodes, commit]);

  const addNode = useCallback((type: StepType, pos?: { x: number; y: number }, extra?: Partial<Step>, parentId?: string) => {
    let base = `${type}_${++idCounter.current}`;
    while (nodes.some(n => n.id === base)) base = `${type}_${++idCounter.current}`;
    const step: Step = { id: base, type, ...defaultsFor(type), ...extra };
    const position = pos || { x: 60 + nodes.length * 24, y: 60 + nodes.length * 24 };
    const node: FsmNode = {
      id: base, type: isLoop(type) ? 'loop' : 'fsm', position, data: { step },
      ...(isLoop(type) ? { style: { width: 260, height: 120 } } : {}),
      ...(parentId ? { parentId, extent: 'parent' as const } : {}),
    };
    commit([...nodes, node]);
    setSelectedId(base);
  }, [nodes, commit]);

  const deleteNode = useCallback((id: string) => {
    const kill = new Set([id, ...descendantsOf(nodes, id)]);
    const clear = (v: any) => (kill.has(v) ? undefined : v);
    const next = nodes.filter(n => !kill.has(n.id)).map(n => {
      const st = { ...n.data.step };
      st.next = clear(st.next); st.default = clear(st.default); st.on_error = clear(st.on_error);
      if (st.conditions) st.conditions = st.conditions.map((c: any) => kill.has(c.next) ? { ...c, next: undefined } : c);
      return { ...n, data: { ...n.data, step: st } };
    });
    commit(next);
    if (kill.has(selectedId || '')) setSelectedId(null);
  }, [nodes, commit, selectedId]);

  // Déplace un nœud dans une boucle (ou à la racine).
  const setParent = useCallback((id: string, parentId: string | undefined) => {
    if (id === parentId) return;
    if (parentId && descendantsOf(nodes, id).has(parentId)) return; // pas de cycle
    const next = nodes.map(n => {
      if (n.id !== id) return n;
      const node: any = { ...n, position: { x: 20, y: 50 } };
      if (parentId) { node.parentId = parentId; node.extent = 'parent'; }
      else { delete node.parentId; delete node.extent; }
      return node;
    });
    commit(next);
  }, [nodes, commit]);

  // ── Interactions react-flow ──
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const applied = applyNodeChanges(changes, nodes) as FsmNode[];
    setNodes(applied);
    if (changes.some(c => c.type === 'position' && (c as any).dragging === false)) {
      const positions: Record<string, { x: number; y: number }> = {};
      applied.forEach(n => { positions[n.id] = n.position; });
      savePositions(docId, positions);
    }
  }, [nodes, docId]);

  const onConnect = useCallback((c: Connection) => {
    if (!c.source || !c.target) return;
    const src = nodes.find(n => n.id === c.source);
    if (!src) return;
    const st = src.data.step;
    if (st.type === 'switch') {
      if (!st.default) patchStep(c.source, { default: c.target });
      else patchStep(c.source, { conditions: [...(st.conditions || []), { operator: 'EQUALS', value: '', next: c.target }] });
    } else {
      patchStep(c.source, { next: c.target });
    }
  }, [nodes, patchStep]);

  const onEdgesDelete = useCallback((deleted: Edge[]) => {
    let next = nodes;
    for (const e of deleted) {
      const kind = (e.data as any)?.kind as string;
      next = next.map(n => {
        if (n.id !== e.source) return n;
        const st = { ...n.data.step };
        if (kind === 'next') st.next = undefined;
        else if (kind === 'default') st.default = undefined;
        else if (kind === 'on_error') st.on_error = undefined;
        else if (kind?.startsWith('cond')) {
          const i = Number(kind.slice(4));
          st.conditions = (st.conditions || []).filter((_: any, j: number) => j !== i);
        }
        return { ...n, data: { ...n.data, step: st } };
      });
    }
    commit(next);
  }, [nodes, commit]);

  const relayout = useCallback((dir: 'TB' | 'LR') => {
    setDirection(dir);
    const laid = layout(nodes, edges, dir);
    commit(laid);
  }, [nodes, edges, commit]);

  const resetLayout = useCallback(() => {
    clearPositions(docId);
    relayout(direction);
  }, [docId, direction, relayout]);

  // ── Drop depuis le catalogue (skill → nœud call, dans une boucle si survolée) ──
  const onDrop = useCallback((e: React.DragEvent) => {
    const raw = e.dataTransfer.getData(CATALOGUE_MIME);
    if (!raw) return;
    e.preventDefault();
    try {
      const p = JSON.parse(raw) as { type: string; name: string; uses_llm?: boolean };
      if (p.type !== 'skills') return;
      const rect = wrapRef.current?.getBoundingClientRect();
      const abs = rect ? { x: e.clientX - rect.left, y: e.clientY - rect.top } : { x: 60, y: 60 };
      // Boucle survolée (dépliée) = conteneur
      let parentId: string | undefined;
      for (const n of nodes) {
        if (n.type !== 'loop' || collapsed.has(n.id) || (n as any).parentId) continue;
        const w = Number(n.style?.width) || 260, h = Number(n.style?.height) || 120;
        if (abs.x >= n.position.x && abs.x <= n.position.x + w && abs.y >= n.position.y && abs.y <= n.position.y + h) {
          parentId = n.id; break;
        }
      }
      const pos = parentId ? { x: 20, y: 50 } : { x: abs.x - 75, y: abs.y - 30 };
      addNode('call', pos, { fn: p.name, uses_llm: p.uses_llm }, parentId);
    } catch { /* ignore */ }
  }, [addNode, nodes, collapsed]);

  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0, minWidth: 0 }}>
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, flex: 1 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, padding: 6, borderBottom: '1px solid #45475a', alignItems: 'center' }}>
          <span style={{ fontSize: 11, opacity: 0.6, marginRight: 2 }}>+ nœud :</span>
          {STEP_TYPES.map(t => {
            const m = stepMeta(t);
            return (
              <button key={t} onClick={() => addNode(t)} title={t} style={{
                background: '#181825', color: m.color, border: `1px solid ${m.color}44`,
                borderRadius: 5, padding: '3px 7px', cursor: 'pointer', fontSize: 11,
              }}>{m.icon} {m.label}</button>
            );
          })}
          <div style={{ flex: 1 }} />
          <button onClick={expandAll} style={{ ...toolBtn, background: '#a6e3a122', color: '#a6e3a1' }} title="Tout déplier (récursif, max 5 niveaux)">
            ⤢ Tout déplier
          </button>
          <button onClick={() => relayout(direction === 'TB' ? 'LR' : 'TB')} style={{ ...toolBtn, background: '#89b4fa22', color: '#89b4fa', fontWeight: 600 }} title="Auto-organiser le graphe">
            {direction === 'TB' ? '⬇' : '➡'} Auto
          </button>
        </div>
        <div ref={wrapRef} style={{ flex: 1, minHeight: 0 }}
          onDragOver={e => { if (e.dataTransfer.types.includes(CATALOGUE_MIME)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } }}
          onDrop={onDrop}>
          <ReactFlow
            nodes={renderNodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onConnect={onConnect}
            onEdgesDelete={onEdgesDelete}
            onNodeClick={(_, n) => setSelectedId(n.id)}
            onPaneClick={() => setSelectedId(null)}
            fitView
            deleteKeyCode={null}
            colorMode="dark"
          >
            <Background color="#313244" gap={16} />
            <Controls />
            <MiniMap nodeColor={n => stepMeta((n.data as any)?.step?.type).color} maskColor="rgba(0,0,0,0.5)" style={{ background: '#181825' }} />
          </ReactFlow>
        </div>
      </div>
      <div style={{ width: 260, flexShrink: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <NodeInspector
          step={selectedStep}
          nodeIds={nodeIds}
          parentId={(nodes.find(n => n.id === selectedId) as any)?.parentId}
          loopIds={loopIds.filter(lid => lid !== selectedId && !descendantsOf(nodes, selectedId || '').has(lid))}
          onChange={patch => selectedId && patchStep(selectedId, patch)}
          onRenameId={newId => selectedId && renameId(selectedId, newId)}
          onSetParent={pid => selectedId && setParent(selectedId, pid)}
          onDelete={() => selectedId && deleteNode(selectedId)}
        />
      </div>
    </div>
  );
}

function defaultsFor(type: StepType): Partial<Step> {
  switch (type) {
    case 'llm_call': return { skill_prompt: '', output_capture: '_reply', next: undefined };
    case 'call': return { fn: '', inputs: {}, capture: '', uses_llm: false };
    case 'tool_call': return { tool: '', args: {} };
    case 'switch': return { variable: '', conditions: [], default: undefined };
    case 'set_variable': return { name: '', value: '' };
    case 'for': return { var: 'i', start: 0, end: 3, step: 1 };
    case 'while': return { condition: { variable: '', operator: 'TRUTHY' } };
    case 'sleep': return { duration_seconds: 60 };
    case 'spawn': return { name: '', role: 'spawned', occupation: 'disparate', request: '' };
    case 'handoff': return { to: '' };
    case 'end': return { status: 'SUCCESS' };
    case 'group': return { next: undefined };
    case 'if': return { condition: { variable: '', operator: 'TRUTHY' }, next: undefined };
    case 'agent_call': return { agent: '', entrypoint: 'main', inputs: {}, capture: {}, next: undefined };
    default: return {};
  }
}

const toolBtn: React.CSSProperties = {
  background: '#313244', color: '#cdd6f4', border: 'none',
  borderRadius: 5, padding: '3px 8px', cursor: 'pointer', fontSize: 11,
};
