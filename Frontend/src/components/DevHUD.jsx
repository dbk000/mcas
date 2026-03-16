import { useState, useEffect } from 'react';
import { getHardeningWarnings } from '../utils/hardening';

/**
 * DevHUD: Minimal operator-grade developer overlay.
 * Surfaces Knip/Bundle/Integrity warnings in local dev.
 */
const DevHUD = () => {
  const [warnings, setWarnings] = useState([]);
  const isDev = import.meta.env.DEV;

  useEffect(() => {
    if (!isDev) return;
    
    const checkWarnings = () => {
      setWarnings(getHardeningWarnings());
    };

    checkWarnings();
    const timer = setInterval(checkWarnings, 5000);
    return () => clearInterval(timer);
  }, [isDev]);

  if (!isDev || warnings.length === 0) return null;

  return (
    <div style={{
      position: 'fixed',
      bottom: '1rem',
      right: '1rem',
      zIndex: 9999,
      background: 'rgba(239, 68, 68, 0.9)',
      color: 'white',
      padding: '0.75rem',
      borderRadius: '0.5rem',
      fontSize: '0.75rem',
      fontFamily: 'monospace',
      boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)',
      border: '1px solid rgba(255, 255, 255, 0.2)'
    }}>
      <div style={{ fontWeight: 'bold', marginBottom: '0.25rem', borderBottom: '1px solid white' }}>
        DEV HARDENING HUD
      </div>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {warnings.map((w, i) => (
          <li key={i}>⚠️ {w}</li>
        ))}
      </ul>
    </div>
  );
};

export default DevHUD;
