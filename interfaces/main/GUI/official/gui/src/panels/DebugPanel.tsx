import React from 'react';
import type { AppApi } from '../useApp.ts';

export function DebugPanel({ app }: { app: AppApi }) {
  return (
    <div style={{ width: '360px', display: 'flex', flexDirection: 'column', gap: '0.75rem', overflow: 'hidden', borderLeft: '1px solid #334155', paddingLeft: '1rem' }}>
      {/* Bandeau de panneaux supplémentaires */}
      <div style={{ display: 'flex', gap: '0.4rem' }}>
        {(['process', 'services', 'logs', 'resources'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => app.setDebugTab(tab)}
            style={{ flex: 1, padding: '0.35rem', fontSize: '0.7rem', borderRadius: '0.3rem', cursor: 'pointer', backgroundColor: app.debugTab === tab ? '#2563eb' : '#334155', color: '#e2e8f0', border: 'none' }}
          >
            {tab === 'process' ? 'Processus' : tab === 'services' ? 'Services' : tab === 'logs' ? 'Logs' : 'Ressources'}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', backgroundColor: '#1e293b', borderRadius: '0.375rem', border: '1px solid #334155', padding: '0.6rem' }}>
        {app.debugTab === 'process' && (
          <>
            <div style={{ fontSize: '0.72rem', color: '#64748b', marginBottom: '0.5rem' }}>Arbre des processus (tick 1 Hz)</div>
            {app.procList.length === 0 ? (
              <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Aucun processus suivi</div>
            ) : (
              app.procList.map((p) => {
                const byId: any = {};
                app.procList.forEach((x) => { byId[x.id] = x; });
                let d = 0; let cur = p.parent_id;
                while (cur && byId[cur]) { d++; cur = byId[cur].parent_id; }
                const stColor = p.status === 'running' ? '#6ee7b7' : p.status === 'failed' ? '#f87171' : p.status === 'cancelled' ? '#fbbf24' : '#94a3b8';
                return (
                  <div key={p.id} style={{ marginLeft: d * 14, padding: '0.3rem 0', borderBottom: '1px solid #334155', fontSize: '0.72rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                      <span style={{ fontWeight: '600', color: '#e2e8f0' }}>{p.name}</span>
                      <span style={{ color: stColor }}>● {p.status}</span>
                      {p.pid ? <span style={{ color: '#64748b' }}>pid {p.pid}</span> : null}
                      <span style={{ color: '#64748b', marginLeft: 'auto' }}>{p.cpu.toFixed(1)}% · {(p.rss_kb / 1024).toFixed(0)} Mo</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginTop: '0.15rem' }}>
                      <span style={{ color: '#64748b', fontSize: '0.65rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '70%' }}>{p.command}</span>
                      <button onClick={() => app.fetchProcLog(p.id)} style={{ marginLeft: 'auto', fontSize: '0.62rem', padding: '0.1rem 0.4rem', backgroundColor: '#334155', color: '#cbd5e1', border: '1px solid #475569', borderRadius: '0.25rem', cursor: 'pointer' }}>logs</button>
                    </div>
                    {app.procLogId === p.id && (
                      <pre style={{ marginTop: '0.3rem', maxHeight: '160px', overflowY: 'auto', backgroundColor: '#0f172a', color: '#a5b4fc', fontSize: '0.62rem', padding: '0.4rem', borderRadius: '0.25rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{app.procLogText || '(vide)'}</pre>
                    )}
                  </div>
                );
              })
            )}
          </>
        )}
        {app.debugTab === 'services' && (
          <>
            <div style={{ fontSize: '0.72rem', color: '#64748b', marginBottom: '0.5rem' }}>Services supervisés (auto-redémarrage si crash)</div>
            {app.serviceList.length === 0 ? (
              <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Aucun service déclaré</div>
            ) : (
          app.serviceList.map((s) => {
            const stColor = s.status === 'running' ? '#6ee7b7' : s.status === 'restarting' ? '#fbbf24' : '#f87171';
            return (
              <div key={s.name} style={{ padding: '0.3rem 0', borderBottom: '1px solid #334155', fontSize: '0.72rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <span style={{ fontWeight: '600', color: '#e2e8f0' }}>{s.name}</span>
                  <span style={{ color: stColor }}>● {s.status}</span>
                  {s.pid ? <span style={{ color: '#64748b' }}>pid {s.pid}</span> : null}
                  <span style={{ color: '#64748b', marginLeft: 'auto' }}>↻ {s.restarts}</span>
                  <button onClick={() => app.fetchServiceLog(s.name)} style={{ marginLeft: '0.3rem', fontSize: '0.62rem', padding: '0.1rem 0.4rem', backgroundColor: '#334155', color: '#cbd5e1', border: '1px solid #475569', borderRadius: '0.25rem', cursor: 'pointer' }}>logs</button>
                </div>
                <div style={{ color: '#64748b', fontSize: '0.65rem', marginTop: '0.1rem' }}>
                  {s.mode}{s.last_exit != null ? ` · exit ${s.last_exit}` : ''} · démarré {new Date(s.started_at * 1000).toLocaleTimeString()}
                </div>
                {app.svcLogName === s.name && (
                  <pre style={{ marginTop: '0.3rem', maxHeight: '160px', overflowY: 'auto', backgroundColor: '#0f172a', color: '#a5b4fc', fontSize: '0.62rem', padding: '0.4rem', borderRadius: '0.25rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{app.svcLogText || '(vide)'}</pre>
                )}
              </div>
            );
          })
            )}
          </>
        )}
        {app.debugTab === 'logs' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', height: '100%' }}>
            <div style={{ fontSize: '0.66rem', color: '#64748b' }}>Logs d'install / dépendances</div>
            {app.installLog.length === 0 ? (
              <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Aucun log pour l'instant — les actions d'install généreront des entrées ici.</div>
            ) : (
              <div style={{ backgroundColor: '#0f172a', borderRadius: '0.375rem', border: '1px solid #334155', padding: '0.6rem', maxHeight: '100%', overflowY: 'auto', fontFamily: 'monospace', fontSize: '0.66rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {app.installLog.map((line, i) => (
                  <div key={i} style={{ color: line.includes('FAILED') || line.includes('ERROR') ? '#f87171' : line.includes('SUCCESS') ? '#6ee7b7' : '#94a3b8' }}>{line}</div>
                ))}
              </div>
            )}
          </div>
        )}
        {app.debugTab === 'resources' && (
          <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>Vue ressources globale (CPU/RAM/bande passante agrégée) — à venir.</div>
        )}
      </div>
    </div>
  );
}
