import { Group, Panel, Separator } from 'react-resizable-panels';
import { useSandbox, CatalogueType, daemonPost } from '../useSandbox.ts';
import { CodeEditor, LibResolveResult } from './CodeEditor.tsx';
import { SandboxMenuBar } from './SandboxMenuBar.tsx';
import { AgentLegoPanel, CATALOGUE_MIME } from './AgentLegoPanel.tsx';

const TABS: { id: CatalogueType; label: string }[] = [
  { id: 'skills', label: '🧩 Skills' },
  { id: 'behaviors', label: '🔀 Comportements' },
  { id: 'personalities', label: '🎭 Personnalités' },
  { id: 'roles', label: '🏷️ Rôles' },
  { id: 'agents', label: '🤖 Agents' },
];

export function AgentSandboxIDE() {
  const s = useSandbox();

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
      <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
        {s.loading && <div style={{ opacity: 0.6, fontSize: 12 }}>Chargement…</div>}
        {!s.loading && s.items.length === 0 && (
          <div style={{ opacity: 0.5, fontSize: 12, padding: 8 }}>Aucun élément.</div>
        )}
        {s.items.map((it: any) => {
          const isSkill = s.activeTab === 'skills';
          const openId = `${s.activeTab}:${it.name}`;
          const isOpen = s.openDocs.some(d => d.id === openId);
          return (
            <div
              key={it.name}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData(CATALOGUE_MIME, JSON.stringify({ type: s.activeTab, name: it.name }));
                e.dataTransfer.effectAllowed = 'copy';
              }}
              onClick={() => s.openItem(it.name)}
              style={{
                padding: '6px 8px', borderRadius: 6, cursor: 'pointer', marginBottom: 4,
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
        })}
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
