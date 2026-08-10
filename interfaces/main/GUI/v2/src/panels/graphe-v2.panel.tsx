// graphe-v2.panel.tsx — PANEL V2 : graphe FSM construit par le BACKEND.
//
// V2 : la construction du graphe est déléguée à `agent_graph_utils` (Python),
// exposée via le service `utilities` (route graphe_utile/yaml_to_fsm). Le GUI
// ne fait que l'affichage : il reçoit un graphe_math (nœuds/arêtes, logique
// pure) + un thème, et gère le fold/placement côté vue.
//
// Ce panel est volontairement MINIMAL (pas de taskflow/boxed/yaml) — il sert
// de socle V2. L'agent YAML → FSM : route backend, logique pure.

import React, { useState, useEffect } from 'react';
import type { PanelDef } from './contract.ts';
import { GrapheSubPanel } from '../theme_graphe/graphe_subpanel.tsx';
import { useGraphThemeControl } from '../graphThemeStore.ts';

export function GrapheV2Panel({ ctx }: { ctx: any }) {
  const [agents, setAgents] = useState<any[]>([]);
  const [sel, setSel] = useState<string>('');
  const [graph, setGraph] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Liste des agents du catalogue.
  useEffect(() => {
    let alive = true;
    ctx.api.post('catalogue/agents/list', {}).then((res: any) => {
      if (!alive) return;
      const r = res?.result ?? {};
      setAgents(r.agents ?? []);
    }).catch(() => {});
    return () => { alive = false; };
  }, [ctx.api.post]);

  // Graphe FSM depuis le BACKEND (graphe_utile/yaml_to_fsm) — logique pure.
  useEffect(() => {
    if (!sel) { setGraph(null); return; }
    let alive = true;
    setLoading(true); setErr(null);
    ctx.api.post('graphe_utile/yaml_to_fsm', { name: sel }).then((res: any) => {
      if (!alive) return;
      const r = res?.result ?? res ?? {};
      setLoading(false);
      if (r.status === 'ok' && Array.isArray(r.nodes)) setGraph(r);
      else { setErr(r.error ?? 'graphe_utile/yaml_to_fsm a échoué'); setGraph(null); }
    }).catch((e: any) => {
      if (!alive) return;
      setLoading(false); setErr(String(e?.message ?? e)); setGraph(null);
    });
    return () => { alive = false; };
  }, [sel, ctx.api.post]);

  // Thème graphe (affichage uniquement — séparé du graphe_math).
  const gTheme = useGraphThemeControl(ctx.api.post);
  useEffect(() => { gTheme.ensureLoaded(); }, [gTheme]);

  return (
    <div style={{ height: '100%', display: 'flex', boxSizing: 'border-box', fontSize: 12 }}>
      {/* Catalogue d'agents */}
      <div style={{ width: 180, minWidth: 180, borderRight: '1px solid var(--mw-border, #1e293b)', overflow: 'auto', padding: 6 }}>
        <div style={{ fontWeight: 700, margin: '4px 0', color: '#a5b4fc' }}>
          Agents (V2) ({agents.length})
        </div>
        {agents.map((a) => (
          <div
            key={a.name}
            onClick={() => setSel(a.name)}
            style={{ padding: '3px 6px', cursor: 'pointer', borderRadius: 4, fontSize: 11,
                     background: sel === a.name ? 'rgba(56,189,248,.15)' : 'transparent',
                     color: sel === a.name ? '#38bdf8' : '#cbd5e1' }}
          >
            {a.name}
          </div>
        ))}
      </div>

      {/* Vue FSM (backend) */}
      <div style={{ flex: 1, minWidth: 0, padding: 6 }}>
        {err && <div style={{ color: '#f87171', marginBottom: 4, fontSize: 11 }}>⚠ {err}</div>}
        {!sel ? (
          <div style={{ color: '#475569', padding: 20, textAlign: 'center' }}>Sélectionnez un agent</div>
        ) : loading ? (
          <div style={{ color: '#475569', padding: 20, textAlign: 'center' }}>Chargement (graphe_utile)…</div>
        ) : graph ? (
          <GrapheSubPanel doc={graph} theme={gTheme.obj} editable={false} engine="reactflow" height="100%" />
        ) : (
          <div style={{ color: '#475569', padding: 20, textAlign: 'center' }}>Aucun graphe</div>
        )}
      </div>
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'graphe-v2',
  labelKey: 'panels.graphe-v2.titre',
  iconKey: 'panels.graphe-v2.titre',
  version: '0.1.0',
  essential: false,
  bundles: ['graphe'],
  paramsSchema: {},
  defaultParams: {},
  declaration: () => 'Graphe V2 — FSM construit par le backend (graphe_utile/yaml_to_fsm), logique pure.',
  component: (({ ctx }: any) => <GrapheV2Panel ctx={ctx} />) as any,
};
