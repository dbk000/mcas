import { useEffect } from 'react'

const SITE_NAME = 'MCAS'

const DEFAULT_META = {
  title: 'MCAS | Market Cognition At Scale',
  description: 'Operator-grade market cognition surfaces with Glass Box timing visibility, honest PF framing, and strategy-ranked signal views.',
  image: '/social/og-default.png',
  path: '/',
}

const PAGE_META = {
  default: DEFAULT_META,
  landing: {
    title: 'MCAS | Operator-Grade Signal Intelligence',
    description: 'Scan live-looking proof, Glass Box timing context, and honest performance framing before you unlock the paid operator view.',
    image: '/social/og-default.png',
    path: '/',
  },
  winners: {
    title: 'MCAS Winners | Curated Strategy Examples',
    description: 'Review curated historical winners by strategy, regime, and timing quality without exposing the full paid execution workflow.',
    image: '/social/og-winners.png',
    path: '/winners',
  },
  pricing: {
    title: 'MCAS Pricing | Free Vs Pro',
    description: 'Compare the free historical preview against the paid live strategy dashboard, Glass Box visibility, and honest PF metrics.',
    image: '/social/og-pricing.png',
    path: '/pricing',
  },
  upgrade: {
    title: 'Upgrade To MCAS Pro',
    description: 'Subscribe to MCAS Pro, then connect to your server to unlock live strategy-ranked surfaces.',
    image: '/social/og-pricing.png',
    path: '/upgrade',
  },
  checkoutSuccess: {
    title: 'MCAS Pro Activated',
    description: 'Payment confirmed. Connect to your MCAS server and open the strategy dashboard.',
    image: '/social/og-default.png',
    path: '/checkout/success',
  },
  checkoutCancel: {
    title: 'MCAS Checkout Canceled',
    description: 'Review the proof surfaces and pricing stack again before restarting the MCAS Pro upgrade flow.',
    image: '/social/og-pricing.png',
    path: '/checkout/cancel',
  },
  settings: {
    title: 'MCAS Settings | Connect Your Server',
    description: 'Configure the MCAS server URL and establish a session.',
    image: '/social/og-default.png',
    path: '/settings',
  },
}

function setTag(selector, value, attr = 'content') {
  const element = document.querySelector(selector)
  if (!element) return
  if (attr === 'text') {
    element.textContent = value
    return
  }
  element.setAttribute(attr, value)
}

function applyPageMeta(key = 'default', overrides = {}) {
  if (typeof document === 'undefined') {
    return DEFAULT_META
  }

  const resolvedOverrides = typeof key === 'object' ? key : overrides
  const resolvedKey = typeof key === 'string' ? key : 'default'

  const meta = {
    ...DEFAULT_META,
    ...(PAGE_META[resolvedKey] || {}),
    ...resolvedOverrides,
  }

  const absoluteUrl = new URL(meta.path || meta.url || '/', window.location.origin).toString()
  const absoluteImage = new URL(meta.image || DEFAULT_META.image, window.location.origin).toString()

  document.title = meta.title
  setTag('meta[name="description"]', meta.description)
  setTag('meta[property="og:title"]', meta.title)
  setTag('meta[property="og:description"]', meta.description)
  setTag('meta[property="og:image"]', absoluteImage)
  setTag('meta[property="og:url"]', absoluteUrl)
  setTag('meta[name="twitter:title"]', meta.title)
  setTag('meta[name="twitter:description"]', meta.description)
  setTag('meta[name="twitter:image"]', absoluteImage)
  setTag('meta[name="application-name"]', SITE_NAME)
  setTag('meta[name="apple-mobile-web-app-title"]', SITE_NAME)

  return meta
}

export function usePageMeta(key, overrides = {}) {
  useEffect(() => {
    applyPageMeta(key, overrides)
    return () => {
      applyPageMeta('default')
    }
  }, [key, overrides])
}
