/**
 * bf.conf template catalog API.
 */
import { apiClient } from './client';
import type { components } from '@/types/api-generated';

export type BfConfTemplate = components['schemas']['BfConfTemplateResponse'];
export type BfConfTemplateSummary = components['schemas']['BfConfTemplateSummary'];
export type BfConfTemplateListResponse = components['schemas']['BfConfTemplateListResponse'];
export type BfConfTemplateCreate = components['schemas']['BfConfTemplateCreate'];
export type BfConfTemplateUpdate = components['schemas']['BfConfTemplateUpdate'];

export const bfConfTemplatesApi = {
  list: () =>
    apiClient
      .get<BfConfTemplateListResponse>('/api/bf-conf-templates')
      .then((res) => res.data.templates),

  get: (id: number) =>
    apiClient
      .get<BfConfTemplate>(`/api/bf-conf-templates/${id}`)
      .then((res) => res.data),

  create: (data: BfConfTemplateCreate) =>
    apiClient
      .post<BfConfTemplate>('/api/bf-conf-templates', data)
      .then((res) => res.data),

  update: (id: number, data: BfConfTemplateUpdate) =>
    apiClient
      .patch<BfConfTemplate>(`/api/bf-conf-templates/${id}`, data)
      .then((res) => res.data),

  delete: (id: number) =>
    apiClient.delete(`/api/bf-conf-templates/${id}`).then((res) => res.data),
};
