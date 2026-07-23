import { useMemo, useEffect, useRef } from 'react';
import { parse as yamlParse } from 'yaml';
import { Group, Panel, Separator } from 'react-resizable-panels';
import { useSandbox, CatalogueType } from '../useSandbox.ts';
import { daemonPost } from '../bridge.ts';
import { CodeEditor, LibResolveResult } from './CodeEditor.tsx';
import { SandboxMenuBar } from './SandboxMenuBar.tsx';
import { AgentLegoPanel, CATALOGUE_MIME } from './AgentLegoPanel.tsx';
import { WorkflowGraphEditor } from './WorkflowGraphEditor.tsx';
import { CatalogTree } from './CatalogTree.tsx';
import type { Step } from '../lib/workflowGraph.ts';

function catalogueGroupPath(tab: CatalogueType, it: any): string[] {
  if (tab === 'skills') return String(it.category || 'system').split(/[./]/).filter(Boolean);
  if (tab === 'roles') return [it.class || it.classe || 'Autre', ...(it.sub_class ? [it.sub_class] : [])];
  if (tab === 'agents') return [it.role || 'Sans rôle'];
  return [];
}

const WORKFLOW_TYPES: CatalogueType[] = ['agents', 'behaviors'];

const TABS: { id: CatalogueType; label: string }[] = [
  { id: 'skills', label: '🧩 Skills' },
  { id: 'behaviors', label: '🔀 Comportements' },
  { id: 'personalities', label: '🎭 Personnalités' },
  { id: 'roles', label: '🏷️ Rôles' },
  { id: 'agents', label: '🤖 Agents' },
];

