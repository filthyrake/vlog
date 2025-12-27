/**
 * Workers API Endpoints
 */

import { apiClient } from '../client';
import type { Worker, ActiveJob, WorkerLogs, WorkerMetrics, DeploymentEvent } from '../types';

export const workersApi = {
  // ===========================================================================
  // Worker Management
  // ===========================================================================

  /**
   * List all workers
   */
  async list(): Promise<Worker[]> {
    const response = await apiClient.fetch<{ workers: Worker[] }>('/api/workers');
    return response.workers;
  },

  /**
   * Get active jobs across all workers
   */
  async getActiveJobs(): Promise<ActiveJob[]> {
    const response = await apiClient.fetch<{ jobs: ActiveJob[] }>('/api/workers/active-jobs');
    return response.jobs;
  },

  /**
   * Disable a worker
   */
  async disable(workerId: string): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/workers/${workerId}/disable`, {
      method: 'PUT',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Disable worker failed: ${response.status}`);
    }
  },

  /**
   * Enable a worker
   */
  async enable(workerId: string): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/workers/${workerId}/enable`, {
      method: 'PUT',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Enable worker failed: ${response.status}`);
    }
  },

  /**
   * Delete a worker
   */
  async delete(workerId: string): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/workers/${workerId}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Delete worker failed: ${response.status}`);
    }
  },

  // ===========================================================================
  // Admin Operations
  // ===========================================================================

  admin: {
    /**
     * Restart a worker
     */
    async restart(workerId: string): Promise<void> {
      const response = await apiClient.fetchResponse(`/api/admin/workers/${workerId}/restart`, {
        method: 'POST',
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Restart worker failed: ${response.status}`);
      }
    },

    /**
     * Trigger update on a worker
     */
    async update(workerId: string): Promise<void> {
      const response = await apiClient.fetchResponse(`/api/admin/workers/${workerId}/update`, {
        method: 'POST',
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Update worker failed: ${response.status}`);
      }
    },

    /**
     * Restart all workers
     */
    async restartAll(): Promise<{ restarted: number }> {
      const response = await apiClient.fetchResponse('/api/admin/workers/restart-all', {
        method: 'POST',
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Restart all workers failed: ${response.status}`);
      }

      return response.json();
    },

    /**
     * Get worker logs
     */
    async getLogs(workerId: string, lines: number = 200): Promise<WorkerLogs> {
      return apiClient.fetch<WorkerLogs>(`/api/admin/workers/${workerId}/logs?lines=${lines}`);
    },

    /**
     * Get worker metrics
     */
    async getMetrics(workerId: string): Promise<WorkerMetrics> {
      return apiClient.fetch<WorkerMetrics>(`/api/admin/workers/${workerId}/metrics`);
    },

    /**
     * Get deployment history
     */
    async getDeployments(limit: number = 50): Promise<DeploymentEvent[]> {
      return apiClient.fetch<DeploymentEvent[]>(`/api/admin/deployments?limit=${limit}`);
    },
  },
};
