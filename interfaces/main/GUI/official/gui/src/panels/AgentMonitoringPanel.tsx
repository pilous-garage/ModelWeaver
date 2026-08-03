import React, { useState, useEffect } from 'react';
import { daemonPost } from '../bridge.ts';

interface AgentMetric {
  agent_id: string;
  total_tasks: number;
  total_tokens: number;
  failed_tasks: number;
  total_runtime_ms: number | null;
  avg_latency_ms: number | null;
  last_updated: string | null;
}

interface ServiceResource {
  name: string;
  pid: number;
  status: string;
  cpu_percent: number | null;
  memory_rss_mb: number | null;
}

export function AgentMonitoringPanel() {
  const [metrics, setMetrics] = useState<AgentMetric[]>([]);
  const [services, setServices] = useState<ServiceResource[]>([]);
  const [err, setErr] = useState('');

  useEffect(() => {
    const poll = async () => {
      try {
        const m = await daemonPost('agent/metrics', {});
        if (m?.result?.agents) setMetrics(m.result.agents);
        const s = await daemonPost('service/resources', {});
        if (s?.result?.services) setServices(s.result.services);
        setErr('');
      } catch (e: any) {
        setErr(e.message || 'daemon indisponible');
      }
    };
    poll();
    const h = setInterval(poll, 2000);
    return () => clearInterval(h);
  }, []);

  const fmtMs = (ms: number | null) => {
    if (ms == null) return '—';
    if (ms < 1000) return `${ms} ms`;
    return `${(ms / 1000).toFixed(1)} s`;
  };

  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem' }}>
      <h3 style={{ fontSize: '0.9rem', fontWeight: '600', marginBottom: '0.2rem' }}>Monitoring agents & services</h3>
      <div style={{ fontSize: '0.62rem', color: '#475569', marginBottom: '0.7rem', fontFamily: 'monospace' }}>mise à jour 2s</div>

      {err && <div style={{ color: '#fca5a5', fontSize: '0.7rem', marginBottom: '0.5rem' }}>{err}</div>}

      {/* Métriques agents */}
      <div style={{ marginBottom: '0.8rem' }}>
        <div style={{ fontSize: '0.75rem', fontWeight: '600', color: '#64748b', marginBottom: '0.3rem' }}>
          Agents ({metrics.length})
        </div>
        {metrics.length === 0 ? (
          <div style={{ color: '#94a3b8', fontSize: '0.7rem' }}>Aucun agent avec métriques</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            {metrics.map((a) => (
              <div key={a.agent_id} style={{
                backgroundColor: '#0f172a', borderRadius: '0.25rem', border: '1px solid #334155',
                padding: '0.3rem 0.5rem', fontSize: '0.68rem', display: 'flex', alignItems: 'center', gap: '0.5rem'
              }}>
                <span style={{ color: '#6ee7b7', fontWeight: '600', minWidth: '3rem' }}>{a.agent_id}</span>
                <span style={{ color: '#64748b' }}>tâches <span style={{ color: '#e2e8f0' }}>{a.total_tasks}</span></span>
                <span style={{ color: '#64748b' }}>tokens <span style={{ color: '#e2e8f0' }}>{a.total_tokens}</span></span>
                <span style={{ color: a.failed_tasks > 0 ? '#fca5a5' : '#64748b' }}>
                  échecs <span style={{ color: a.failed_tasks > 0 ? '#fca5a5' : '#e2e8f0' }}>{a.failed_tasks}</span>
                </span>
                <span style={{ color: '#64748b' }}>latence moy <span style={{ color: '#fbbf24' }}>{fmtMs(a.avg_latency_ms)}</span></span>
                <span style={{ color: '#64748b' }}>runtime <span style={{ color: '#e2e8f0' }}>{fmtMs(a.total_runtime_ms)}</span></span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Ressources services (CPU/RAM) */}
      <div>
        <div style={{ fontSize: '0.75rem', fontWeight: '600', color: '#64748b', marginBottom: '0.3rem' }}>
          Services ({services.length})
        </div>
        {services.length === 0 ? (
          <div style={{ color: '#94a3b8', fontSize: '0.7rem' }}>Aucun service actif détecté</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            {services.map((s) => (
              <div key={s.name} style={{
                backgroundColor: '#0f172a', borderRadius: '0.25rem', border: '1px solid #334155',
                padding: '0.3rem 0.5rem', fontSize: '0.68rem', display: 'flex', alignItems: 'center', gap: '0.5rem'
              }}>
                <span style={{ color: '#6ee7b7', fontWeight: '600', minWidth: '6rem' }}>{s.name}</span>
                <span style={{
                  color: s.status === 'running' ? '#22c55e' : '#f59e0b', fontSize: '0.62rem'
                }}>{s.status}</span>
                <span style={{ color: '#64748b' }}>PID <span style={{ color: '#e2e8f0' }}>{s.pid}</span></span>
                <span style={{ color: '#64748b' }}>
                  CPU <span style={{ color: s.cpu_percent != null ? (s.cpu_percent > 50 ? '#f59e0b' : '#22c55e') : '#64748b' }}>
                    {s.cpu_percent != null ? `${s.cpu_percent}%` : '—'}
                  </span>
                </span>
                <span style={{ color: '#64748b' }}>
                  RAM <span style={{ color: '#e2e8f0' }}>{s.memory_rss_mb != null ? `${s.memory_rss_mb} MB` : '—'}</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