export function AgentSandboxIDE() {
  const s = useSandbox();
  const smokeRan = useRef(false);

  // ── Mode smoke auto (?smoke=1) ──
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (!new URLSearchParams(window.location.search).get('smoke')) return;
    if (smokeRan.current) return;
    smokeRan.current = true;

    const log = (...args: any[]) => console.log('[smoke]', ...args);
    (window as any).__smoke_result = null;

    const run = async () => {
      log('=== SMOKE AUTO START ===');
      try {
        // 1. Switch to agents tab & open worker
        s.setActiveTab('agents');
        await new Promise(r => setTimeout(r, 2000));
        log('tab agents activé');

        await s.openItem('worker');
        await new Promise(r => setTimeout(r, 3000));
        log('worker ouvert');

        // 2. Switch to graph view
        const workerId = s.openDocs.find(d => d.name === 'worker')?.id;
        if (workerId) {
          s.setDocView(workerId, 'graph');
          await new Promise(r => setTimeout(r, 2000));
          log('vue graphe activée, docId=' + workerId);
        }

        // 3. Wait for graph to render, then extract nodes/edges
        await new Promise(r => setTimeout(r, 4000));

        const graphState = evalNodeEdge();
        log(`nœuds=${graphState.nodes.length}, arêtes=${graphState.edges.length}`);
        (window as any).__smoke_result = graphState;

        // 4. Export YAML via API (save le .graph)
        const doc = s.openDocs.find(d => d.name === 'worker');
        if (doc) {
          const res = await daemonPost('catalogue/agents/get', { name: 'worker' });
          if (res?.ok && res?.result) {
            log('agent YAML récupéré', Object.keys(res.result));
            (window as any).__smoke_yaml = res.result.yaml;
          }
        }

        log('=== SMOKE AUTO OK ===');
      } catch (e: any) {
        log('❌ SMOKE FAIL:', e.message || e);
        (window as any).__smoke_result = { error: String(e) };
      }
    };

    run();
  }, [s.openDocs.length]);

  function evalNodeEdge() {
    try {
      const nodes = Array.from(document.querySelectorAll('.react-flow__node')).map((n: any) => ({
        id: n.getAttribute('data-id') || '',
        label: (n.querySelector('div') || n).textContent?.trim()?.substring(0, 80) || '',
      }));
      const edges = Array.from(document.querySelectorAll('.react-flow__edge')).map((e: any) => ({
        from: e.getAttribute('data-sourceid') || '',
        to: e.getAttribute('data-targetid') || '',
      }));
      return { nodes, edges };
    } catch {
      return { nodes: [], edges: [] };
    }
  }

  const resolveRef = async (ref: string): Promise<LibResolveResult | null> => {
    try {
      const res = await daemonPost('lib/resolve', { ref });
      if (res?.ok && res?.result) return res.result as LibResolveResult;
      return { found: false };
    } catch {
      return { found: false };
    }
  };

  const doc = s.activeDoc;

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: '#1e1e2e', color: '#cdd6f4', fontFamily: 'system-ui, sans-serif' }}>
      <SandboxMenuBar
        onNew={(t) => s.newDoc(t)}
        onSave={s.saveActive}
        onSaveAll={s.saveAll}
        onCloseTab={() => s.activeDocId && s.closeDoc(s.activeDocId)}
        onRescanLib={s.rescanLib}
        showCatalogue={s.showCatalogue}
        onToggleCatalogue={() => s.setShowCatalogue(!s.showCatalogue)}
        showLego={s.showLego}
        onToggleLego={() => s.setShowLego(!s.showLego)}
        hasActive={!!s.activeDocId}
      />

      <header style={{ padding: '6px 16px', borderBottom: '1px solid #45475a', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
        <strong style={{ fontSize: 14 }}>🛠️ Agent Sandbox</strong>
        <span style={{ opacity: 0.6, fontSize: 12 }}>IDE de création d'agents</span>
        {s.msg && <span style={{ fontSize: 12, color: '#a6e3a1' }}>{s.msg}</span>}
        {s.error && <span style={{ fontSize: 12, color: '#f38ba8' }}>{s.error}</span>}
      </header>

      <div style={{ flex: 1, minHeight: 0 }}>
        <Group orientation="horizontal" style={{ height: '100%', display: 'flex' }}>
          {s.showCatalogue && (
            <>
              <Panel defaultSize={22} minSize={14} style={{ display: 'flex', minWidth: 0 }}>
                <CataloguePane s={s} />
              </Panel>
              <Separator style={{ width: 4, background: '#45475a', cursor: 'col-resize' }} />
            </>
          )}
          <Panel defaultSize={s.showLego ? 54 : 78} minSize={30} style={{ display: 'flex', minWidth: 0 }}>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0 }}>
              <EditorTabs s={s} />
              {doc ? (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderBottom: '1px solid #45475a', flexShrink: 0 }}>
                    <span style={{ fontSize: 12, opacity: 0.7 }}>
                      {doc.isNew ? `Nouveau ${doc.type}` : `${doc.type} / ${doc.name}`}
                    </span>
                    {doc.dirty && <span style={{ color: '#f9e2af', fontSize: 11 }}>● modifié</span>}
                    <div style={{ flex: 1 }} />
                    {WORKFLOW_TYPES.includes(doc.type) && !doc.showInline && (
                      <div style={{ display: 'flex', border: '1px solid #45475a', borderRadius: 6, overflow: 'hidden' }}>
                        {(['code', 'graph'] as const).map(v => (
                          <button key={v} onClick={() => s.setDocView(doc.id, v)} style={{
                            background: (doc.view || 'code') === v ? '#89b4fa' : 'transparent',
                            color: (doc.view || 'code') === v ? '#1e1e2e' : '#a6adc8',
                            border: 'none', padding: '4px 10px', cursor: 'pointer', fontSize: 12, fontWeight: 600,
                          }}>{v === 'code' ? '📄 Code' : '🔀 Graphe'}</button>
                        ))}
                      </div>
                    )}
                    {doc.type === 'agents' && !doc.isNew && (
                      <button onClick={() => s.generateInline(doc.id)} style={btnStyle('#fab387', '#1e1e2e')}>⚙️ Générer inline</button>
                    )}
                    {doc.type === 'agents' && (
                      <button onClick={() => s.toggleInline(doc.id)} disabled={!doc.inlineText} style={btnStyle('#cba6f7', '#1e1e2e')}>
                        {doc.showInline ? '📄 Normal' : '📦 Inline'}
                      </button>
                    )}
                    <button onClick={() => s.saveDoc(doc.id)} disabled={!doc.dirty} style={btnStyle('#89b4fa', '#1e1e2e')}>💾 Enregistrer</button>
                  </div>
                  {WORKFLOW_TYPES.includes(doc.type) && (doc.view || 'code') === 'graph' && !doc.showInline ? (
                    <GraphView s={s} />
                  ) : (
                    <CodeEditor
                      key={doc.id + (doc.showInline ? ':inline' : ':normal')}
                      value={doc.showInline ? doc.inlineText : doc.text}
                      readOnly={doc.showInline}
                      onResolve={resolveRef}
                      onChange={(v) => {
                        if (doc.showInline) s.setDocInline(doc.id, v);
                        else s.setDocText(doc.id, v);
                      }}
                    />
                  )}
                </>
              ) : (
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: 0.4, fontSize: 13 }}>
                  Ouvrez un élément du catalogue ou créez-en un nouveau (Fichier ▸ Nouveau).
                </div>
              )}
            </div>
          </Panel>
          {s.showLego && (
            <>
              <Separator style={{ width: 4, background: '#45475a', cursor: 'col-resize' }} />
              <Panel defaultSize={24} minSize={14} style={{ display: 'flex', minWidth: 0 }}>
                <AgentLegoPanel s={s} />
              </Panel>
            </>
          )}
        </Group>
      </div>
    </div>
  );
}

