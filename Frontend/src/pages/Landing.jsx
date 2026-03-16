import { usePageMeta } from '../utils/pageMeta'
import LayoutMagazine from './landing-layouts/layout-08-magazine'

export default function Landing() {
  usePageMeta('landing')
  return <LayoutMagazine />
}
