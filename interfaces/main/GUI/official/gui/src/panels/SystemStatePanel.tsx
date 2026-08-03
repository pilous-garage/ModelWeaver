import React, { useEffect, useState } from 'react';
import type { AppApi } from '../useApp.ts';
import { daemonPost } from '../bridge.ts';

interface HardwareData {
  cpu?: any;
   memory?: any;
   motherboard?: any;
   gpus?: any[];
   disks?: any[];
   network?: any[];
   usb_count?: number;
   thermal?: any;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '0.6rem' }}>
      <div style={{ fontSize: '0.68rem', fontWeight: '600', color: '#64748b', marginBottom: '0.2rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{title}</div>
      <div style={{ fontSize: '0.72rem', color: '#e2e8f0' }}>{children}</div>
    </div>
  );
}

function Row({ k, v, color }: { k: string; v: any; color?: string }) {
  if (v == null || v === '' || v === 0) return null;
  return (
    <div style={{ lineHeight: '1.5' }}>
      <span style={{ color: '#64748b' }}>{k} : </span>
      <span style={{ color: color || '#e2e8f0' }}>{String(v)}</span>
    </div>
  );
}

function Tag({ children, color }: { children: React.ReactNode; color?: string }) {
  return (
    <span style={{
      fontSize: '0.65rem', backgroundColor: '#0f172a', border: '1px solid #334155',
      borderRadius: '0.25rem', padding: '0.1rem 0.4rem', margin: '0.1rem 0.2rem 0.1rem 0',
      color: color || '#6ee7b7', display: 'inline-block',
    }}>{children}</span>
  );
}

