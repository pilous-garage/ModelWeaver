/** PanelTreeRenderer — Rendu récursif d'un arbre de panneaux (avec onglets).
 *
 * Modèle unifié :
 *  - nœud `group` : espace à onglets (TabbedPanel) — plusieurs panels par
 *    espace, drag/drop (déplacer un onglet / split 4 directions).
 *  - nœud `panel` : rétro-compat, rendu comme groupe à 1 onglet.
 *  - nœud split (direction/sizes/children) : split redimensionnable via
 *    react-resizable-panels ; chaque feuille est un groupe d'onglets.
 */

import React, { useEffect, useState, useRef } from 'react';
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
type DropZone = 'top' | 'bottom' | 'left' | 'right' | 'middle' | null;

/** Groupe d'onglets : barre + contenu + drag/drop (déplacer un onglet vers
 * un autre groupe, ou split 4 directions sur les bords). */
function GroupTabs({ group, ctx }: { group: PanelTreeNode; ctx: any }) {
  const tabs = (group.tabs || []).filter((t: string) => t);
  const active = (group.activeTab && tabs.includes(group.activeTab)) ? group.activeTab : (tabs[0] || '');
  const api = ctx.api || {};
  const rootRef = useRef<HTMLDivElement>(null);
  const [dropZone, setDropZone] = useState<DropZone>(null);
  const [dragOverTab, setDragOverTab] = useState<string | null>(null);
  const titleFor = (id: string) => PANEL_REGISTRY[id]?.label || id;

  const EDGE = 0.10;
  const getDropZone = (e: React.DragEvent): DropZone => {
    const el = rootRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    if (x < EDGE) return 'left';
    if (x > 1 - EDGE) return 'right';
    if (y < EDGE) return 'top';
    if (y > 1 - EDGE) return 'bottom';
    return 'middle';
  };
  const clearDrag = () => { setDropZone(null); setDragOverTab(null); };

  const onTabDragStart = (e: React.DragEvent, tabId: string) => {
    e.dataTransfer.setData('application/mw-tab', JSON.stringify({ tabId, fromGroup: group.groupId }));
    e.dataTransfer.effectAllowed = 'move';
  };
  const onRootDragOver = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes('application/mw-tab')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDropZone(getDropZone(e));
  };
  const onTabDragOver = (e: React.DragEvent, tabId: string) => {
    if (!e.dataTransfer.types.includes('application/mw-tab')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const zone = getDropZone(e);
    setDropZone(zone);
    setDragOverTab(zone === 'middle' ? tabId : null);
  };
  const onBarDragOver = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes('application/mw-tab')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDropZone(null);
    setDragOverTab(null);
  };
  const onTabDrop = (e: React.DragEvent, targetTabId: string) => {
    e.preventDefault(); e.stopPropagation();
    clearDrag();
    const raw = e.dataTransfer.getData('application/mw-tab');
    if (!raw) return;
    const { tabId, fromGroup } = JSON.parse(raw);
    if (tabId === targetTabId && fromGroup === group.groupId) return;
    const idx = tabs.indexOf(targetTabId);
    api.moveTabToGroup?.(tabId, fromGroup, group.groupId, idx);
  };
  const onBarDrop = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    clearDrag();
    const raw = e.dataTransfer.getData('application/mw-tab');
    if (!raw) return;
    const { tabId, fromGroup } = JSON.parse(raw);
    if (fromGroup === group.groupId) return;
    api.moveTabToGroup?.(tabId, fromGroup, group.groupId);
  };
  const onRootDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const zone = dropZone;
    clearDrag();
    const raw = e.dataTransfer.getData('application/mw-tab');
    if (!raw) return;
    if (zone === 'middle') {
      const { tabId, fromGroup } = JSON.parse(raw);
      if (fromGroup === group.groupId) return;
      api.moveTabToGroup?.(tabId, fromGroup, group.groupId);
      return;
    }
    const { tabId, fromGroup } = JSON.parse(raw);
    const dir = (zone === 'top' || zone === 'bottom') ? 'vertical' : 'horizontal';
    api.splitLeafAtWithTab?.(group.groupId, dir, tabId, fromGroup);
  };

  const edge = dropZone === 'left' ? 'right' : dropZone === 'right' ? 'left' : dropZone === 'top' ? 'bottom' : dropZone === 'bottom' ? 'top' : null;

  return (
    <div
      ref={rootRef}
      onDragOver={onRootDragOver}
      onDrop={onRootDrop}
      onDragLeave={clearDrag}
      style={{
        position: 'relative',
        display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, height: '100%',
        boxSizing: 'border-box',
      }}
    >
      {/* Preview split sur les bords */}
      {dropZone && dropZone !== 'middle' && (
        <div style={{
          position: 'absolute', inset: 0, zIndex: 20, pointerEvents: 'none',
          display: 'flex',
          flexDirection: edge === 'top' || edge === 'bottom' ? 'column' : 'row',
        }}>
          <div style={{
            flex: 1, backgroundColor: 'rgba(96, 165, 250, 0.08)',
            border: '1px dashed #60a5fa', borderRadius: '0.4rem', margin: '2px',
          }} />
          <div style={{
            [edge === 'top' || edge === 'bottom' ? 'height' : 'width']: '30%',
            backgroundColor: 'rgba(96, 165, 250, 0.15)',
            border: '1px dashed #60a5fa', borderRadius: '0.4rem', margin: '2px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: '0.7rem', color: '#93c5fd', fontWeight: 600,
          }}>
            + nouveau panneau
          </div>
        </div>
      )}
      {/* Preview middle : barre en surbrillance */}
      {dropZone === 'middle' && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: '1.8rem', zIndex: 20,
          pointerEvents: 'none', backgroundColor: 'rgba(96, 165, 250, 0.12)',
          borderBottom: '2px solid #60a5fa',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '0.65rem', color: '#93c5fd', fontWeight: 600,
        }}>
          + ajouter au groupe
        </div>
      )}

      {/* Barre d'onglets */}
      <div
        onDragOver={onBarDragOver}
        onDrop={onBarDrop}
        onDragLeave={clearDrag}
        style={{
          display: 'flex', flexWrap: 'wrap', gap: '2px',
          padding: '0.2rem 0.2rem 0',
          backgroundColor: '#0f172a',
          borderBottom: '1px solid #334155',
          minHeight: '1.9rem',
          flexShrink: 0,
          zIndex: dropZone && dropZone !== 'middle' ? 30 : 'auto',
        }}
      >
        {tabs.map((tabId: string) => {
          const isActive = tabId === active;
          return (
            <div
              key={tabId}
              draggable
              onDragStart={(e) => onTabDragStart(e, tabId)}
              onDragOver={(e) => onTabDragOver(e, tabId)}
              onDrop={(e) => onTabDrop(e, tabId)}
              onDragLeave={clearDrag}
              onClick={() => api.activateTab?.(group.groupId, tabId)}
              style={{
                display: 'flex', alignItems: 'center', gap: '0.3rem',
                padding: '0.18rem 0.5rem', fontSize: '0.72rem',
                fontWeight: isActive ? 600 : 400,
                color: isActive ? '#e2e8f0' : '#94a3b8',
                backgroundColor: isActive ? '#1e293b' : 'transparent',
                borderTopLeftRadius: '0.3rem', borderTopRightRadius: '0.3rem',
                cursor: 'grab', userSelect: 'none', whiteSpace: 'nowrap',
                border: dragOverTab === tabId && dropZone === 'middle' ? '1px dashed #60a5fa' : '1px solid transparent',
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
      <div
        onDragOver={(e) => { if (e.dataTransfer.types.includes('application/mw-tab')) e.preventDefault(); }}
        style={{ flex: 1, overflow: 'auto', minHeight: 0, display: 'flex', zIndex: dropZone && dropZone !== 'middle' ? 30 : 'auto' }}
      >
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

  // Normalise sizes : si le nombre de children ne correspond pas, répartit
  // équitablement (évite que react-resizable-panels crashe sur un defaultSize
  // incohérent après un split).
  const sizes = (tree.sizes && tree.sizes.length === children.length)
    ? tree.sizes
    : Array(children.length).fill(Math.round(100 / children.length));

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
            defaultSize={sizes[idx]}
            minSize={10}
          >
            <PanelTreeRenderer tree={child} ctx={ctx} />
          </Panel>
        </React.Fragment>
      ))}
    </Group>
  );
}
