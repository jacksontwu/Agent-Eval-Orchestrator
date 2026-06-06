export interface Worker {
  workerId: string;
  displayName: string;
  host: string;
  slotsTotal: number;
  slotsUsed: number;
  capabilities: Record<string, unknown>;
  status: string;
  enabled: boolean;
  note: string;
  tags: string[];
  allocationWeight: number;
  lastHeartbeatAt: string | null;
}

export interface DashboardTask {
  runId: string;
  displayName: string;
  owner: string;
  status: string;
  templateId: string;
  latestBatchId: string | null;
  counts: Record<string, number>;
  updatedAt: string;
}

export interface Batch {
  batchId: string;
  runId: string;
  status: string;
  assignedWorkerId: string | null;
  selectedCaseIds: string[];
  summary: Record<string, unknown>;
  createdAt: string;
}

export interface CaseRun {
  caseRunId: string;
  batchId: string;
  caseId: string;
  status: string;
  score: number | null;
  errorText: string | null;
}

export interface DatasetInfo {
  datasetRef: string;
  available: boolean;
  path: string;
}

export interface RunDetail {
  runId: string;
  displayName: string;
  owner: string;
  syncStatus: string;
  rerunStatus: string;
  batches: Batch[];
}
