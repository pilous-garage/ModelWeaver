import React, { useRef } from 'react';
import type { AppApi } from '../useApp.ts';

interface Props {
  id: string;
  title: string;
  app: AppApi;
  children: React.ReactNode;
  defaultCollapsed?: boolean;
  className?: string;
}

export function PanelContainer({ id, title, app, children, defaultCollapsed = false, className = '' }: Props) {
  const collapsed = app.collapsedPanels[id] ?? defaultCollapsed;
  const dragRef = useRef<HTMLDivElement>(null);

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData('text/plain', id);
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const draggedId = e.dataTransfer.getData('text/plain');
    if (draggedId && draggedId !== id) {
      app.movePanel(draggedId, id);
    }
  };

  return (
    <div
      draggable
      onDragStart={handleDragStart}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      style={{
        backgroundColor: '#1e293b',
        borderRadius: '0.5rem',
        border: '1px solid #334155',
        display: 'flex',
        flexDirection: 'column',
        minHeight: collapsed ? 'auto' : 0,
      }}
    >
      <div
        ref={dragRef}
        onClick={() => app.togglePanelCollapse(id)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.4rem',
          padding: '0.4rem 0.6rem',
          cursor: 'grab',
          userSelect: 'none',
          borderBottom: collapsed ? 'none' : '1px solid #334155',
        }}
      >
        <span style={{ color: '#64748b', fontSize: '0.7rem', cursor: 'grab' }}>⠿</span>
        <span style={{ fontSize: '0.82rem', fontWeight: 600, flex: 1, color: '#e2e8f0' }}>{title}</span>
        <span style={{ color: '#64748b', fontSize: '0.7rem', cursor: 'pointer' }}>
          {collapsed ? '▸' : '▾'}
        </span>
      </div>
      {!collapsed && (
        <div style={{ padding: '0.6rem' }}>
          {children}
        </div>
      )}
    </div>
  );
}
