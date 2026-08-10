// graphe-v2.panel.tsx — PANEL V2 : graphe construit par le BACKEND.
//
// V2 : la construction du graphe est déléguée à `agent_graph_utils` (Python),
// exposée via le service `utilities` (routes graphe_utile/yaml_to_fsm et
// graphe_utile/yaml_to_taskflow). Le GUI reçoit un graphe_math (logique pure)
// + un thème, et gère le fold/placement côté vue.
//
// Onglets : FSM (backend) / Taskflow (Pétri backend) / YAML / YAML inline.
// Boutons : reload/reset (re-demande le graphe), + ceux de GrapheSubPanel
// (algo, direction, Tout déplier/replier, Re-layout).

import React, { useState, useEffect, useCallback } from 'react';
import type { PanelDef } from './contract.ts';
import { GrapheSubPanel } from '../theme_graphe/graphe_subpanel.tsx';
import { useGraphThemeControl } from '../graphThemeStore.ts';

type View = 'fsm' | 'taskflow' | 'yaml' | 'yaml-inline';

/** Convertit le Pétri plat (num_id) en GraphDoc pour GrapheSubPanel. */
function petriToGraphDoc(petri: any): any {
  const nodes = (petri?.nodes ?? []).map((n: any) => ({
    id: String(n.num_id),
    type: n.class === 'transition' ? 'transition'
      : n.class === 'box' ? 'flow' : (n.type || 'place'),
    label: n.label ?? n.id,
    ref: n.id,
    tags: n.specialty ? [n.specialty] : [],
    vars: { visible: n.visible !== false, petri: n },
  }));
  const edges = (petri?.edges ?? [])
    .filter((e: any) => e.visible !== false)
    .map((e: any) => ({ from: String(e.from), to: String(e.to), label: e.label, type: e.type ?? 'next' }));
  return { id: `taskflow-${petri?.title ?? ''}`, title: `${petri?.title ?? ''} — taskflow`, nodes, edges };
}

