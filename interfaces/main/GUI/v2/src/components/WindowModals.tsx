import React, { useEffect, useRef, useState } from 'react';
import { registerWindow } from '../windows.ts';

// ── Overlay commun ────────────────────────────────────────────────────

const overlayStyle: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 2000,
  background: 'rgba(0,0,0,.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
};

const modalStyle: React.CSSProperties = {
  background: 'var(--mw-bg-panel, #1e293b)', color: 'var(--mw-fg, #e2e8f0)',
  border: '1px solid var(--mw-border, #334155)', borderRadius: 10,
  padding: 18, minWidth: 380, boxShadow: '0 8px 32px rgba(0,0,0,.5)',
};

// ── Enregistrer la fenêtre ────────────────────────────────────────────

/**
 * Modale « Enregistrer la fenêtre » : choisit un nom/id pour la fenêtre
 * courante. Vérifie l'unicité contre les fenêtres ENREGISTRÉES existantes.
 * Enregistrer = RENOMMER (l'occurrence vivante disparaît, devient enregistrée).
 */
export function RegisterWindowModal(props: {
  windowId: string;
  currentTitle: string;
  registeredNames: string[];
  onClose(): void;
  onDone(title: string): void;
}) {
  const { windowId, currentTitle, registeredNames, onClose, onDone } = props;
  const [name, setName] = useState(currentTitle);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); inputRef.current?.select(); }, []);

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) { setError('Le nom ne peut pas être vide'); return; }
    if (registeredNames.includes(trimmed)) {
      setError(`Une fenêtre enregistrée nommée « ${trimmed} » existe déjà.`);
      return;
    }
    const ok = await registerWindow(windowId, trimmed);
    if (ok) onDone(trimmed);
    else setError("Échec de l'enregistrement (daemon).");
  };

  return (
    <div style={overlayStyle} data-testid="register-modal" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={modalStyle}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10 }}>
          Enregistrer la fenêtre
        </div>
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 8 }}>
          Choisissez le nom de la fenêtre. L'enregistrement remplace l'occurrence
          vivante « {windowId} » par cette fenêtre enregistrée.
        </div>
        <input
          ref={inputRef}
          value={name}
          data-testid="register-input"
          onChange={(e) => { setName(e.target.value); setError(null); }}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') onClose(); }}
          style={{
            width: '100%', boxSizing: 'border-box', fontSize: 13, padding: '6px 8px',
            background: 'var(--mw-bg, #0f172a)', color: 'var(--mw-fg, #e2e8f0)',
            border: `1px solid ${error ? '#f87171' : 'var(--mw-border, #334155)'}`, borderRadius: 6,
          }}
        />
        {error && <div data-testid="register-error" style={{ color: '#f87171', fontSize: 11, marginTop: 6 }}>{error}</div>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 14 }}>
          <button
            onClick={onClose}
            style={{ padding: '5px 12px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'transparent', color: '#94a3b8', border: '1px solid var(--mw-border, #334155)' }}
          >Annuler</button>
          <button
            data-testid="register-submit"
            onClick={submit}
            style={{ padding: '5px 12px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--mw-accent, #3b82f6)', color: '#fff', border: 'none' }}
          >Enregistrer</button>
        </div>
      </div>
    </div>
  );
}

// ── À propos (Aide) ───────────────────────────────────────────────────

/** Modale « À propos » — texte provisoire (lorem ipsum). */
export function AboutModal(props: { onClose(): void }) {
  const { onClose } = props;
  return (
    <div style={overlayStyle} data-testid="about-modal" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={{ ...modalStyle, maxWidth: 460 }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 8 }}>ModelWeaver</div>
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 10 }}>Version 0.9.0</div>
        <div style={{ fontSize: 12, lineHeight: 1.6, color: 'var(--mw-fg-dim, #cbd5e1)' }}>
          Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed non risus.
          Suspendisse lectus tortor, dignissim sit amet, adipiscing nec, ultricies sed, dolor.
          Cras elementum ultrices diam. Maecenas ligula massa, varius a, semper congue, euismod
          non, mi. Proin porttitor, orci nec nonummy molestie, enim est eleifend mi, non
          fermentum diam nisl sit amet erat.
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}>
          <button data-testid="about-close" onClick={onClose}
            style={{ padding: '5px 14px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--mw-accent, #3b82f6)', color: '#fff', border: 'none' }}>
            OK
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Session (renommer) ────────────────────────────────────────────────

/**
 * Modale « Renommer la session » : le nom change, pas l'id.
 * (Le thème de session se règle via Affichage → Thèmes → Thème de la session.)
 */
export function SessionModal(props: {
  session: { id: string; name?: string } | null;
  onClose(): void;
  onDone(value: string): void;
}) {
  const { session, onClose, onDone } = props;
  const [value, setValue] = useState(session?.name ?? session?.id ?? '');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); inputRef.current?.select(); }, []);

  const submit = () => {
    const v = value.trim();
    if (!v) return;
    onDone(v);
  };

  return (
    <div style={overlayStyle} data-testid="session-modal" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={modalStyle}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10 }}>
          Renommer la session
        </div>
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 8 }}>
          L'identifiant ({session?.id}) ne change pas ; seul le nom affiché est modifié.
        </div>
        <input
          ref={inputRef}
          value={value}
          data-testid="session-rename-input"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') onClose(); }}
          style={{ width: '100%', boxSizing: 'border-box', fontSize: 13, padding: '6px 8px', background: 'var(--mw-bg, #0f172a)', color: 'var(--mw-fg, #e2e8f0)', border: '1px solid var(--mw-border, #334155)', borderRadius: 6 }}
        />
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 14 }}>
          <button onClick={onClose} style={{ padding: '5px 12px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'transparent', color: '#94a3b8', border: '1px solid var(--mw-border, #334155)' }}>Annuler</button>
          <button data-testid="session-submit" onClick={submit} style={{ padding: '5px 12px', fontSize: 12, borderRadius: 6, cursor: 'pointer', background: 'var(--mw-accent, #3b82f6)', color: '#fff', border: 'none' }}>OK</button>
        </div>
      </div>
    </div>
  );
}
