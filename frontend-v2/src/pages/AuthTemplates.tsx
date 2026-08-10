/**
 * Access Methods page — D-020 redesign.
 *
 * Bold heading, two SectionCard-shaped panels (SSH connections, credential
 * templates) on a single calm surface, plus a quiet info callout explaining
 * how templates apply to projects.
 */

import ContainerRegistries from '@/components/settings/ContainerRegistries';
import CredentialTemplates from '@/components/settings/CredentialTemplates';
import SSHCredentials from '@/components/settings/SSHCredentials';
import { Info } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { usePageRefresh } from '@/hooks/usePageRefresh';

export default function AuthTemplates() {
  const { refresh, isRefreshing } = usePageRefresh();
  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto" data-onboarding="auth-templates-page">
      {/* Header */}
      <PageHeader
        title="Access Methods"
        subtitle="Cloud-provider credentials, SSH jumphost configurations, and single-node SSH access used across projects and deployments."
        onRefresh={refresh}
        isRefreshing={isRefreshing}
      />

      <SSHCredentials />
      <ContainerRegistries />
      <CredentialTemplates />

      {/* How-to callout — token-pure info accent on left border, not whole-card tint */}
      <div className="rounded-xl border border-border bg-card border-l-2 border-l-info p-6">
        <div className="flex items-center gap-2 mb-2">
          <Info className="h-4 w-4 text-info" />
          <h2 className="text-sm font-semibold text-foreground">Using credential templates</h2>
        </div>
        <p className="text-sm text-muted-foreground mb-3">
          Create credential templates above, then assign them to projects:
        </p>
        <ol className="text-sm list-decimal list-inside space-y-1.5 ml-1 text-foreground/80">
          <li>Go to the <strong className="text-foreground">Projects</strong> page</li>
          <li>Click on a project or create a new one</li>
          <li>Click the <strong className="text-foreground">Credentials</strong> button on the project detail page</li>
          <li>Select a credential template from the dropdown</li>
          <li>Save to apply the template to that project</li>
        </ol>
        <p className="text-sm mt-3 text-muted-foreground">
          <strong className="text-foreground">Tip:</strong> mark a template as default to automatically assign it to new projects.
        </p>
      </div>
    </div>
  );
}
