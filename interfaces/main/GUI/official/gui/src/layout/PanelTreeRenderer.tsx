/** PanelTreeRenderer — Rendu récursif d'un arbre de panneaux (avec onglets).
 *
 * Modèle unifié :
 *  - nœud `group` : espace à onglets (TabbedPanel) — plusieurs panels par
 *    espace, drag/drop (déplacer un onglet / split 4 directions).
 *  - nœud `panel` : rétro-compat, rendu comme groupe à 1 onglet.
 *  - nœud split (direction/sizes/children) : split redimensionnable via
 *    react-resizable-panels ; chaque feuille est un groupe d'onglets.
 */

import React, { useEffect, useState } from 'react';
import { Group, Panel, Separator } from 'react-resizable-panels';
import { PANEL_REGISTRY } from '../panels/index.ts';
import type { PanelTreeNode } from './useLayout.ts';
import { logGui } from '../gui_log.ts';

interface Props {
  tree: PanelTreeNode;
  ctx: any; // PanelContext (api expose activateTab/closeTab/moveTabToGroup/splitLeafAtWithTab)
}

/** Charge PA RESSEUSEMENT un panel externe référencé mais non chargé. */
function LazyPanel({ id, ctx }: { id: string; ctx: any }) {
  const [def, setDef] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    logGui('panel:lazy-request', { id });
    import('../panels/loader.ts').then((m) =>
      m.ensurePanelLoaded(id).then((d) => {
        if (cancelled) return;
        if (d) setDef(d); else setError('chargement échoué');
      }),
    ).catch((e) => { if (!cancelled) setError(String(e?.message || e)); });
    return () => { cancelled = true; };
  }, [id]);

  if (error) {
    return (
      <div style={{ padding: '1rem', color: 'var(--error, #e84545)' }}>
        Panneau "{id}" : {error}
      </div>
    );
  }
  if (!def) {
    return (
      <div style={{ padding: '1rem', color: '#94a3b8' }}>
        Chargement du panneau "{id}"…
      </div>
    );
  }
  const C = def.component;
  return <C ctx={ctx} />;
}

/** Rendu d'un panel par id (registry ou lazy-load). */
export function renderPanelById(id: string, ctx: any) {
  const panelDef = PANEL_REGISTRY[id];
  if (panelDef) {
    const PanelComponent = panelDef.component;
    return <PanelComponent ctx={ctx} />;
  }
  return <LazyPanel id={id} ctx={ctx} />;
}