function listEntrypoints(obj: any): string[] {
  if (obj?.entrypoints && typeof obj.entrypoints === 'object') {
    return Object.keys(obj.entrypoints).sort();
  }
  if (Array.isArray(obj?.workflow?.steps)) return ['main'];
  return [];
}

function resolveSteps(obj: any, ep: string): Step[] {
  if (obj?.entrypoints?.[ep]?.steps) return obj.entrypoints[ep].steps;
  if (ep === 'main' && Array.isArray(obj?.workflow?.steps)) return obj.workflow.steps;
  return [];
}

function entrypointCounts(obj: any, ep: string): number {
  return resolveSteps(obj, ep).length;
}

function GraphView({ s }: { s: ReturnType<typeof useSandbox> }) {
  const doc = s.activeDoc;
  if (!doc) return null;
  const { obj, parseError } = useMemo(() => {
    try {
      const o = yamlParse(doc.text);
      return { obj: o || {}, parseError: null };
    } catch (e: any) {
      return { obj: null, parseError: e.message || 'YAML invalide' };
    }
  }, [doc.text]);
  const eps = obj ? listEntrypoints(obj) : [];
  const activeEp = doc.activeEntrypoint || eps[0] || 'main';
  const steps: Step[] = obj ? resolveSteps(obj, activeEp) : [];
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0 }}>
      {parseError && (
        <div style={{ padding: '4px 12px', background: '#f38ba822', color: '#f38ba8', fontSize: 11, borderBottom: '1px solid #f38ba844' }}>
          ⚠️ {parseError} — le graphe est en lecture seule ; corrige le code en vue Code.
        </div>
      )}
      {doc.type === 'agents' && obj && <GraphSlotsBar s={s} obj={obj} />}
      {doc.type === 'agents' && eps.length > 0 && (
        <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid #45475a', flexShrink: 0, padding: '0 8px' }}>
          {eps.map(ep => (
            <button
              key={ep}
              onClick={() => s.setActiveEntrypoint(doc.id, ep)}
              style={{
                padding: '5px 12px', fontSize: 11, cursor: 'pointer', border: 'none',
                borderBottom: activeEp === ep ? '2px solid #89b4fa' : '2px solid transparent',
                background: 'transparent', color: activeEp === ep ? '#cdd6f4' : '#6c7086',
                display: 'flex', alignItems: 'center', gap: 5,
              }}
            >
              ◇ {ep}
              <span style={{ color: '#6c7086', fontSize: 10 }}>{entrypointCounts(obj || {}, ep)} steps</span>
            </button>
          ))}
        </div>
      )}
      <WorkflowGraphEditor
        key={`${doc.id}:${activeEp}`}
        docId={doc.id}
        steps={steps}
        onStepsChange={parseError ? undefined : (next) => s.setWorkflowSteps(doc.id, next, activeEp)}
      />
    </div>
  );
}

