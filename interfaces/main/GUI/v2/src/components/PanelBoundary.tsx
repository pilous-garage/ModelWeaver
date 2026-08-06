// PanelBoundary — Error Boundary par panel : un panel qui crash en rendu
// affiche un cadre d'erreur au lieu de faire tomber TOUTE l'application.
// Indispensable pour les panels externes (contrat V1) non isolés.

import React from 'react';

interface Props {
  panelId: string;
  children: React.ReactNode;
}

interface State {
  error: string | null;
}

export class PanelBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(e: any): State {
    return { error: String(e?.message ?? e) };
  }

  componentDidCatch(error: any, info: any) {
    console.error(`[panel:${this.props.panelId}] erreur de rendu:`, error, info);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return (
        <div className="mw-panel-error" data-testid={`panel-error-${this.props.panelId}`}
          style={{ padding: 12, fontSize: 12, color: '#f87171', background: 'var(--mw-bg-panel, #1e293b)', height: '100%', overflow: 'auto', boxSizing: 'border-box' }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Erreur du panel {this.props.panelId}</div>
          <div style={{ color: '#fecaca', whiteSpace: 'pre-wrap' }}>{this.state.error}</div>
          <button className="mw-btn" onClick={this.reset} style={{ marginTop: 8, fontSize: 11 }}>
            Réessayer
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
