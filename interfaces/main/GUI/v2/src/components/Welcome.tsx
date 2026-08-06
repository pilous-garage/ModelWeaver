import React from 'react';

/**
 * Bandeau d'accueil affiché dans une fenêtre VIDE (aucun onglet).
 * Propose des actions rapides équivalentes au menu (ouvrir une fenêtre,
 * nouvelle fenêtre vierge, nouveau mini-layout, ouvrir un panneau).
 */
export function Welcome(props: {
  t: (k: string) => string;
  onAction(action: string): void;
}) {
  const { t, onAction } = props;

  const actions: { label: string; action: string }[] = [
    { label: t('menu.ouvrirFenetre') ?? 'Ouvrir une fenêtre', action: 'window:open' },
    { label: t('menu.nouvelleFenetreVierge') ?? 'Nouvelle fenêtre vierge', action: 'window:new-blank' },
    { label: t('menu.miniLayout') ?? 'Mini-layout', action: 'mini-layout:add' },
  ];

  const buttonStyle: React.CSSProperties = {
    padding: '8px 16px', fontSize: 13, borderRadius: 8, cursor: 'pointer',
    background: 'var(--mw-bg-panel, #1e293b)', color: 'var(--mw-fg, #e2e8f0)',
    border: '1px solid var(--mw-border, #334155)', transition: 'border-color .15s, background .15s',
  };

  return (
    <div
      data-testid="mw-welcome"
      style={{
        height: '100%', display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', gap: 24,
        color: '#94a3b8', padding: 24,
      }}
    >
      <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--mw-fg, #e2e8f0)' }}>
        ModelWeaver
      </div>
      <div style={{ fontSize: 14, maxWidth: 420, textAlign: 'center' }}>
        Fenêtre vide — ouvrez une fenêtre, créez un mini-layout ou ajoutez des panneaux
        depuis le menu « Affichage ».
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'center' }}>
        {actions.map((a) => (
          <button
            key={a.action}
            data-testid={`welcome-${a.action.replace(/[^a-z0-9-]/gi, '-')}`}
            onClick={() => onAction(a.action)}
            onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--mw-accent, #3b82f6)'; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--mw-border, #334155)'; }}
            style={buttonStyle}
          >{a.label}</button>
        ))}
      </div>
    </div>
  );
}
