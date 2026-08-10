import { api } from '@/lib/api';
import type { CloudRegionOption } from '@/types';

/**
 * IBM IAM/token endpoints do not support browser CORS from this UI origin.
 * Region discovery must therefore use the same-origin backend-assisted API.
 */
export async function loadIbmRegionsFromApiKey(apiKey: string): Promise<CloudRegionOption[]> {
  const response = await api.queryCloudRegions({ provider: 'ibm', ibmcloud_api_key: apiKey });
  return response.regions;
}
