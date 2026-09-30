import type {
  ActivityEventList,
  BackgroundJob,
  ActivityStats,
  AnalyticsResponse,
  Automation,
  AutomationList,
  AutomationPayload,
  AutomationRunResult,
  AutomationUpdatePayload,
  ClearResponse,
  DiscoverResponse,
  DiscoveredWorkflowsResponse,
  ExecutionDetail,
  ExecutionList,
  ExecutionStepList,
  GmailDiagnosis,
  GmailMessageList,
  HealthResponse,
  IntegrationEvent,
  IntegrationList,
  IntegrationStatus,
  JobList,
  Schedule,
  ScheduleFrequency,
  SchedulerStatus,
  WorkerStatus,
  RejectWorkflowRequest,
  SystemStatus,
  WorkflowDraft,
  WorkflowDraftList,
  WorkflowUnderstanding,
  WorkflowUnderstandingList,
} from "@/types/api";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly detail?: string;

  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch (error) {
    throw new ApiError(
      0,
      error instanceof Error ? error.message : "Network request failed",
    );
  }

  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body = (await response.json()) as {
        error?: { detail?: string };
      };
      detail = body?.error?.detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(
      response.status,
      `Request failed: ${response.status}`,
      detail,
    );
  }

  return (await response.json()) as T;
}

function queryString(params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function listActivity(params: {
  limit?: number;
  application?: string;
  category?: string;
  session_id?: string;
} = {}): Promise<ActivityEventList> {
  return request<ActivityEventList>(`/api/activity${queryString(params)}`);
}

export function getActivityStats(): Promise<ActivityStats> {
  return request<ActivityStats>("/api/activity/stats");
}


export function clearActivity(): Promise<ClearResponse> {
  return request<ClearResponse>("/api/activity", { method: "DELETE" });
}

export function getDiscoveredWorkflows(): Promise<DiscoveredWorkflowsResponse> {
  return request<DiscoveredWorkflowsResponse>("/api/workflows/discovered");
}

export function discoverWorkflows(): Promise<DiscoverResponse> {
  return request<DiscoverResponse>("/api/workflows/discover", {
    method: "POST",
  });
}

export function getWorkflowUnderstandings(): Promise<WorkflowUnderstandingList> {
  return request<WorkflowUnderstandingList>("/api/ai/understandings");
}

export function getWorkflowUnderstanding(
  id: string,
): Promise<WorkflowUnderstanding> {
  return request<WorkflowUnderstanding>(
    `/api/ai/understandings/${encodeURIComponent(id)}`,
  );
}

export function understandWorkflow(
  workflowCandidateId: string,
): Promise<WorkflowUnderstanding> {
  return request<WorkflowUnderstanding>(
    `/api/ai/understand/${encodeURIComponent(workflowCandidateId)}`,
    { method: "POST" },
  );
}

export function getWorkflowDrafts(): Promise<WorkflowDraftList> {
  return request<WorkflowDraftList>("/api/workflows/drafts");
}

export function getWorkflowDraft(id: string): Promise<WorkflowDraft> {
  return request<WorkflowDraft>(
    `/api/workflows/drafts/${encodeURIComponent(id)}`,
  );
}

export function generateWorkflowDraft(
  workflowCandidateId: string,
): Promise<WorkflowDraft> {
  return request<WorkflowDraft>(
    `/api/workflows/drafts/${encodeURIComponent(workflowCandidateId)}/generate`,
    { method: "POST" },
  );
}

export function approveWorkflowDraft(id: string): Promise<WorkflowDraft> {
  return request<WorkflowDraft>(
    `/api/workflows/drafts/${encodeURIComponent(id)}/approve`,
    { method: "POST" },
  );
}

export function rejectWorkflowDraft(
  id: string,
  payload: RejectWorkflowRequest = {},
): Promise<WorkflowDraft> {
  return request<WorkflowDraft>(
    `/api/workflows/drafts/${encodeURIComponent(id)}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function deleteWorkflowDraft(
  id: string,
): Promise<{ deleted: number; id: string }> {
  return request<{ deleted: number; id: string }>(
    `/api/workflows/drafts/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

export function executeWorkflowDraft(
  id: string,
  inputs: Record<string, unknown> = {},
): Promise<ExecutionDetail> {
  return request<ExecutionDetail>(
    `/api/workflows/drafts/${encodeURIComponent(id)}/execute`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ inputs }),
    },
  );
}

export function getExecutions(
  params: { draft_id?: string; status?: string } = {},
): Promise<ExecutionList> {
  return request<ExecutionList>(`/api/workflows/executions${queryString(params)}`);
}

export function getExecution(id: string): Promise<ExecutionDetail> {
  return request<ExecutionDetail>(
    `/api/workflows/executions/${encodeURIComponent(id)}`,
  );
}

export function getExecutionSteps(id: string): Promise<ExecutionStepList> {
  return request<ExecutionStepList>(
    `/api/workflows/executions/${encodeURIComponent(id)}/steps`,
  );
}

export function getAutomations(params: {
  enabled?: boolean;
} = {}): Promise<AutomationList> {
  const query =
    params.enabled === undefined ? "" : `?enabled=${String(params.enabled)}`;
  return request<AutomationList>(`/api/automations${query}`);
}

