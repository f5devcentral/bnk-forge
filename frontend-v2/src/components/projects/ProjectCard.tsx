import { memo } from 'react';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import type { Project } from '@/types';
import { Calendar, FolderGit2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { ProjectStatsDisplay } from '@/components/shared/ProjectStatsDisplay';
import { getProjectLocationInfo } from '@/lib/aws-regions';
import { BackendBadge } from './BackendBadge';

interface ProjectCardProps {
  project: Project;
}

export const ProjectCard = memo(function ProjectCard({ project }: ProjectCardProps) {
  const locationInfo = getProjectLocationInfo(project.cloud_provider, project.region, project.credential_template?.provider, project.project_type);

  return (
    <Card
      data-testid="project-card"
      className={`relative transition-all hover:shadow-md ${project.has_deployments ? 'border-l-4 border-l-success' : ''}`}
    >
      <Link
        to={`/projects/${project.id}`}
        className="absolute inset-0 z-0 focus:ring-2 focus:ring-offset-2 rounded-xl focus:outline-none focus:ring-ring"
      >
        <span className="sr-only">View project {project.name}</span>
      </Link>
      <CardHeader>
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div
              className="w-12 h-12 rounded-lg flex items-center justify-center"
              style={{ backgroundColor: project.color || '#2563eb' }}
            >
              <FolderGit2 className="h-6 w-6 text-white" />
            </div>
            <div>
              <h3 className="text-xl font-bold">{project.name}</h3>
              <div className="flex items-center gap-2 mt-1 flex-wrap relative z-10">
                {project.has_deployments && (
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Badge tabIndex={0} variant="success" className="text-xs cursor-help">Deployed</Badge>
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>Project has deployed modules and infrastructure</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                )}
                {locationInfo && (
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Badge tabIndex={0} variant="outline" className="text-xs cursor-help font-normal whitespace-nowrap">
                          {locationInfo.flag} {locationInfo.display}
                        </Badge>
                      </TooltipTrigger>
                      <TooltipContent>
                        <p>{locationInfo.label}</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                )}
                <BackendBadge
                  backendType={project.backend_type}
                  stateStorageProvider={project.state_storage_provider}
                  s3Bucket={project.s3_state_bucket}
                  s3Region={project.s3_state_region}
                  useNativeLocking={project.s3_use_native_locking}
                  s3Endpoint={project.s3_endpoint}
                />
              </div>
              {project.description && (
                <p className="text-sm text-muted-foreground mt-1">{project.description}</p>
              )}
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {/* Project Info */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            {project.created_at && (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Calendar className="h-4 w-4" />
                <span className="text-xs">
                  Created {new Date(project.created_at).toLocaleDateString()}
                </span>
              </div>
            )}
          </div>

          {/* Stats */}
          <ProjectStatsDisplay project={project} variant="compact" />
        </div>
      </CardContent>
    </Card>
  );
});
