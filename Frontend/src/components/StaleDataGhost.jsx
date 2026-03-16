import { useEffect, useState } from 'react';

/**
 * StaleDataGhost: Implements "Informed Doubt" patterns.
 * Dim or ghost data that has exceeded its freshness threshold.
 */
export const StaleDataGhost = ({ 
  children, 
  lastUpdateTs, 
  staleThresholdMs = 30000, // 30s default
  showTimer = false
}) => {
  const [elapsed, setElapsed] = useState(0);
  const [isStale, setIsStale] = useState(false);

  useEffect(() => {
    if (!lastUpdateTs) return;

    const interval = setInterval(() => {
      const now = Date.now();
      const delta = now - lastUpdateTs;
      setElapsed(delta);
      setIsStale(delta > staleThresholdMs);
    }, 1000);

    return () => clearInterval(interval);
  }, [lastUpdateTs, staleThresholdMs]);

  const opacityClass = isStale ? 'opacity-40 grayscale transition-all duration-500' : 'opacity-100 transition-all';
  
  return (
    <div className="group relative">
      <div className={opacityClass}>
        {children}
      </div>
      
      {isStale && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <span className="rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-widest text-white">
            Stale ({Math.floor(elapsed / 1000)}s)
          </span>
        </div>
      )}
      
      {showTimer && !isStale && (
        <div className="absolute right-0 top-0 p-1 text-[8px] text-gray-500 opacity-0 group-hover:opacity-100">
          Last: {Math.floor(elapsed / 1000)}s ago
        </div>
      )}
    </div>
  );
};
