/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'terminal': {
          900: 'rgb(var(--c-t-900) / <alpha-value>)',
          800: 'rgb(var(--c-t-800) / <alpha-value>)',
          700: 'rgb(var(--c-t-700) / <alpha-value>)',
          600: 'rgb(var(--c-t-600) / <alpha-value>)',
          500: 'rgb(var(--c-t-500) / <alpha-value>)',
          400: 'rgb(var(--c-t-400) / <alpha-value>)',
          300: 'rgb(var(--c-t-300) / <alpha-value>)',
          200: 'rgb(var(--c-t-200) / <alpha-value>)',
          100: 'rgb(var(--c-t-100) / <alpha-value>)',
        },
        'amber': {
          DEFAULT: 'rgb(var(--c-accent) / <alpha-value>)',
          50:  '#fffbeb',
          100: '#fef3c7',
          200: '#fde68a',
          300: 'rgb(var(--c-accent-light) / <alpha-value>)',
          400: 'rgb(var(--c-accent) / <alpha-value>)',
          500: 'rgb(var(--c-accent) / <alpha-value>)',
          600: 'rgb(var(--c-accent-strong) / <alpha-value>)',
          700: '#b45309',
          800: '#92400e',
          900: '#78350f',
        },
        'signal': {
          long: 'rgb(var(--c-signal-long) / <alpha-value>)',
          short: 'rgb(var(--c-signal-short) / <alpha-value>)',
          neutral: 'rgb(var(--c-signal-neutral) / <alpha-value>)',
        }
      },
      fontFamily: {
        'mono': ['JetBrains Mono', 'Fira Code', 'SF Mono', 'monospace'],
        'sans': ['Inter', 'system-ui', 'sans-serif'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.3s ease-out',
        'slide-up': 'slideUp 0.3s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      }
    },
  },
  plugins: [],
}
