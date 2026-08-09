// agent/taskflow — graphe du taskflow d'un agent (rôles → consomme/génère)
// avec dépliage : steps de l'entrypoint (routes de consommation/dépense).
// Source : agent/taskflow (build_taskflow). Sélecteur d'agent + dépliage.

import React, { useState } from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  agent-taskflow:
    titre: "Taskflow agent"
    agent: "Agent"
    consomme: "Consomme"
    genere: "Génère"
    etapes: "Étapes"
    deplier: "Déplier"
    replier: "Replier"
    erreur: "Erreur"
    vide: "Aucune donnée"
`;

const LANG_EN = `
panels:
  agent-taskflow:
    titre: "Agent taskflow"
    agent: "Agent"
    consomme: "Consumes"
    genere: "Generates"
    etapes: "Steps"
    deplier: "Expand"
    replier: "Collapse"
    erreur: "Error"
    vide: "No data"
`;


function AgentTaskflowPanel({ ctx }: { ctx: any }) {
  const { data, error } = usePoll<any>(
    ctx.api.post, 'agent/taskflow', {}, 8000,
    (res) => res?.result ?? {}, true,
  );
  const nodes: any[] = data?.nodes ?? [];
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.agent-taskflow.erreur') ?? 'Erreur'} : {error}</div>}
      {nodes.length === 0 && <div style={{ color: '#475569' }}>{ctx.t?.('panels.agent-taskflow.vide') ?? 'Aucune donnée'}</div>}
      {nodes.map((n: any) => {
        const isOpen = expanded === n.id;
        return (
          <div key={n.id} style={{ marginBottom: 6, border: '1px solid var(--mw-border, #1e293b)', borderRadius: 6 }}>
            {/* En-tête du rôle */}
            <div
              onClick={() => setExpanded(isOpen ? null : n.id)}
              style={{ display: 'flex', gap: 8, padding: '6px 8px', cursor: 'pointer', alignItems: 'center', background: 'rgba(148,163,184,.06)' }}
            >
              <span style={{ fontWeight: 700 }}>{n.label ?? n.id}</span>
              <span style={{ color: '#64748b', fontSize: 11 }}>({n.agents?.length ?? 0} agents)</span>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 11, color: '#38bdf8' }}>{isOpen ? (ctx.t?.('panels.agent-taskflow.replier') ?? 'Replier') : (ctx.t?.('panels.agent-taskflow.deplier') ?? 'Déplier')}</span>
            </div>
            {/* Contenu déplié */}
            {isOpen && (
              <div style={{ padding: '6px 8px' }}>
                {/* Consommation */}
                <div style={{ fontSize: 11, color: '#fbbf24', fontWeight: 600 }}>{ctx.t?.('panels.agent-taskflow.consomme') ?? 'Consomme'}</div>
                {(n.consumes ?? []).length === 0 && <div style={{ color: '#475569', fontSize: 11 }}>—</div>}
                {(n.consumes ?? []).map((c: string) => (
                  <div key={c} style={{ fontSize: 11, color: '#cbd5e1', paddingLeft: 8 }}>→ {c}</div>
                ))}
                {/* Génération */}
                <div style={{ fontSize: 11, color: '#4ade80', fontWeight: 600, marginTop: 6 }}>{ctx.t?.('panels.agent-taskflow.genere') ?? 'Génère'}</div>
                {(n.generates ?? []).length === 0 && <div style={{ color: '#475569', fontSize: 11 }}>—</div>}
                {(n.generates ?? []).map((g: string) => (
                  <div key={g} style={{ fontSize: 11, color: '#cbd5e1', paddingLeft: 8 }}>← {g}</div>
                ))}
                {/* Étapes (si le backend les fournit) */}
                {(n.steps ?? []).length > 0 && (
                  <>
                    <div style={{ fontSize: 11, color: '#a5b4fc', fontWeight: 600, marginTop: 6 }}>{ctx.t?.('panels.agent-taskflow.etapes') ?? 'Étapes'}</div>
                    {(n.steps ?? []).map((s: any) => (
                      <div key={s.id} style={{ fontSize: 11, color: '#94a3b8', paddingLeft: 8 }}>
                        <span style={{ fontFamily: 'monospace' }}>{s.id}</span> — {s.fn ?? ''}
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'agent-taskflow',
  labelKey: 'panels.agent-taskflow.titre',
  iconKey: 'panels.agent-taskflow.titre',
  version: '0.1.0',
  essential: false,
  bundles: ['graphe'],
  paramsSchema: {},
  defaultParams: {},
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[agent-taskflow] Graphe taskflow d\'un agent (dépliage des steps)',
  component: AgentTaskflowPanel,
};

export const langFr = LANG_FR;
