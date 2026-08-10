/**
 * Category color mappings for modules.
 *
 * D-020 — all class strings returned here are token-only so consumers compose
 * them with shadcn surfaces without re-introducing raw palette accents.
 */
export const categoryColors: Record<string, string> = {
  // Cloud Providers
  aws: 'bg-warning',
  azure: 'bg-primary',
  gcp: 'bg-success',

  // F5 / BIG-IP
  bnk: 'bg-destructive', // F5 BIG-IP Next for Kubernetes
  f5: 'bg-destructive',
  'big-ip': 'bg-destructive',

  // Kubernetes
  kubernetes: 'bg-primary',
  k8s: 'bg-primary',

  // Infrastructure Categories
  infra: 'bg-muted-foreground',
  infrastructure: 'bg-muted-foreground',
  network: 'bg-info',
  networking: 'bg-info',
  security: 'bg-primary',
  database: 'bg-warning',
  storage: 'bg-primary',
  compute: 'bg-muted-foreground',
  monitoring: 'bg-success',
  logging: 'bg-warning',

  // Common module types
  vpc: 'bg-warning', // AWS VPC
  eks: 'bg-warning', // AWS EKS
  rds: 'bg-warning', // AWS RDS
  s3: 'bg-primary', // AWS S3
};

export function getCategoryColor(category?: string): string {
  if (!category) return 'bg-muted-foreground';
  const normalized = category.toLowerCase();
  return categoryColors[normalized] || 'bg-primary';
}

export function getProviderColor(provider?: string): string {
  if (!provider) return '';
  const normalized = provider.toLowerCase();
  return categoryColors[normalized] || '';
}
