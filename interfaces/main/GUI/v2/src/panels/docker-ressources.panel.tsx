// gestion/docker-ressources — caches docker, conteneurs, fork, tests. Migré de V1.
// Routes : docker/caches/list, docker/status, docker/cache/create, docker/fork,
//          docker/run-tests, docker/snapshot, docker/release.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  docker-ressources:
    titre: "Ressources Docker"
    caches: "Caches"
    conteneurs: "Conteneurs"
    creer: "Créer un cache"
    fork: "Fork"
    tests: "Tests"
    snapshot: "Snapshot"
    release: "Libérer"
    nom: "Nom"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  docker-ressources:
    titre: "Resources Docker"
    caches: "Caches"
    conteneurs: "Conteneurs"
    creer: "Créer un cache"
    fork: "Fork"
    tests: "Tests"
    snapshot: "Snapshot"
    release: "Libérer"
    nom: "Name"
    erreur: "Error"
`;


function DockerPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data: caches, reload: reloadCaches } = usePoll<any>(
    ctx.api.post, 'docker/caches/list', {}, 5000,
    (res) => res?.result?.caches ?? [], true,
  );
  const { data: status } = usePoll<any>(
    ctx.api.post, 'docker/status', {}, 5000,
    (res) => res?.result?.containers ?? {}, true,
  );
  const [newName, setNewName] = React.useState('');

  const act = async (route: string, body: any) => {
    try { await ctx.api.post(route, body); reloadCaches(); } catch { /* best-effort */ }
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8 }}>
        <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder={ctx.t?.('panels.docker-ressources.nom') ?? 'Nom'}
          style={{ flex: 1, background: 'var(--mw-bg, #0f172a)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6, padding: '4px 8px', color: 'var(--mw-fg, #e2e8f0)', fontSize: 12 }} />
        <button className="mw-btn" onClick={() => newName && act('docker/cache/create', { name: newName })}>{ctx.t?.('panels.docker-ressources.creer') ?? 'Créer'}</button>
      </div>
      <div style={{ fontWeight: 600, margin: '6px 0 4px' }}>{ctx.t?.('panels.docker-ressources.caches') ?? 'Caches'}</div>
      {(caches ?? []).map((c: any, i: number) => (
        <div key={c.name ?? i} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
          <span style={{ flex: 1, fontWeight: 600 }}>{c.name}</span>
          <span style={{ color: '#94a3b8' }}>{c.image ?? ''}</span>
          <button className="mw-btn" onClick={() => act('docker/fork', { cache: c.name })} style={{ fontSize: 11 }}>{ctx.t?.('panels.docker-ressources.fork') ?? 'Fork'}</button>
        </div>
      ))}
      <div style={{ fontWeight: 600, margin: '8px 0 4px' }}>{ctx.t?.('panels.docker-ressources.conteneurs') ?? 'Conteneurs'}</div>
      {Object.entries(status ?? {}).map(([name, v]: [string, any]) => (
        <div key={name} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
          <span style={{ flex: 1 }}>{name}</span>
          <span style={{ color: v?.status === 'running' ? '#4ade80' : '#94a3b8' }}>{v?.status ?? ''}</span>
          <button className="mw-btn" onClick={() => act('docker/snapshot', { container: name })} style={{ fontSize: 11 }}>{ctx.t?.('panels.docker-ressources.snapshot') ?? 'Snapshot'}</button>
          <button className="mw-btn" onClick={() => act('docker/release', { container: name })} style={{ fontSize: 11 }}>{ctx.t?.('panels.docker-ressources.release') ?? 'Libérer'}</button>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'docker-ressources',
  labelKey: 'panels.docker-ressources.titre',
  iconKey: 'panels.docker-ressources.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['monitoring', 'docker'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[docker-ressources] Ressources Docker v1.0.0\n  routes: docker/caches/list, docker/status, docker/cache/create, docker/fork, docker/snapshot, docker/release',
  component: DockerPanel,
};

export const langFr = LANG_FR;