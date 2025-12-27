/**
 * Workers Store
 * Manages worker list, active jobs, and worker operations
 */

import { workersApi } from '@/api/endpoints/workers';
import type { Worker, ActiveJob, WorkerStats, DeploymentEvent, WorkerMetrics } from '@/api/types';
import { formatTimeSince, formatDeploymentTime, isVersionOutdated, getEventIcon, getEventColor } from '@/utils/formatters';
import type { AlpineContext } from './types';

export interface WorkersState {
  // Worker list
  workersList: Worker[];
  workerStats: WorkerStats;
  activeJobs: ActiveJob[];
  loading: boolean;
  error: string | null;

  // Command pending state (for buttons)
  workerCommandPending: Record<string, boolean>;

  // Logs modal
  showLogsModal: boolean;
  logsWorkerName: string;
  logsContent: string;
  logsLoading: boolean;

  // Metrics modal
  showMetricsModal: boolean;
  metricsWorkerName: string;
  metricsData: Partial<WorkerMetrics>;
  metricsLoading: boolean;

  // Deployment history
  deploymentEvents: DeploymentEvent[];
  deploymentEventsLoading: boolean;
}

export interface WorkersActions {
  // Data loading
  loadWorkers(): Promise<void>;
  loadActiveJobs(): Promise<void>;
  loadDeploymentHistory(): Promise<void>;

  // Worker operations
  disableWorker(workerId: string): Promise<void>;
  enableWorker(workerId: string): Promise<void>;
  deleteWorker(workerId: string): Promise<void>;
  restartWorker(workerId: string): Promise<void>;
  updateWorker(workerId: string): Promise<void>;
  restartAllWorkers(): Promise<void>;

  // Modals
  viewWorkerLogs(worker: Worker): Promise<void>;
  closeLogsModal(): void;
  viewWorkerMetrics(worker: Worker): Promise<void>;
  closeMetricsModal(): void;

  // Formatters
  formatTimeSince: typeof formatTimeSince;
  formatDeploymentTime: typeof formatDeploymentTime;
  isVersionOutdated: typeof isVersionOutdated;
  getEventIcon: typeof getEventIcon;
  getEventColor: typeof getEventColor;

  // Stats computation
  computeWorkerStats(): void;
}

export type WorkersStore = WorkersState & WorkersActions;