/** Barre d'onglets + contenu du groupe. */
function GroupTabs({ group, ctx }: { group: PanelTreeNode; ctx: any }) {
  const tabs = (group.tabs || []).filter((t: string) => t);
  const active = (group.activeTab && tabs.includes(group.activeTab)) ? group.activeTab : (tabs[0] || '');
  const api = ctx.api || {};
  const titleFor = (id: string) => PANEL_REGISTRY[id]?.label || id;

  const onTabDragStart = (e: React.DragEvent, tabId: string) => {
    e.dataTransfer.setData('application/mw-tab', JSON.stringify({ tabId, fromGroup: group.groupId }));
    e.dataTransfer.effectAllowed = 'move';
  };
  const onTabDrop = (e: React.DragEvent, targetTabId: string) => {
    e.preventDefault(); e.stopPropagation();
    const raw = e.dataTransfer.getData('application/mw-tab');
    if (!raw) return;
    const { tabId, fromGroup } = JSON.parse(raw);
    if (tabId === targetTabId && fromGroup === group.groupId) return;
    const idx = tabs.indexOf(targetTabId);
    api.moveTabToGroup?.(tabId, fromGroup, group.groupId, idx);
  };
  const onBarDrop = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    const raw = e.dataTransfer.getData('application/mw-tab');
    if (!raw) return;
    const { tabId, fromGroup } = JSON.parse(raw);
    if (fromGroup === group.groupId) return;
    api.moveTabToGroup?.(tabId, fromGroup, group.groupId);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, height: '100%', boxSizing: 'border-box' }}>
      {/* Barre d'onglets */}
      <div
        onDragOver={(e) => { if (e.dataTransfer.types.includes('application/mw-tab')) e.preventDefault(); }}
        onDrop={onBarDrop}
        style={{
          display: 'flex', flexWrap: 'wrap', gap: '2px',
          padding: '0.2rem 0.2rem 0',
          backgroundColor: '#0f172a',
          borderBottom: '1px solid #334155',
          minHeight: '1.9rem',
          flexShrink: 0,
        }}
      >
        {tabs.map((tabId: string) => {
          const isActive = tabId === active;
          return (
            <div
              key={tabId}
              draggable
              onDragStart={(e) => onTabDragStart(e, tabId)}
              onDragOver={(e) => { if (e.dataTransfer.types.includes('application/mw-tab')) e.preventDefault(); }}
              onDrop={(e) => onTabDrop(e, tabId)}
              onClick={() => api.activateTab?.(group.groupId, tabId)}
              style={{
                display: 'flex', alignItems: 'center', gap: '0.3rem',
                padding: '0.18rem 0.5rem', fontSize: '0.72rem',
                fontWeight: isActive ? 600 : 400,
                color: isActive ? '#e2e8f0' : '#94a3b8',
                backgroundColor: isActive ? '#1e293b' : 'transparent',
                borderTopLeftRadius: '0.3rem', borderTopRightRadius: '0.3rem',
                cursor: 'grab', userSelect: 'none', whiteSpace: 'nowrap',
                border: '1px solid transparent',
              }}
            >
              <span style={{ color: '#64748b', fontSize: '0.6rem' }}>☰</span>
              <span>{titleFor(tabId)}</span>
              {group.closable !== false && (
                <span
                  onClick={(e) => { e.stopPropagation(); api.closeTab?.(group.groupId, tabId); }}
                  style={{ color: '#64748b', fontSize: '0.65rem', cursor: 'pointer', padding: '0 2px' }}
                  title="Fermer"
                >✕</span>
              )}
            </div>
          );
        })}
      </div>
      {/* Contenu de l'onglet actif */}
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0, display: 'flex' }}>
        {renderPanelById(active, ctx)}
      </div>
    </div>
  );
}

export function PanelTreeRenderer({ tree, ctx }: Props) {
  if (tree.type === 'group') {
    if (tree.visible === false) return null;
    return <GroupTabs group={tree} ctx={ctx} />;
  }

  if (tree.type === 'panel') {
    if (tree.visible === false) return null;
    return <GroupTabs group={{ ...tree, type: 'group', groupId: tree.groupId || `pg-${tree.id}`, tabs: [tree.id || ''], activeTab: tree.id || '' }} ctx={ctx} />;
  }

  if (tree.type === 'ext') {
    if (tree.visible === false) return null;
    return (
      <iframe
        src={tree.url || `/extensions/${tree.id}/index.html`}
        style={{ width: '100%', height: '100%', border: 'none', background: 'var(--bg, #1a1a2e)' }}
        title={tree.id}
      />
    );
  }

  // Nœud splitter (direction/sizes/children)
  const direction = tree.direction || 'horizontal';
  const children = (tree.children || []).filter((c) => c.visible !== false);

  if (children.length === 0) return null;
  if (children.length === 1) return <PanelTreeRenderer tree={children[0]} ctx={ctx} />;

  return (
    <Group direction={direction} style={{ height: '100%', width: '100%' }}>
      {children.map((child, idx) => (
        <React.Fragment key={child.groupId || child.id || `node-${idx}`}>
          {idx > 0 && (
            <Separator
              style={{
                width: direction === 'horizontal' ? '4px' : '100%',
                height: direction === 'vertical' ? '4px' : undefined,
                background: 'var(--border, #2a2a4a)',
                cursor: direction === 'horizontal' ? 'col-resize' : 'row-resize',
              }}
            />
          )}
          <Panel
            defaultSize={child.sizes?.[0] !== undefined ? child.sizes[idx] : undefined}
            minSize={10}
          >
            <PanelTreeRenderer tree={child} ctx={ctx} />
          </Panel>
        </React.Fragment>
      ))}
    </Group>
  );
}