export function GrapheV2Panel({ ctx }: { ctx: any }) {
  const [agents, setAgents] = useState<any[]>([]);
  const [sel, setSel] = useState<string>('');
  const [view, setView] = useState<View>('fsm');
  const [fsm, setFsm] = useState<any>(null);
  const [petri, setPetri] = useState<any>(null);
  const [yaml, setYaml] = useState('');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

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

  // Re-demande le graphe complet (reload/reset).
  const load = useCallback((name: string, v: View) => {
    if (!name) return;
    let alive = true;
    setLoading(true); setErr(null);
    const route = v === 'taskflow' ? 'graphe_utile/yaml_to_taskflow' : 'graphe_utile/yaml_to_fsm';
    ctx.api.post(route, { name }).then((res: any) => {
      if (!alive) return;
      const r = res?.result ?? res ?? {};
      setLoading(false);
      if (r.status === 'ok' && Array.isArray(r.nodes)) {
        if (v === 'taskflow') setPetri(r);
        else setFsm(r);
      } else {
        setErr(r.error ?? `${route} a échoué`); (v === 'taskflow' ? setPetri : setFsm)(null);
      }
    }).catch((e: any) => {
      if (!alive) return;
      setLoading(false); setErr(String(e?.message ?? e)); (v === 'taskflow' ? setPetri : setFsm)(null);
    });
    // YAML pour les vues texte (best-effort).
    if (v === 'yaml' || v === 'yaml-inline') {
      ctx.api.post('catalogue/agents/get', { name }).then((res: any) => {
        if (!alive) return;
        const r = res?.result ?? res ?? {};
        if (r.status === 'ok') setYaml(r.yaml ?? '');
      }).catch(() => {});
    }
    return () => { alive = false; };
  }, [ctx.api.post]);

  useEffect(() => { if (sel) load(sel, view); }, [sel, view, reloadKey, load]);

  // Thème graphe (affichage uniquement — séparé du graphe_math).
  const gTheme = useGraphThemeControl(ctx.api.post);
  useEffect(() => { gTheme.ensureLoaded(); }, [gTheme]);

  // Actions folding PÉTRI → BACKEND (graphe_utile/yaml_to_taskflow).
  const petriAction = useCallback((numId: number, action: 'fold' | 'unfold') => {
    if (!sel) return;
    setLoading(true); setErr(null);
    ctx.api.post('graphe_utile/yaml_to_taskflow', { name: sel, action, node: numId }).then((res: any) => {
      const r = res?.result ?? res ?? {};
      setLoading(false);
      if (r.status === 'ok' && Array.isArray(r.nodes)) setPetri(r);
      else setErr(r.error ?? 'action taskflow échouée');
    }).catch((e: any) => { setLoading(false); setErr(String(e?.message ?? e)); });
  }, [sel, ctx.api.post]);
  const petriFoldAll = useCallback(() => {
    if (!sel) return;
    setLoading(true); setErr(null);
    ctx.api.post('graphe_utile/yaml_to_taskflow', { name: sel, action: 'fold_all' }).then((res: any) => {
      const r = res?.result ?? res ?? {};
      setLoading(false);
      if (r.status === 'ok' && Array.isArray(r.nodes)) setPetri(r);
      else setErr(r.error ?? 'fold_all échoué');
    }).catch((e: any) => { setLoading(false); setErr(String(e?.message ?? e)); });
  }, [sel, ctx.api.post]);

  const shownGraph = view === 'taskflow' && petri ? petriToGraphDoc(petri) : fsm;
  const graphCount = (view === 'taskflow' ? petri?.nodes?.length : fsm?.nodes?.length) ?? 0;
  const counts = petri?.counts;

  return (
    <div style={{ height: '100%', display: 'flex', boxSizing: 'border-box', fontSize: 12 }}>
      {/* Catalogue d'agents */}
      <div style={{ width: 170, minWidth: 170, borderRight: '1px solid var(--mw-border, #1e293b)', overflow: 'auto', padding: 6 }}>
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

      <div style={{ flex: 1, minWidth: 0, padding: 6, display: 'flex', flexDirection: 'column' }}>
        {/* Onglets + reload */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {(['fsm', 'taskflow', 'yaml', 'yaml-inline'] as View[]).map((v) => (
            <button key={v} className="mw-btn" onClick={() => setView(v)}
              style={{ padding: '2px 10px', fontSize: 11, textTransform: 'capitalize',
                       opacity: view === v ? 1 : 0.5, borderColor: view === v ? '#38bdf8' : undefined,
                       color: view === v ? '#38bdf8' : undefined }}>
              {v === 'yaml-inline' ? 'YAML inline' : v}
            </button>
          ))}
          <span style={{ borderLeft: '1px solid #334155', margin: '0 4px' }} />
          <button className="mw-btn" onClick={() => setReloadKey(k => k + 1)} title="Re-demande le graphe complet"
            style={{ padding: '2px 8px', fontSize: 11 }}>⟳ Reload</button>
          {counts && <span style={{ fontSize: 10, color: '#64748b' }}>places={counts.places} trans={counts.transitions} arêtes={counts.edges}</span>}
          {graphCount > 0 && <span style={{ fontSize: 10, color: '#475569' }}>({graphCount} nœuds)</span>}
        </div>
        {err && <div style={{ color: '#f87171', marginBottom: 4, fontSize: 11 }}>⚠ {err}</div>}
        {!sel ? (
          <div style={{ color: '#475569', padding: 20, textAlign: 'center' }}>Sélectionnez un agent</div>
        ) : loading ? (
          <div style={{ color: '#475569', padding: 20, textAlign: 'center' }}>Chargement (graphe_utile)…</div>
        ) : view === 'yaml' || view === 'yaml-inline' ? (
          <pre style={{ flex: 1, minHeight: 0, overflow: 'auto', margin: 0, fontSize: 10, padding: 8, borderRadius: 6,
                        border: '1px solid var(--mw-border, #1e293b)', color: '#cbd5e1' }}>{yaml}</pre>
        ) : shownGraph ? (
          view === 'taskflow' ? (
            <GrapheSubPanel doc={shownGraph} theme={gTheme.obj} editable={false} engine="reactflow" height="100%"
              autoExpandAll
              petriMode
              onPetriAction={petriAction}
              onPetriFoldAll={petriFoldAll} />
          ) : (
            <GrapheSubPanel doc={shownGraph} theme={gTheme.obj} editable={false} engine="reactflow" height="100%" />
          )
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
  declaration: () => 'Graphe V2 — FSM/Pétri construits par le backend (graphe_utile), logique pure.',
  component: (({ ctx }: any) => <GrapheV2Panel ctx={ctx} />) as any,
};