function GraphSlotsBar({ s, obj }: { s: ReturnType<typeof useSandbox>; obj: any }) {
  const role: string = obj.role || '';
  const tone: string = obj.personality?.tone || '';
  const skills: string[] = Array.isArray(obj.skills) ? obj.skills : [];
  const dropSlot = (accepts: CatalogueType, apply: (name: string) => void) => ({
    onDragOver: (e: React.DragEvent) => {
      if (!e.dataTransfer.types.includes(CATALOGUE_MIME)) return;
      e.preventDefault(); e.dataTransfer.dropEffect = 'copy';
    },
    onDrop: (e: React.DragEvent) => {
      const raw = e.dataTransfer.getData(CATALOGUE_MIME);
      if (!raw) return;
      e.preventDefault();
      try { const p = JSON.parse(raw); if (p.type === accepts) apply(p.name); } catch { /* ignore */ }
    },
  });
  const slot: React.CSSProperties = {
    border: '1px dashed #45475a', borderRadius: 6, padding: '3px 8px',
    fontSize: 11, minWidth: 90, background: '#11111b',
  };
  return (
    <div style={{ display: 'flex', gap: 8, padding: '6px 12px', borderBottom: '1px solid #45475a', alignItems: 'center', flexShrink: 0, flexWrap: 'wrap' }}>
      <div style={slot} {...dropSlot('roles', name => s.patchActiveDoc(o => { o.role = name; }))}>
        <span style={{ opacity: 0.6 }}>🏷️ rôle : </span>{role || <em style={{ opacity: 0.4 }}>glisser</em>}
      </div>
      <div style={slot} {...dropSlot('personalities', async name => {
        const pers = await s.fetchItemParsed('personalities', name);
        if (pers) s.patchActiveDoc(o => { o.personality = { tone: pers.tone || '', system_prompt: pers.system_prompt || '' }; });
      })}>
        <span style={{ opacity: 0.6 }}>🎭 perso : </span>{tone || <em style={{ opacity: 0.4 }}>glisser</em>}
      </div>
      <div style={{ ...slot, borderStyle: 'solid', opacity: 0.85 }} title={skills.join(', ')}>
        <span style={{ opacity: 0.6 }}>🧩 skills auto : </span>{skills.length}
      </div>
    </div>
  );
}