export function SystemStatePanel({ app, initialHardware }: { app: AppApi; initialHardware?: HardwareData }) {
  const [hw, setHw] = useState<HardwareData | null>(initialHardware || null);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (initialHardware) return;
    let alive = true;
    const load = async () => {
      try {
        const data = await daemonPost('system/hardware', {});
        if (alive) setHw(data?.result || {});
      } catch (e: any) {
        if (alive) setErr(e.message || 'daemon indisponible');
      }
    };
    load();
    return () => { alive = false; };
  }, []);

  const cpu = hw?.cpu || {};
  const mem = hw?.memory || {};
  const mobo = hw?.motherboard || {};
  const bios = mobo.bios || {};

  return (
    <div style={{ backgroundColor: '#1e293b', borderRadius: '0.5rem', border: '1px solid #334155', padding: '1rem', overflow: 'auto' }}>
      <h3 style={{ fontSize: '0.9rem', fontWeight: '600', marginBottom: '0.4rem' }}>État du système</h3>
      <div style={{ fontSize: '0.62rem', color: '#475569', marginBottom: '0.6rem', fontFamily: 'monospace' }}>inventaire matériel · check complet</div>

      {err && <div style={{ color: '#fca5a5', fontSize: '0.7rem', marginBottom: '0.5rem' }}>{err}</div>}

      {!hw && !err ? (
        <div style={{ color: '#94a3b8', fontSize: '0.8rem' }}>Chargement…</div>
      ) : (
        <div>
          <Section title="Système">
            <Row k="OS" v={`${cpu.architecture ? cpu.architecture + ' ' : ''}${app.systemState?.os || ''}`} />
            <Row k="Noyau" v={app.systemState?.os_release} />
            <Row k="Build" v={app.systemState?.os_version} />
          </Section>

          <Section title="Processeur">
            <Row k="Modèle" v={cpu.model} color="#93c5fd" />
            <Row k="Cœurs logiques" v={cpu.cores_logiques} />
            {cpu.threads_total && <Row k="Threads (logiques)" v={cpu.threads_total} />}
            {cpu.cores_physiques && <Row k="Cœurs physiques / socket" v={cpu.cores_physiques} />}
            {cpu.sockets && <Row k="Socket(s)" v={cpu.sockets} />}
            {cpu.freq_max_mhz && <Row k="Fréquence max" v={`${Number(cpu.freq_max_mhz).toFixed(0)} MHz`} />}
            {cpu.freq_min_mhz && <Row k="Fréquence min" v={`${Number(cpu.freq_min_mhz).toFixed(0)} MHz`} />}
            {cpu.cache_l2 && <Row k="Cache L2" v={cpu.cache_l2} />}
            <Row k="Virtualisation" v={cpu.virtualization} />
          </Section>

          <Section title="Mémoire">
            {mem.ram_total_gb != null && <Row k="RAM totale" v={`${mem.ram_total_gb} Go`} />}
            {mem.swap_total_gb != null && mem.swap_total_gb > 0 && <Row k="Swap" v={`${mem.swap_total_gb} Go`} />}
          </Section>

          <Section title="Carte mère / BIOS">
            <Row k="Fabricant" v={mobo.manufacturer} />
            <Row k="Modèle" v={mobo.product} color="#93c5fd" />
            {mobo.serial && <Row k="Série" v={mobo.serial} />}
            {mobo.error && !mobo.manufacturer && !mobo.product && <div style={{ color: '#fca5a5', fontSize: '0.7rem' }}>{mobo.error}</div>}
            {bios.vendor && (
              <Row k="BIOS" v={`${bios.vendor} ${bios.version || ''}${bios.date ? ' · ' + bios.date : ''}`} />
            )}
          </Section>

          <Section title="GPU">
            {(hw?.gpus || []).length === 0 && <span style={{ color: '#94a3b8' }}>aucun GPU dédié (APU/iGPU intégré)</span>}
            {(hw?.gpus || []).map((g: any, i: number) => (
              <div key={i} style={{ marginBottom: '0.3rem' }}>
                <div>{g.description}</div>
                {g.vram_total_mb && <div style={{ color: '#64748b', fontSize: '0.65rem' }}>VRAM {g.vram_total_mb} MB{g.temp_c ? ` · ${g.temp_c}°C` : ''}</div>}
              </div>
            ))}
           </Section>

           <Section title="Stockage">
            {(hw?.disks || []).map((d: any, i: number) => (
              <div key={i} style={{ lineHeight: '1.5' }}>
                <Tag>{d.interface || 'disk'}</Tag>
                <span style={{ color: '#e2e8f0' }}>{d.name} — {d.size_gb} Go</span>
                {d.model && <span style={{ color: '#64748b', fontSize: '0.65rem' }}> · {d.model}</span>}
                {d.serial && <span style={{ color: '#64748b', fontSize: '0.65rem' }}> · SN {d.serial}</span>}
              </div>
            ))}
          </Section>

          <Section title="Réseau">
            {(hw?.network || []).map((n: any, i: number) => (
              <div key={i} style={{ lineHeight: '1.5' }}>
                <Tag color={n.state === 'up' ? '#6ee7b7' : '#94a3b8'}>{n.state || '?'}</Tag>
                <span style={{ color: '#e2e8f0' }}>{n.name}</span>
                <span style={{ color: '#64748b', fontSize: '0.65rem' }}>
                  {' '}· {n.type}{n.speed_mbps && n.speed_mbps > 0 ? ` · ${n.speed_mbps} Mb/s` : ''}{n.mac ? ` · ${n.mac}` : ''}
                </span>
              </div>
            ))}
          </Section>

          <Section title="Gestionnaires">
            {(app.systemState?.detected_managers || []).map((pm: string) => (
              <Tag key={pm}>{pm}</Tag>
            ))}
            {(app.systemState?.detected_managers || []).length === 0 && <span style={{ color: '#fca5a5' }}>aucun</span>}
          </Section>

          {hw?.usb_count != null && (
            <Section title="Périphériques">
              <span style={{ color: '#94a3b8' }}>{hw.usb_count} périphériques USB</span>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}
