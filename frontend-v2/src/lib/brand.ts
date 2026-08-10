declare global {
  interface Window {
    __BRAND__?: string
  }
}

export function getBrand(): 'f5' | 'forge' {
  return window.__BRAND__ === 'f5' ? 'f5' : 'forge'
}
