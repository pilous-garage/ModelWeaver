import React from 'react';
import type { AppApi, PanelNode, PanelSplit, PanelGroup as PG } from '../useApp.ts';
import { TabbedPanel } from './TabbedPanel.tsx';

interface Props {
  node: PanelNode;
  app: AppApi;
  renderTab: (tabId: string) => React.ReactNode;
}

export function PanelTreeRenderer({ node, app, renderTab }: Props) {
  if (!('direction' in node)) {
    return <TabbedPanel group={node} app={app}>
      {tabId => renderTab(tabId)}
    </TabbedPanel>;
  }
  const split = node as PanelSplit;
  return (
    <div style={{
      display: 'flex',
      flexDirection: split.direction === 'horizontal' ? 'row' : 'column',
      flex: 1,
      minHeight: 0,
      minWidth: 0,
      gap: '3px',
    }}>
      {split.children.map((child, i) => (
        <React.Fragment key={i}>
          {i > 0 && (
            <div style={{
              [split.direction === 'horizontal' ? 'width' : 'height']: '3px',
              backgroundColor: '#334155',
              borderRadius: '2px',
              flexShrink: 0,
            }} />
          )}
          <div style={{
            flex: 1,
            minHeight: 0,
            minWidth: 0,
            overflow: 'hidden',
            display: 'flex',
          }}>
            <PanelTreeRenderer node={child} app={app} renderTab={renderTab} />
          </div>
        </React.Fragment>
      ))}
    </div>
  );
}