function CataloguePane({ s }: { s: ReturnType<typeof useSandbox> }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, borderRight: '1px solid #45475a' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', borderBottom: '1px solid #45475a' }}>
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => s.setActiveTab(t.id)}
            style={{
              flex: '1 0 auto', padding: '8px 4px', fontSize: 11, cursor: 'pointer',
              background: s.activeTab === t.id ? '#313244' : 'transparent',
              color: s.activeTab === t.id ? '#cdd6f4' : '#a6adc8',
              border: 'none', borderBottom: s.activeTab === t.id ? '2px solid #89b4fa' : '2px solid transparent',
            }}
          >{t.label}</button>
        ))}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 8px' }}>
        <span style={{ fontSize: 11, opacity: 0.6 }}>{s.items.length} élément(s)</span>
        <button onClick={() => s.newDoc()} style={btnStyle('#a6e3a1', '#1e1e2e')}>+ Nouveau</button>
      </div>
      <div style={{ flex: 1, overflow: 'hidden', padding: 8, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {s.loading ? (
          <div style={{ opacity: 0.6, fontSize: 12 }}>Chargement…</div>
        ) : (
          <CatalogTree
            items={s.items as any[]}
            theme="catppuccin"
            storageKey={`mw_fold_sandbox_${s.activeTab}`}
            searchPlaceholder="Rechercher…"
            getKey={(it: any) => it.name}
            getSearchText={(it: any) => `${it.name} ${it.description || ''}`}
            getGroupPath={(it: any) => catalogueGroupPath(s.activeTab, it)}
            renderItem={(it: any) => {
              const isSkill = s.activeTab === 'skills';
              const openId = `${s.activeTab}:${it.name}`;
              const isOpen = s.openDocs.some(d => d.id === openId);
              return (
                <div
                  draggable
                  onDragStart={(e) => {
                    const payload: any = { type: s.activeTab, name: it.name };
                    if (s.activeTab === 'skills') payload.uses_llm = !!it.uses_llm;
                    e.dataTransfer.setData(CATALOGUE_MIME, JSON.stringify(payload));
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  onClick={() => s.openItem(it.name)}
                  style={{
                    padding: '6px 8px', borderRadius: 6, cursor: 'pointer',
                    background: s.activeDocId === openId ? '#45475a' : 'transparent',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = s.activeDocId === openId ? '#45475a' : '#313244')}
                  onMouseLeave={e => (e.currentTarget.style.background = s.activeDocId === openId ? '#45475a' : 'transparent')}
                >
                  <div style={{ overflow: 'hidden' }}>
                    <div style={{ fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {isOpen && <span style={{ color: '#89b4fa' }}>● </span>}{it.name}
                    </div>
                    {it.description && <div style={{ fontSize: 10, opacity: 0.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{it.description}</div>}
                  </div>
                  {!isSkill && (
                    <button
                      onClick={(e) => { e.stopPropagation(); s.deleteItem(it.name); }}
                      style={{ background: 'transparent', border: 'none', color: '#f38ba8', cursor: 'pointer', fontSize: 14 }}
                      title="Supprimer"
                    >✕</button>
                  )}
                </div>
              );
            }}
          />
        )}
      </div>
    </div>
  );
}

function EditorTabs({ s }: { s: ReturnType<typeof useSandbox> }) {
  if (s.openDocs.length === 0) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 2, padding: '4px 6px 0', background: '#181825', borderBottom: '1px solid #45475a', flexShrink: 0 }}>
      {s.openDocs.map(d => {
        const active = d.id === s.activeDocId;
        return (
          <div
            key={d.id}
            onClick={() => s.activateDoc(d.id)}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '4px 8px', fontSize: 12,
              cursor: 'pointer', maxWidth: 200,
              color: active ? '#cdd6f4' : '#a6adc8',
              background: active ? '#1e1e2e' : 'transparent',
              borderTopLeftRadius: 6, borderTopRightRadius: 6,
              border: active ? '1px solid #45475a' : '1px solid transparent',
              borderBottom: 'none',
            }}
            title={d.name || d.title}
          >
            <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {d.dirty && <span style={{ color: '#f9e2af' }}>● </span>}
              {d.title}
            </span>
            <span
              onClick={(e) => { e.stopPropagation(); s.closeDoc(d.id); }}
              style={{ color: '#6c7086', fontSize: 12, cursor: 'pointer', padding: '0 2px' }}
            >✕</span>
          </div>
        );
      })}
    </div>
  );
}

function btnStyle(bg: string, fg: string): React.CSSProperties {
  return {
    background: bg, color: fg, border: 'none', borderRadius: 6, padding: '5px 10px',
    cursor: 'pointer', fontSize: 12, fontWeight: 600,
  };
}
