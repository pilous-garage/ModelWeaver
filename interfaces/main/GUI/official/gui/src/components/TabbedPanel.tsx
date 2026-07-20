import React, { useRef, useState, useCallback } from 'react';
import type { AppApi, PanelGroupData } from '../useApp.ts';

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
  group: PanelGroupData;
  column: 'left' | 'center' | 'right';
  app: AppApi;
  children: (tabId: string) => React.ReactNode;
}

export function TabbedPanel({ group, column, app, children }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [dragOverTab, setDragOverTab] = useState<string | null>(null);
  const [dropZone, setDropZone] = useState<DropZone>(null);

  const EDGE_THRESHOLD = 0.10;

  const getDropZone = useCallback((e: React.DragEvent): DropZone => {
    const el = rootRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    if (x < EDGE_THRESHOLD) return 'left';
    if (x > 1 - EDGE_THRESHOLD) return 'right';
    if (y < EDGE_THRESHOLD) return 'top';
    if (y > 1 - EDGE_THRESHOLD) return 'bottom';
    return 'middle';
  }, []);

  const handleTabDragStart = (e: React.DragEvent, tabId: string) => {
    e.dataTransfer.setData('application/mw-tab', JSON.stringify({ tabId, fromGroup: group.id, fromCol: column }));
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleRootDragOver = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes('application/mw-tab')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const zone = getDropZone(e);
    setDropZone(zone);
  };

  const handleTabDragOver = (e: React.DragEvent, tabId: string) => {
    if (!e.dataTransfer.types.includes('application/mw-tab')) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const zone = getDropZone(e);
    setDropZone(zone);
    if (zone === 'middle') setDragOverTab(tabId);
    else setDragOverTab(null);
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
    const { tabId, fromGroup } = JSON.parse(raw);
    if (tabId === targetTabId && fromGroup === group.id) return;
    const idx = group.tabs.indexOf(targetTabId);
    app.moveTab(tabId, fromGroup, group.id, idx);
  };

  const handleBarDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDropZone(null);
    setDragOverTab(null);
    const raw = e.dataTransfer.getData('application/mw-tab');
    if (!raw) return;
    const { tabId, fromGroup } = JSON.parse(raw);
    if (fromGroup === group.id) return;
    app.moveTab(tabId, fromGroup, group.id);
  };

  const handleRootDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const zone = dropZone;
    setDropZone(null);
    setDragOverTab(null);
    const raw = e.dataTransfer.getData('application/mw-tab');
    if (!raw) return;
    const { tabId, fromGroup, fromCol } = JSON.parse(raw);

    if (zone === 'top' || zone === 'bottom') {
      const idx = app.panelGroups[column].findIndex(g => g.id === group.id);
      if (idx === -1) return;
      const insertIdx = zone === 'top' ? idx : idx + 1;
      if (fromCol === column && fromGroup === group.id && group.tabs.length === 1) return;
      app.moveTabToColumnAt(tabId, fromGroup, column, insertIdx);
    } else if (zone === 'left' || zone === 'right') {
      const adjCol = zone === 'left'
        ? (column === 'center' ? 'left' : column === 'right' ? 'center' : null)
        : (column === 'center' ? 'right' : column === 'left' ? 'center' : null);
      if (!adjCol || (fromCol === adjCol && fromGroup)) return;
      app.moveTabToNewGroup(tabId, fromGroup, adjCol);
    }
  };

  const handleDragLeave = () => {
    setDropZone(null);
    setDragOverTab(null);
  };

  const splitIndicatorStyle: React.CSSProperties = {
    position: 'absolute',
    left: 0,
    right: 0,
    height: '4px',
    backgroundColor: '#60a5fa',
    zIndex: 10,
    borderRadius: '2px',
    transition: 'opacity 0.1s',
    pointerEvents: 'none',
  };

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
      {/* Split indicators */}
      {dropZone === 'top' && <div style={{ ...splitIndicatorStyle, top: 0, left: 0, right: 0, height: '4px' }} />}
      {dropZone === 'bottom' && <div style={{ ...splitIndicatorStyle, bottom: 0, left: 0, right: 0, height: '4px' }} />}
      {dropZone === 'left' && <div style={{ ...splitIndicatorStyle, left: 0, top: 0, bottom: 0, width: '4px' }} />}
      {dropZone === 'right' && <div style={{ ...splitIndicatorStyle, right: 0, top: 0, bottom: 0, width: '4px' }} />}

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
        onDragOver={e => { if (e.dataTransfer.types.includes('application/mw-tab')) { e.preventDefault(); } }}
        style={{ flex: 1, overflow: 'auto', padding: '0.6rem' }}
      >
        {children(group.activeTab)}
      </div>
    </div>
  );
}
