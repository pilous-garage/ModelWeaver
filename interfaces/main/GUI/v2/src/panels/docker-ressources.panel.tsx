// gestion/docker-ressources — caches docker (préparateurs) + conteneurs
// forkés (testeurs/codeurs). Migré de V1.
// Routes : docker/caches/list, docker/status, docker/cache/create,
//          docker/cache/register, docker/fork, docker/run-tests,
//          docker/snapshot, docker/release.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  docker-ressources:
    titre: "Ressources Docker"
    preparateurs: "Préparateurs — caches prêts"
    testeurs: "Testeurs / codeurs — copies en cours"
    creer: "Créer un cache"
    ajouter: "Enregistrer"
    fork: "Créer une copie"
    tests: "Tests"
    snapshot: "Snapshot"
    release: "Libérer"
    nom: "Nom"
    base: "Image de base"
    outils: "Outils (ex: pytest git)"
    erreur: "Erreur"
    running: "running"
`;

const LANG_EN = `
panels:
  docker-ressources:
    titre: "Resources Docker"
    preparateurs: "Preparers — ready caches"
    testeurs: "Testers / coders — working copies"
    creer: "Create cache"
    ajouter: "Register"
    fork: "Fork a copy"
    tests: "Tests"
    snapshot: "Snapshot"
    release: "Release"
    nom: "Name"
    base: "Base image"
    outils: "Tools (e.g. pytest git)"
    erreur: "Error"
    running: "running"