export function createWorkersStore(_context?: AlpineContext): WorkersStore {
  return {
    // Initial state
    workersList: [],
    workerStats: { active: 0, idle: 0, offline: 0, disabled: 0, total: 0 },
    activeJobs: [],
    loading: false,
    error: null,
    workerCommandPending: {},

    // Logs modal
    showLogsModal: false,
    logsWorkerName: '',
    logsContent: '',
    logsLoading: false,

    // Metrics modal
    showMetricsModal: false,
    metricsWorkerName: '',
    metricsData: {},
    metricsLoading: false,

    // Deployment history
    deploymentEvents: [],
    deploymentEventsLoading: false,

    // Formatters
    formatTimeSince,
    formatDeploymentTime,
    isVersionOutdated,
    getEventIcon,
    getEventColor,

    // ===========================================================================
    // Data Loading
    // ===========================================================================

    async loadWorkers(): Promise<void> {
      this.loading = true;
      this.error = null;

      try {
        const [workers, jobs] = await Promise.all([
          workersApi.list(),
          workersApi.getActiveJobs(),
        ]);

        this.workersList = workers;
        this.activeJobs = jobs;
        this.computeWorkerStats();
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to load workers';
        this.workersList = [];
        this.activeJobs = [];
      } finally {
        this.loading = false;
      }
    },

    async loadActiveJobs(): Promise<void> {
      try {
        this.activeJobs = await workersApi.getActiveJobs();
      } catch (e) {
        console.error('Failed to load active jobs:', e);
      }
    },

    async loadDeploymentHistory(): Promise<void> {
      this.deploymentEventsLoading = true;

      try {
        this.deploymentEvents = await workersApi.admin.getDeployments(50);
      } catch (e) {
        console.error('Failed to load deployment history:', e);
        this.deploymentEvents = [];
      } finally {
        this.deploymentEventsLoading = false;
      }
    },

    // ===========================================================================
    // Worker Operations
    // ===========================================================================

    async disableWorker(workerId: string): Promise<void> {
      this.workerCommandPending[workerId] = true;

      try {
        await workersApi.disable(workerId);
        const worker = this.workersList.find((w) => w.worker_id === workerId);
        if (worker) {
          worker.status = 'disabled';
        }
        this.computeWorkerStats();
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to disable worker';
      } finally {
        this.workerCommandPending[workerId] = false;
      }
    },

    async enableWorker(workerId: string): Promise<void> {
      this.workerCommandPending[workerId] = true;

      try {
        await workersApi.enable(workerId);
        const worker = this.workersList.find((w) => w.worker_id === workerId);
        if (worker) {
          worker.status = 'idle';
        }
        this.computeWorkerStats();
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to enable worker';
      } finally {
        this.workerCommandPending[workerId] = false;
      }
    },

    async deleteWorker(workerId: string): Promise<void> {
      this.workerCommandPending[workerId] = true;

      try {
        await workersApi.delete(workerId);
        this.workersList = this.workersList.filter((w) => w.worker_id !== workerId);
        this.computeWorkerStats();
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to delete worker';
      } finally {
        this.workerCommandPending[workerId] = false;
      }
    },

    async restartWorker(workerId: string): Promise<void> {
      this.workerCommandPending[workerId] = true;

      try {
        await workersApi.admin.restart(workerId);
        // Worker will come back after restart
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to restart worker';
      } finally {
        this.workerCommandPending[workerId] = false;
      }
    },

    async updateWorker(workerId: string): Promise<void> {
      this.workerCommandPending[workerId] = true;

      try {
        await workersApi.admin.update(workerId);
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to trigger worker update';
      } finally {
        this.workerCommandPending[workerId] = false;
      }
    },

    async restartAllWorkers(): Promise<void> {
      this.loading = true;

      try {
        await workersApi.admin.restartAll();
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to restart all workers';
      } finally {
        this.loading = false;
      }
    },

    // ===========================================================================
    // Modals
    // ===========================================================================

    async viewWorkerLogs(worker: Worker): Promise<void> {
      this.logsWorkerName = worker.worker_name || worker.worker_id;
      this.logsContent = '';
      this.logsLoading = true;
      this.showLogsModal = true;

      try {
        const result = await workersApi.admin.getLogs(worker.worker_id, 200);
        this.logsContent = result.logs;
      } catch (e) {
        this.logsContent = `Failed to load logs: ${e instanceof Error ? e.message : String(e)}`;
      } finally {
        this.logsLoading = false;
      }
    },

    closeLogsModal(): void {
      this.showLogsModal = false;
      this.logsContent = '';
    },

    async viewWorkerMetrics(worker: Worker): Promise<void> {
      this.metricsWorkerName = worker.worker_name || worker.worker_id;
      this.metricsData = {};
      this.metricsLoading = true;
      this.showMetricsModal = true;

      try {
        const metrics = await workersApi.admin.getMetrics(worker.worker_id);
        this.metricsData = metrics;
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to load metrics';
      } finally {
        this.metricsLoading = false;
      }
    },

    closeMetricsModal(): void {
      this.showMetricsModal = false;
      this.metricsData = {};
    },

    // ===========================================================================
    // Stats
    // ===========================================================================

    computeWorkerStats(): void {
      const stats = { active: 0, idle: 0, offline: 0, disabled: 0, total: 0 };

      for (const worker of this.workersList) {
        stats.total++;
        switch (worker.status) {
          case 'active':
            stats.active++;
            break;
          case 'idle':
            stats.idle++;
            break;
          case 'offline':
            stats.offline++;
            break;
          case 'disabled':
            stats.disabled++;
            break;
        }
      }

      this.workerStats = stats;
    },
  };
}
