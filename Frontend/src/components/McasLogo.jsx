/**
 * MCAS Governance Shield Logo
 * Shield outline containing a candlestick-chart "M" formed by
 * 5 candlesticks (green-red-green-red-green creating an M shape).
 * No text — just the shield with the candlestick M.
 */
export default function McasLogo({ className = 'size-8', style }) {
  return (
    <svg
      viewBox="0 0 64 72"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={style}
      role="img"
      aria-label="MCAS"
    >
      {/* Shield outline */}
      <path
        d="M32 2L6 14v22c0 16.6 11 31.2 26 34 15-2.8 26-17.4 26-34V14L32 2z"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinejoin="round"
        fill="none"
      />

      {/* 5 candlesticks forming an M shape
          Pattern: green-red-green-red-green
          M shape: high-low-high-low-high for body positions
          Wick extends above and below each body */}

      {/* Candle 1 — GREEN (left leg of M, tall) */}
      <line x1="16" y1="20" x2="16" y2="52" stroke="#1D9E75" strokeWidth="1.5" />
      <rect x="13" y="24" width="6" height="18" rx="1" fill="#1D9E75" />

      {/* Candle 2 — RED (first valley of M) */}
      <line x1="24" y1="26" x2="24" y2="56" stroke="#E24B4A" strokeWidth="1.5" />
      <rect x="21" y="34" width="6" height="16" rx="1" fill="#E24B4A" />

      {/* Candle 3 — GREEN (center peak of M) */}
      <line x1="32" y1="18" x2="32" y2="50" stroke="#1D9E75" strokeWidth="1.5" />
      <rect x="29" y="22" width="6" height="18" rx="1" fill="#1D9E75" />

      {/* Candle 4 — RED (second valley of M) */}
      <line x1="40" y1="26" x2="40" y2="56" stroke="#E24B4A" strokeWidth="1.5" />
      <rect x="37" y="34" width="6" height="16" rx="1" fill="#E24B4A" />

      {/* Candle 5 — GREEN (right leg of M, tall) */}
      <line x1="48" y1="20" x2="48" y2="52" stroke="#1D9E75" strokeWidth="1.5" />
      <rect x="45" y="24" width="6" height="18" rx="1" fill="#1D9E75" />
    </svg>
  )
}
