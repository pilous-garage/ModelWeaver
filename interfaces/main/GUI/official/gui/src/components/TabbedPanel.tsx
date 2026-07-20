import React, { useRef, useState, useCallback } from 'react';
import type { AppApi, PanelGroup as PanelGroupType } from '../useApp.ts';

const PANEL_TITLES: Record<string, string> = {
  'system-state': 'Système',
  'resources': 'Ressources',
  'installed-tools': 'Outils',
  'catalogue': 'Catalogue',
  'chat': 'Chat',
  'install-queue': 'File',
  'agents': 'Agents',
  'local-models': 'LLM',
  'keys': 'Clés',
  'debug': 'Debug',
};

type DropZone = 'top' | 'bottom' | 'left' | 'right' | 'middle' | null;

interface Props {
  group: PanelGroupType;
  app: AppApi;
  children: (tabId: string) => React.ReactNode;
}

export function TabbedPanel({ group, app, children }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [dragOverTab, setDragOverTab] = useState<string | null>(null);
  const [dropZone, setDropZone] = useState<DropZone>(null);

  const EDGE = 0.10;

  const getDropZone = useCallback((e: React.DragEvent): DropZone => {
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
  }, []);

  const handleTabDragStart = (e: React.DragEvent, tabId: string) => {
    e.dataTransfer.setData('application/mw-tab', JSON.stringify({ tabId, fromGroup: group.id }));
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleRootDragOver = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes('application/mw-tab')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDropZone(getDropZone(e));
  };

  const handleTabDragOver = (e: React.DragEvent, tabId: string) => {
    if (!e.dataTransfer.types.includes('application/mw-tab')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const zone = getDropZone(e);
    setDropZone(zone);
    setDragOverTab(zone === 'middle' ? tabId : null);
  };

  const handleBarDragOver = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes('application/mw-tab')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDropZone(null);
    setDragOverTab(null);
  };

  const handleTabDrop = (e: React.DragEvent, targetTabId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDropZone(null);
    setDragOverTab(null);
    const raw = e.dataTransfer.getData('application/mw-tab');
    if (!raw) return;
    const { tabId, fromGroup: fromG } = JSON.parse(raw);
    if (tabId === targetTabId && fromG === group.id) return;
    const idx = group.tabs.indexOf(targetTabId);
    app.moveTabToGroup(tabId, fromG, group.id, idx);
  };

  const handleBarDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDropZone(null);
    setDragOverTab(null);
    const raw = e.dataTransfer.getData('application/mw-tab');
    if (!raw) return;
    const { tabId, fromGroup: fromG } = JSON.parse(raw);
    if (fromG === group.id) return;
    app.moveTabToGroup(tabId, fromG, group.id);
  };

  const handleRootDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const zone = dropZone;
    setDropZone(null);
    setDragOverTab(null);
    const raw = e.dataTransfer.getData('application/mw-tab');
    if (!raw) return;
    if (zone === 'middle') return;
    const { tabId, fromGroup: fromG } = JSON.parse(raw);
    const dir = zone === 'top' || zone === 'bottom' ? 'vertical' : 'horizontal';
    app.splitLeafAtWithTab(group.id, dir, tabId, fromG);
  };

  const handleDragLeave = () => {
    setDropZone(null);
    setDragOverTab(null);
  };

  const edge = dropZone === 'left' ? 'right' : dropZone === 'right' ? 'left' : dropZone === 'top' ? 'bottom' : dropZone === 'bottom' ? 'top' : null;

  return (
    <div
      ref={rootRef}
      onDragOver={handleRootDragOver}
      onDrop={handleRootDrop}
      onDragLeave={handleDragLeave}
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        minHeight: 0,
        backgroundColor: '#1e293b',
        borderRadius: '0.5rem',
        border: '1px solid #334155',
        overflow: 'hidden',
      }}
    >
      {/* Preview overlay for edge splits */}
      {dropZone && dropZone !== 'middle' && (
        <div style={{
          position: 'absolute',
          inset: 0,
          zIndex: 20,
          pointerEvents: 'none',
          display: 'flex',
          flexDirection: edge === 'top' || edge === 'bottom' ? 'column' : 'row',
        }}>
          <div style={{
            flex: 1,
            backgroundColor: 'rgba(96, 165, 250, 0.08)',
            border: '1px dashed #60a5fa',
            borderRadius: '0.4rem',
            margin: '2px',
          }} />
          <div style={{
            [edge === 'top' || edge === 'bottom' ? 'height' : 'width']: '30%',
            backgroundColor: 'rgba(96, 165, 250, 0.15)',
            border: '1px dashed #60a5fa',
            borderRadius: '0.4rem',
            margin: '2px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '0.7rem',
            color: '#93c5fd',
            fontWeight: 600,
          }}>
            + nouveau panneau
          </div>
        </div>
      )}

      {/* Tab bar */}
      <div
        onDragOver={handleBarDragOver}
        onDrop={handleBarDrop}
        onDragLeave={handleDragLeave}
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '2px',
          padding: '0.25rem 0.25rem 0',
          backgroundColor: '#0f172a',
          borderBottom: '1px solid #334155',
          minHeight: '1.8rem',
          zIndex: dropZone && dropZone !== 'middle' ? 30 : 'auto',
        }}
      >
        {group.tabs.map(tabId => {
          const isActive = tabId === group.activeTab;
          return (
            <div
              key={tabId}
              draggable
              onDragStart={e => handleTabDragStart(e, tabId)}
              onDragOver={e => handleTabDragOver(e, tabId)}
              onDrop={e => handleTabDrop(e, tabId)}
              onDragLeave={handleDragLeave}
              onClick={() => app.activateTab(group.id, tabId)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.3rem',
                padding: '0.2rem 0.5rem',
                fontSize: '0.75rem',
                fontWeight: isActive ? 600 : 400,
                color: isActive ? '#e2e8f0' : '#94a3b8',
                backgroundColor: isActive ? '#1e293b' : 'transparent',
                borderTopLeftRadius: '0.3rem',
                borderTopRightRadius: '0.3rem',
                cursor: 'grab',
                userSelect: 'none',
                whiteSpace: 'nowrap',
                border: dragOverTab === tabId && dropZone === 'middle' ? '1px dashed #60a5fa' : '1px solid transparent',
                transition: 'background-color 0.1s',
              }}
            >
              <span style={{ color: '#64748b', fontSize: '0.6rem' }}>☰</span>
              <span>{PANEL_TITLES[tabId] || tabId}</span>
              <span
                onClick={e => { e.stopPropagation(); app.closeTab(group.id, tabId); }}
                style={{ color: '#64748b', fontSize: '0.65rem', cursor: 'pointer', padding: '0 2px' }}
              >✕</span>
            </div>
          );
        })}
      </div>

      {/* Active tab content */}
      <div
        onDragOver={e => { if (e.dataTransfer.types.includes('application/mw-tab')) e.preventDefault(); }}
        style={{ flex: 1, overflow: 'auto', padding: '0.6rem', zIndex: dropZone && dropZone !== 'middle' ? 30 : 'auto' }}
      >
        {children(group.activeTab)}
      </div>
    </div>
  );
}
