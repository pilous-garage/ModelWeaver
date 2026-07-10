import React, { useState } from 'react';
import { invoke } from '@tauri-apps/api/core';

type Step = 'WELCOME' | 'TOS' | 'DOWNLOADING' | 'SYS_INSTALL' | 'FINALIZING' | 'DASHBOARD';

function App() {
  const [step, setStep] = useState<Step>('WELCOME');
  const [loading, setLoading] = useState(false);
  const [statusMsg, setStatusMsg] = useState('');
  const [error, setError] = useState<string | null>(null);

  const INSTALL_DIR = '.modelweaver';
  const RELEASE_URL = 'https://github.com/anomalyco/ModelWeaver/releases/latest/download/modelweaver_client.tar.gz';

  const nextStep = (next: Step) => {
    setError(null);
    setStep(next);
  };

  const handleBootstrap = async () => {
    setLoading(true);
    setError(null);

    try {
      // 1. Téléchargement et Extraction
      setStatusMsg('Téléchargement du projet...');
      await invoke('download_and_unpack', { url: RELEASE_URL, installDir: INSTALL_DIR });
      
      // 2. Installation des dépendances système (Python, SQLite)
      setStatusMsg('Installation des dépendances système (Python, SQLite)...');
      await invoke('run_bootstrap_script', { installDir: INSTALL_DIR });
      
      // 3. Finalisation
      setStep('FINALIZING');
    } catch (e) {
      setError(`Erreur lors du bootstrap: ${e}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ 
      height: '100vh', 
      display: 'flex', 
      alignItems: 'center', 
      justifyContent: 'center', 
      backgroundColor: '#0f172a', 
      color: 'white',
      fontFamily: 'Inter, system-ui, sans-serif'
    }}>
      <div style={{ 
        maxWidth: '500px', 
        width: '90%', 
        padding: '2.5rem', 
        backgroundColor: '#1e293b', 
        borderRadius: '1.5rem', 
        boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
        textAlign: 'center'
      }}>
        
        {step === 'WELCOME' && (
          <div className="fade-in">
            <h1 style={{ fontSize: '2rem', marginBottom: '1rem', fontWeight: 'bold' }}>ModelWeaver</h1>
            <p style={{ color: '#94a3b8', marginBottom: '2rem', lineHeight: '1.6' }}>
              Bienvenue dans l'installateur officiel. Nous allons configurer votre environnement IA en quelques instants.
            </p>
            <button 
              onClick={() => nextStep('TOS')}
              style={{ 
                padding: '0.75rem 2rem', 
                backgroundColor: '#3b82f6', 
                color: 'white', 
                border: 'none', 
                borderRadius: '0.5rem', 
                cursor: 'pointer',
                fontSize: '1rem',
                fontWeight: '600'
              }}
            >
              Commencer
            </button>
          </div>
        )}

        {step === 'TOS' && (
          <div className="fade-in">
            <h2 style={{ fontSize: '1.25rem', marginBottom: '1rem' }}>Conditions d'utilisation</h2>
            <div style={{ 
              height: '200px', 
              overflowY: 'auto', 
              backgroundColor: '#0f172a', 
              padding: '1rem', 
              borderRadius: '0.5rem', 
              textAlign: 'left', 
              fontSize: '0.875rem', 
              color: '#cbd5e1',
              marginBottom: '2rem',
              border: '1px solid #334155'
            }}>
              <p>Le logiciel ModelWeaver est fourni "tel quel".</p>
              <p>L'installateur peut demander des privilèges administrateur (sudo) pour installer Python3 et SQLite3 sur votre système.</p>
              <p>L'utilisateur est responsable de la gestion de ses clés API.</p>
            </div>
            <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center' }}>
              <button 
                onClick={() => nextStep('WELCOME')}
                style={{ padding: '0.75rem 1.5rem', backgroundColor: '#475569', color: 'white', border: 'none', borderRadius: '0.5rem', cursor: 'pointer' }}
              >
                Retour
              </button>
              <button 
                onClick={handleBootstrap}
                style={{ padding: '0.75rem 1.5rem', backgroundColor: '#3b82f6', color: 'white', border: 'none', borderRadius: '0.5rem', cursor: 'pointer' }}
              >
                J'accepte et j'installe
              </button>
            </div>
          </div>
        )}

        {(step === 'DOWNLOADING' || (step === 'TOS' && loading)) && (
          <div className="fade-in">
            <div style={{ marginBottom: '2rem' }}>
              <div style={{ 
                width: '40px', 
                height: '40px', 
                border: '4px solid #3b82f6', 
                borderTop: '4px solid transparent', 
                borderRadius: '50%', 
                animation: 'spin 1s linear infinite',
                margin: '0 auto 1.5rem'
              }}></div>
              <p style={{ color: '#f8fafc', fontWeight: '500' }}>{statusMsg}</p>
              {error && <p style={{ color: '#ef4444', fontSize: '0.875rem', marginTop: '1rem' }}>{error}</p>}
            </div>
          </div>
        )}

        {step === 'FINALIZING' && (
          <div className="fade-in">
            <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>🚀</div>
            <h2 style={{ fontSize: '1.5rem', marginBottom: '1rem' }}>Prêt à décoller !</h2>
            <p style={{ color: '#94a3b8', marginBottom: '2rem' }}>L'environnement a été configuré avec succès.</p>
            <button 
              onClick={() => setStep('DASHBOARD')}
              style={{ padding: '0.75rem 2rem', backgroundColor: '#10b981', color: 'white', border: 'none', borderRadius: '0.5rem', cursor: 'pointer', fontWeight: 'bold' }}
            >
              Ouvrir ModelWeaver
            </button>
          </div>
        )}

        {step === 'DASHBOARD' && (
          <div className="fade-in">
            <h2 style={{ fontSize: '1.5rem', marginBottom: '1rem' }}>Tableau de Bord</h2>
            <div style={{ 
              padding: '2rem', 
              border: '2px dashed #3b82f6', 
              borderRadius: '1rem', 
              color: '#3b82f6',
              fontWeight: 'bold'
            }}>
              [ Dashboard Principal ]
            </div>
          </div>
        )}

      </div>
      <style>{`
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .fade-in { animation: fadeIn 0.3s ease-in; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
      `}</style>
    </div>
  );
}

export default App;
