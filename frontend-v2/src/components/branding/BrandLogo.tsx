import type { SVGProps } from 'react'
import { getBrand } from '@/lib/brand'
import { F5Logo } from './F5Logo'
import { ForgeLogo } from './ForgeLogo'

export function BrandLogo(props: SVGProps<SVGSVGElement>) {
  return getBrand() === 'f5' ? <F5Logo {...props} /> : <ForgeLogo {...props} />
}
