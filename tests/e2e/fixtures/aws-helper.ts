/**
 * AWS Helper - Verify infrastructure resources
 *
 * Uses AWS SDK to check if resources were actually created/destroyed.
 */

import { EC2Client, DescribeVpcsCommand, DescribeSecurityGroupsCommand } from '@aws-sdk/client-ec2';
import { EKSClient, DescribeClusterCommand } from '@aws-sdk/client-eks';
import { TEST_CONFIG } from '../config/test-config';

/**
 * AWS Helper class for resource verification
 */
export class AwsHelper {
  private ec2Client: EC2Client;
  private eksClient: EKSClient;
  private region: string;

  constructor() {
    this.region = TEST_CONFIG.aws.region;

    // Initialize AWS clients with credentials from environment
    const clientConfig = {
      region: this.region,
      credentials: {
        accessKeyId: process.env.AWS_ACCESS_KEY_ID!,
        secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY!,
        sessionToken: process.env.AWS_SESSION_TOKEN,
      },
    };

    this.ec2Client = new EC2Client(clientConfig);
    this.eksClient = new EKSClient(clientConfig);
  }

  /**
   * Check if VPC exists with given CIDR block
   */
  async vpcExists(vpcCidr: string, tags?: Record<string, string>): Promise<{ exists: boolean; vpcId?: string }> {
    try {
      const command = new DescribeVpcsCommand({
        Filters: [
          {
            Name: 'cidr',
            Values: [vpcCidr],
          },
        ],
      });

      const response = await this.ec2Client.send(command);

      if (!response.Vpcs || response.Vpcs.length === 0) {
        return { exists: false };
      }

      // If tags provided, verify they match
      if (tags) {
        const vpc = response.Vpcs.find((v) => {
          const vpcTags = v.Tags || [];
          return Object.entries(tags).every(([key, value]) =>
            vpcTags.some((t) => t.Key === key && t.Value === value)
          );
        });

        if (vpc) {
          return { exists: true, vpcId: vpc.VpcId };
        }
        return { exists: false };
      }

      return { exists: true, vpcId: response.Vpcs[0].VpcId };
    } catch (error) {
      console.error('Error checking VPC:', error);
      return { exists: false };
    }
  }

  /**
   * Check if security group exists in VPC
   */
  async securityGroupExists(vpcId: string, groupName?: string): Promise<{ exists: boolean; securityGroupId?: string }> {
    try {
      const filters: any[] = [
        {
          Name: 'vpc-id',
          Values: [vpcId],
        },
      ];

      if (groupName) {
        filters.push({
          Name: 'group-name',
          Values: [groupName],
        });
      }

      const command = new DescribeSecurityGroupsCommand({
        Filters: filters,
      });

      const response = await this.ec2Client.send(command);

      if (!response.SecurityGroups || response.SecurityGroups.length === 0) {
        return { exists: false };
      }

      return {
        exists: true,
        securityGroupId: response.SecurityGroups[0].GroupId
      };
    } catch (error) {
      console.error('Error checking Security Group:', error);
      return { exists: false };
    }
  }

  /**
   * Check if EKS cluster exists
   */
  async eksClusterExists(clusterName: string): Promise<{ exists: boolean; status?: string }> {
    try {
      const command = new DescribeClusterCommand({
        name: clusterName,
      });

      const response = await this.eksClient.send(command);

      if (!response.cluster) {
        return { exists: false };
      }

      return {
        exists: true,
        status: response.cluster.status
      };
    } catch (error: any) {
      // ResourceNotFoundException means cluster doesn't exist
      if (error.name === 'ResourceNotFoundException') {
        return { exists: false };
      }

      console.error('Error checking EKS cluster:', error);
      throw error;
    }
  }

  /**
   * Verify VPC was destroyed (doesn't exist)
   */
  async verifyVpcDestroyed(vpcCidr: string): Promise<boolean> {
    const result = await this.vpcExists(vpcCidr);
    return !result.exists;
  }

  /**
   * Verify EKS cluster was destroyed (doesn't exist)
   */
  async verifyEksClusterDestroyed(clusterName: string): Promise<boolean> {
    const result = await this.eksClusterExists(clusterName);
    return !result.exists;
  }

  /**
   * Wait for EKS cluster to reach ACTIVE status
   * Used after apply to ensure cluster is fully deployed
   */
  async waitForEksClusterActive(clusterName: string, timeoutMs: number = 1200000): Promise<boolean> {
    const startTime = Date.now();
    const pollInterval = 30000; // Check every 30s

    while (Date.now() - startTime < timeoutMs) {
      const result = await this.eksClusterExists(clusterName);

      if (result.exists && result.status === 'ACTIVE') {
        return true;
      }

      if (result.exists && result.status === 'FAILED') {
        throw new Error(`EKS cluster ${clusterName} entered FAILED state`);
      }

      // Wait before next check
      await new Promise((resolve) => setTimeout(resolve, pollInterval));
    }

    throw new Error(`Timeout waiting for EKS cluster ${clusterName} to become ACTIVE`);
  }

  /**
   * Get VPC details including subnets and route tables
   */
  async getVpcDetails(vpcId: string): Promise<any> {
    try {
      const command = new DescribeVpcsCommand({
        VpcIds: [vpcId],
      });

      const response = await this.ec2Client.send(command);
      return response.Vpcs?.[0] || null;
    } catch (error) {
      console.error('Error getting VPC details:', error);
      return null;
    }
  }
}

/**
 * Create AWS helper instance
 */
export function createAwsHelper(): AwsHelper {
  return new AwsHelper();
}
