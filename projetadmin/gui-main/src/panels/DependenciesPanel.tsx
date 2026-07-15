import React from 'react';
import type { AppApi } from '../useApp.ts';
import { Spinner } from '../components/ui.tsx';

export function DependenciesPanel({ app }: { app: AppApi }) {
  return (
    <div style={{
      height: '100vh',
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: '#0f172a',
      color: '#e2e8f0',
      fontFamily: 'sans-serif',
      padding: '2rem',
      overflowY: 'auto',
    }}>
      <style>{`@keyframes mw-spin { to { transform: rotate(360deg); } }`}</style>
      <div style={{ maxWidth: '800px', width: '100%', margin: '0 auto' }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 'bold', marginBottom: '2rem' }}>
          ModelWeaver
        </h1>

        {/* Dependencies panel */}
        <div style={{
          backgroundColor: '#1e293b',
          borderRadius: '0.5rem',
          border: '1px solid #334155',
          padding: '1.5rem',
          marginBottom: '1.5rem',
        }}>
          <h3 style={{ fontSize: '1rem', fontWeight: '600', marginBottom: '1rem' }}>System Dependencies</h3>

          {app.checking && <p style={{ color: '#94a3b8' }}>Checking dependencies...</p>}

          {!app.checking && app.error && (
            <div style={{ color: '#fca5a5', marginBottom: '1rem' }}>Error: {app.error}</div>
          )}

          {/* Required dependencies */}
          <div style={{ marginBottom: app.recommendedDeps.length > 0 ? '1.5rem' : '0' }}>
            <h4 style={{ fontSize: '0.875rem', fontWeight: '600', color: '#94a3b8', marginBottom: '0.75rem' }}>Required</h4>
            {app.requiredDeps.map((dep) => (
              <div key={dep.name} style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.5rem 0',
                borderBottom: '1px solid #334155',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <input
                    type="checkbox"
                    checked
                    disabled

                    style={{
                      width: '16px',
                      height: '16px',
                      cursor: 'not-allowed',
                      opacity: 1,
                    }}
                  />
                  <div>
                    <div style={{ fontWeight: '500' }}>{dep.name}</div>
                    <div style={{ fontSize: '0.75rem', color: '#64748b' }}>{dep.description}</div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {dep.installed ? (
                    <span style={{ color: '#6ee7b7' }}>✓ Installé</span>
                  ) : (
                    <span style={{ color: '#f59e0b' }} title={dep.target_pkg}>⚠ Sera installé</span>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Recommended dependencies */}
          {app.recommendedDeps.length > 0 && (
            <div>
              <h4 style={{ fontSize: '0.875rem', fontWeight: '600', color: '#94a3b8', marginBottom: '0.75rem' }}>Recommended</h4>
              {app.recommendedDeps.map((dep) => (
                <div key={dep.name} style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '0.5rem 0',
                  borderBottom: '1px solid #334155',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <input
                      type="checkbox"
                      checked={dep.installed || app.selectedRecommended[dep.name] || false}
                      onChange={() => app.handleRecommendedToggle(dep.name)}
                      disabled={dep.installed}
                      style={{
                        width: '16px',
                        height: '16px',
                        cursor: dep.installed ? 'not-allowed' : 'pointer',
                        opacity: dep.installed ? 1 : 0.8,
                      }}
                    />
                    <div>
                      <div style={{ fontWeight: '500' }}>{dep.name}</div>
                      <div style={{ fontSize: '0.75rem', color: '#64748b' }}>{dep.description}</div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    {dep.installed ? (
                      <span style={{ color: '#6ee7b7' }}>✓ Installé</span>
                    ) : (
                      <span style={{ color: '#f59e0b' }} title={dep.target_pkg}>⚠ Optionnel</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: '1rem', marginTop: '1rem' }}>
          <button
            onClick={() => app.withFeedback('install-deps', app.handleInstall)}
            disabled={app.allRequiredInstalled || app.installing || app.loadingActions['install-deps']}
            style={{
              padding: '0.5rem 1rem',
              backgroundColor: (app.allRequiredInstalled || app.installing || app.loadingActions['install-deps']) ? '#475569' : '#3b82f6',
              color: 'white',
              border: 'none',
              borderRadius: '0.375rem',
              cursor: (app.allRequiredInstalled || app.installing || app.loadingActions['install-deps']) ? 'not-allowed' : 'pointer',
              fontWeight: '500',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.4rem',
            }}
          >
            {(app.installing || app.loadingActions['install-deps']) && <Spinner />}
            {app.installing
              ? `Installing... (${app.installProgress.filter(p => p.status === 'success').length}/${app.installProgress.length})`
              : app.allRequiredInstalled ? 'All dependencies installed' : 'Install Selected'}
          </button>
          <button
            onClick={app.handleQuit}
            style={{
              padding: '0.5rem 1rem',
              backgroundColor: '#ef4444',
              color: 'white',
              border: 'none',
              borderRadius: '0.375rem',
              cursor: 'pointer',
              fontWeight: '500',
            }}
          >
            Refuse and Quit
          </button>
        </div>

        {/* Installation progress (auto-opening dropdown) */}
        {(app.installProgress.length > 0 || app.installing) && (
          <div style={{
            marginTop: '1rem',
            backgroundColor: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '0.375rem',
            overflow: 'hidden',
          }}>
            <button
              onClick={() => app.setInstallPanelOpen(!app.installPanelOpen)}
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.75rem 1rem',
                backgroundColor: '#1e293b',
                color: '#e2e8f0',
                border: 'none',
                cursor: 'pointer',
                fontSize: '0.875rem',
                fontWeight: '600',
              }}
            >
              <span>
                {app.installing
                  ? `⏳ Installation... (${app.installProgress.filter(p => p.status === 'success').length}/${app.installProgress.length})`
                  : '✓ Installation terminée'}
                {' '}({app.installProgress.filter(p => p.status === 'success').length}/{app.installProgress.length})
              </span>
              <span style={{ transition: 'transform 0.2s', transform: app.installPanelOpen ? 'rotate(180deg)' : 'rotate(0deg)' }}>▾</span>
            </button>
            {app.installPanelOpen && (
              <div style={{ padding: '0 1rem 1rem' }}>
                {app.installProgress.map((p) => {
                  const badge = p.status === 'installing' ? { t: '⏳', c: '#fbbf24' }
                    : p.status === 'success' ? { t: '✓', c: '#6ee7b7' }
                    : p.status === 'failed' ? { t: '✗', c: '#f87171' }
                    : { t: '•', c: '#94a3b8' };
                  return (
                    <div key={p.name} style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      padding: '0.35rem 0',
                      borderBottom: '1px solid #334155',
                      fontSize: '0.8rem',
                    }}>
                      <span style={{ color: badge.c, fontWeight: '700', width: '1.2rem' }}>{badge.t}</span>
                      <span style={{ fontWeight: '500', minWidth: '6rem' }}>{p.name}</span>
                      <span style={{ color: '#94a3b8', fontSize: '0.7rem', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.detail}</span>
                    </div>
                  );
                })}
                {/* Raw log under the list */}
                {app.installLog.length > 0 && (
                  <div style={{
                    marginTop: '0.75rem',
                    backgroundColor: '#0f172a',
                    border: '1px solid #334155',
                    borderRadius: '0.375rem',
                    padding: '0.75rem',
                    maxHeight: '160px',
                    overflowY: 'auto',
                    fontFamily: 'monospace',
                    fontSize: '0.7rem',
                  }}>
                    {app.installLog.map((line, i) => (
                      <div key={i} style={{ color: line.includes('FAILED') || line.includes('ERROR') ? '#f87171' : line.includes('SUCCESS') ? '#6ee7b7' : '#94a3b8' }}>
                        {line}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Status */}
        {!app.checking && !app.error && (
          <div style={{ fontSize: '0.875rem', color: app.allRequiredInstalled ? '#6ee7b7' : '#fca5a5', marginTop: '1rem' }}>
            Status: {app.allRequiredInstalled ? '✓ All required dependencies are installed' : '✗ Missing required dependencies'}
          </div>
        )}
      </div>
    </div>
  );
}