export function getAutomation(id: string): Promise<Automation> {
  return request<Automation>(`/api/automations/${encodeURIComponent(id)}`);
}

export function createAutomation(
  payload: AutomationPayload,
): Promise<Automation> {
  return request<Automation>("/api/automations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function updateAutomation(
  id: string,
  payload: AutomationUpdatePayload,
): Promise<Automation> {
  return request<Automation>(`/api/automations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function enableAutomation(id: string): Promise<Automation> {
  return request<Automation>(
    `/api/automations/${encodeURIComponent(id)}/enable`,
    { method: "POST" },
  );
}

export function disableAutomation(id: string): Promise<Automation> {
  return request<Automation>(
    `/api/automations/${encodeURIComponent(id)}/disable`,
    { method: "POST" },
  );
}

export function runAutomation(id: string): Promise<AutomationRunResult> {
  return request<AutomationRunResult>(
    `/api/automations/${encodeURIComponent(id)}/run`,
    { method: "POST" },
  );
}

export function deleteAutomation(
  id: string,
): Promise<{ deleted: number; id: string }> {
  return request<{ deleted: number; id: string }>(
    `/api/automations/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

export function getAnalytics(days?: number): Promise<AnalyticsResponse> {
  return request<AnalyticsResponse>(
    `/api/analytics${queryString({ days })}`,
  );
}

export function getSystemStatus(): Promise<SystemStatus> {
  return request<SystemStatus>("/api/system/status");
}

// ---------------------------------------------------------------- Phase 8

export function getIntegrations(): Promise<IntegrationList> {
  return request<IntegrationList>("/api/integrations");
}

export function getIntegration(provider: string): Promise<IntegrationStatus> {
  return request<IntegrationStatus>(
    `/api/integrations/${encodeURIComponent(provider)}`,
  );
}

export function connectIntegration(
  provider: string,
  payload: { code?: string; label?: string } = {},
): Promise<IntegrationStatus & { authorize_url?: string }> {
  return request<IntegrationStatus & { authorize_url?: string }>(
    `/api/integrations/${encodeURIComponent(provider)}/connect`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function disconnectIntegration(
  provider: string,
): Promise<{ provider: string; disconnected: boolean }> {
  return request<{ provider: string; disconnected: boolean }>(
    `/api/integrations/${encodeURIComponent(provider)}/disconnect`,
    { method: "POST" },
  );
}

export function testIntegration(
  provider: string,
): Promise<{ ok: boolean; message: string; provider: string }> {
  return request<{ ok: boolean; message: string; provider: string }>(
    `/api/integrations/${encodeURIComponent(provider)}/test`,
    { method: "POST" },
  );
}

export function getIntegrationEvents(
  provider: string,
  limit = 20,
): Promise<{ provider: string; events: IntegrationEvent[]; count: number }> {
  return request<{ provider: string; events: IntegrationEvent[]; count: number }>(
    `/api/integrations/${encodeURIComponent(provider)}/events${queryString({
      limit,
    })}`,
  );
}

export function getSchedulerStatus(): Promise<SchedulerStatus> {
  return request<SchedulerStatus>("/api/scheduler/status");
}

export function getWorkerStatus(): Promise<WorkerStatus> {
  return request<WorkerStatus>("/api/worker/status");
}

export function drainWorker(
  limit = 25,
): Promise<{ processed: number; status: WorkerStatus }> {
  return request<{ processed: number; status: WorkerStatus }>(
    `/api/worker/drain${queryString({ limit })}`,
  );
}

export function runSchedulerTick(): Promise<{ fired: Record<string, number> }> {
  return request<{ fired: Record<string, number> }>("/api/scheduler/tick", {
    method: "POST",
  });
}

export function getSchedules(): Promise<{
  schedules: Schedule[];
  count: number;
}> {
  return request<{ schedules: Schedule[]; count: number }>("/api/schedules");
}

export function putSchedule(
  automationId: string,
  payload: {
    frequency: ScheduleFrequency;
    timezone?: string;
    run_at?: string | null;
    interval_seconds?: number | null;
    time_of_day?: string | null;
    days_of_week?: number[] | null;
  },
): Promise<Schedule> {
  return request<Schedule>(
    `/api/schedules/${encodeURIComponent(automationId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function getBackgroundJobs(
  params: { automation_id?: string; status?: string; limit?: number } = {},
): Promise<JobList> {
  return request<JobList>(`/api/background-jobs${queryString(params)}`);
}

export function getBackgroundJob(id: string): Promise<BackgroundJob> {
  return request<BackgroundJob>(`/api/background-jobs/${encodeURIComponent(id)}`);
}

export function runAutomationDemo(
  automationId: string,
): Promise<AutomationRunResult> {
  return request<AutomationRunResult>(
    `/api/automations/${encodeURIComponent(automationId)}/demo`,
    { method: "POST" },
  );
}

export function getIntegrationTest(provider: string): Promise<GmailDiagnosis> {
  return request<GmailDiagnosis>(
    `/api/integrations/${encodeURIComponent(provider)}/test`,
  );
}


/**
 * Recent messages from the authenticated account's real mailbox, so the user
 * can choose which email a workflow run should process. No token is returned.
 */
export function listGmailMessages(
  limit = 10,
): Promise<GmailMessageList> {
  return request<GmailMessageList>(
    `/api/integrations/gmail/messages?limit=${limit}`,
  );
}