`;


function DockerPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data: caches, error: cachesErr, reload: reloadCaches } = usePoll<any>(
    ctx.api.post, 'docker/caches/list', {}, 5000,
    (res) => res?.result?.caches ?? [], true,
  );
  const { data: status, error: statusErr, reload: reloadStatus } = usePoll<any>(
    ctx.api.post, 'docker/status', {}, 5000,
    (res) => res?.result?.containers ?? {}, true,
  );
  const [newName, setNewName] = React.useState('');
  const [newBase, setNewBase] = React.useState('python:3.12-slim');
  const [newTools, setNewTools] = React.useState('');
  const [lastMsg, setLastMsg] = React.useState<{ ok: boolean; text: string } | null>(null);

  const act = async (route: string, body: any) => {
    try {
      const res = await ctx.api.post(route, body);
      const r = res?.result ?? res ?? {};
      if (r?.ok === false || r?.status === 'error') {
        setLastMsg({ ok: false, text: `${route} : ${r?.error ?? JSON.stringify(r)}` });
      } else {
        setLastMsg({ ok: true, text: `${route} : ok` });
      }
    } catch (e: any) {
      setLastMsg({ ok: false, text: `${route} : ${String(e?.message ?? e)}` });
    }
    reloadCaches();
    reloadStatus();
  };

  const containers = Object.entries(status ?? {}).map(([name, v]: [string, any]) => ({ name, ...(v ?? {}) }));

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {lastMsg ? (
        <div style={{ fontSize: 11, padding: '4px 8px', borderRadius: 6, marginBottom: 6, background: lastMsg.ok ? '#052e16' : '#450a0a', color: lastMsg.ok ? '#4ade80' : '#f87171' }}>
          {lastMsg.text}
        </div>
      ) : null}
      {(cachesErr || statusErr) ? (
        <div style={{ fontSize: 11, color: '#f87171', marginBottom: 6 }}>{(cachesErr || statusErr)}</div>
      ) : null}

      {/* ── Préparateurs : créer/enregister un cache prêt ── */}
      <div style={{ fontWeight: 700, margin: '2px 0 4px', color: '#a5b4fc' }}>{ctx.t?.('panels.docker-ressources.preparateurs') ?? 'Préparateurs'}</div>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
        <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder={ctx.t?.('panels.docker-ressources.nom') ?? 'Nom'}
          style={{ flex: 1, minWidth: 70, background: 'var(--mw-bg, #0f172a)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6, padding: '4px 8px', color: 'var(--mw-fg, #e2e8f0)', fontSize: 12 }} />
        <input value={newBase} onChange={(e) => setNewBase(e.target.value)} placeholder={ctx.t?.('panels.docker-ressources.base') ?? 'Image de base'}
          style={{ width: 90, background: 'var(--mw-bg, #0f172a)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6, padding: '4px 8px', color: 'var(--mw-fg, #e2e8f0)', fontSize: 12 }} />
        <button className="mw-btn" onClick={() => newName && act('docker/cache/create', { name: newName, base: newBase || 'python:3.12-slim', tools: (newTools || '').split(/[\s,]+/).filter(Boolean) })}>{ctx.t?.('panels.docker-ressources.creer') ?? 'Créer'}</button>
        <button className="mw-btn" onClick={() => newName && newBase && act('docker/cache/register', { name: newName, image: newBase })} style={{ fontSize: 11 }}>{ctx.t?.('panels.docker-ressources.ajouter') ?? 'Enregistrer'}</button>
      </div>
      <p style={{ margin: '0 0 4px', fontSize: 11, color: '#94a3b8' }}>
        <span style={{ color: '#64748b' }}>{ctx.t?.('panels.docker-ressources.outils') ?? 'Outils'}</span>{': '}<input value={newTools} onChange={(e) => setNewTools(e.target.value)} placeholder="pytest git"
          style={{ background: 'var(--mw-bg, #0f172a)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6, padding: '2px 6px', color: 'var(--mw-fg, #e2e8f0)', fontSize: 11, width: 140 }} />
      </p>
      {(caches ?? []).map((c: any, i: number) => (
        <div key={c.name ?? i} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
          <span style={{ flex: 1, fontWeight: 600 }}>{c.name}</span>
          <span style={{ color: c.local ? '#4ade80' : '#64748b', fontSize: 11 }}>{c.local ? '✓ local' : 'image absente'}</span>
        </div>
      ))}

      {/* ── Testeurs / codeurs : copie d'un cache (fork) + conteneurs ── */}
      <div style={{ fontWeight: 600, margin: '10px 0 4px', color: '#67e8f9' }}>{ctx.t?.('panels.docker-ressources.testeurs') ?? 'Testeurs / codeurs'}</div>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 6 }}>
        <span style={{ fontSize: 11, color: '#94a3b8' }}>Fork depuis :</span>
        {(caches ?? []).map((c: any, i: number) => (
          <button key={c.name ?? i} className="mw-btn" style={{ fontSize: 11 }} onClick={() => act('docker/fork', { cache: c.name })}>
            {ctx.t?.('panels.docker-ressources.fork') ?? '⚡ copie'} {c.name}
          </button>
        ))}
      </div>
      {containers.length === 0 ? <div style={{ fontSize: 11, color: '#475569' }}>Aucun conteneur forké.</div> : null}
      {containers.map((v: any) => (
        <div key={v.name} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
          <span style={{ flex: 1 }}>{v.name}</span>
          <span style={{ fontWeight: 600, color: v?.status === 'running' ? '#4ade80' : '#94a3b8' }}>{v?.status === 'running' ? (ctx.t?.('panels.docker-ressources.running') ?? 'running') : (v?.status ?? '')}</span>
          {v?.agent_id ? <span style={{ fontSize: 11, color: '#94a3b8' }}>agent {v.agent_id}</span> : null}
          <button className="mw-btn" onClick={() => act('docker/run-tests', { container: v.name, command: 'pytest -q' })} style={{ fontSize: 11 }}>{ctx.t?.('panels.docker-ressources.tests') ?? 'Tests'}</button>
          <button className="mw-btn" onClick={() => act('docker/snapshot', { container: v.name })} style={{ fontSize: 11 }}>{ctx.t?.('panels.docker-ressources.snapshot') ?? 'Snapshot'}</button>
          <button className="mw-btn" onClick={() => act('docker/release', { container: v.name })} style={{ fontSize: 11 }}>{ctx.t?.('panels.docker-ressources.release') ?? 'Libérer'}</button>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'docker-ressources',
  labelKey: 'panels.docker-ressources.titre',
  iconKey: 'panels.docker-ressources.titre',
  version: '1.1.0',
  essential: false,
  bundles: ['monitoring', 'docker'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[docker-ressources] Ressources Docker v1.1.0\n  préparateurs (caches) + testeurs/codeurs (fork+conteneurs)\n  routes: docker/caches/list, docker/status, docker/cache/create, docker/fork, docker/run-tests, docker/snapshot, docker/release',
  component: DockerPanel,
};

export const langFr = LANG_FR;