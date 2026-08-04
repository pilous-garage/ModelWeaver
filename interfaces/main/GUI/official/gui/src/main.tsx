import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.tsx';
import './index.css';

// Expose React/ReactDOM en global — les panels EXTERNES (compilés isolément
// par panel-creator) importent React via l'URL du daemon qui ré-exporte
// window.React (React partagé, pas de duplication).
(window as any).React = React;
(window as any).ReactDOM = ReactDOM;

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
