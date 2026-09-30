export interface HealthResponse {
  status: string;
  service: string;
}

export type ConnectionState = "checking" | "connected" | "disconnected";

export type ActivityCategory =
  | "application"
  | "browser"
  | "file"
  | "communication"
  | "crm"
  | "ui"
  | "system";

export interface ActivityEvent {
  id: string;
  timestamp: string;
  application: string;
  category: ActivityCategory;
  action: string;
  description: string;
  metadata: Record<string, unknown>;
  session_id: string;
}

export interface ActivityEventList {
  events: ActivityEvent[];
  count: number;
  total: number;
}

export interface ActivityStats {
  total_events: number;
  total_today: number;
  applications: string[];
  application_count: number;
  last_activity: string | null;
  current_session: string | null;
  session_event_count: number;
}

export interface SimulateRequest {
  workflow?: string;
  repetitions?: number;
}

export interface SimulateResponse {
  workflow: string;
  repetitions: number;
  generated: number;
  session_ids: string[];
  events: ActivityEvent[];
}

export interface ClearResponse {
  cleared: number;
}

export interface WorkflowStep {
  application: string;
  category: string;
  action: string;
  description: string;
}

export type WorkflowStatus =
  | "detected"
  | "reviewed"
  | "approved"
  | "rejected";

export type ConfidenceLabel = "low" | "medium" | "high";

export interface WorkflowCandidate {
  id: string;
  name: string;
  sequence: WorkflowStep[];
  occurrence_count: number;
  session_ids: string[];
  similarity_score: number;
  confidence: number;
  confidence_label: ConfidenceLabel;
  applications: string[];
  first_seen: string;
  last_seen: string;
  status: WorkflowStatus;
}

export interface DiscoveredWorkflowsResponse {
  workflows: WorkflowCandidate[];
  count: number;
}

export interface DiscoverResponse extends DiscoveredWorkflowsResponse {
  sequences_analyzed: number;
  threshold: number;
}

export interface WorkflowUnderstandingStep {
  order: number;
  application: string;
  category: string;
  action: string;
  purpose: string;
  input?: string | null;
  output?: string | null;
}

export interface WorkflowUnderstanding {
  id: string;
  workflow_candidate_id: string;
  workflow_name: string;
  intent: string;
  description: string;
  trigger: string;
  steps: WorkflowUnderstandingStep[];
  applications: string[];
  categories: string[];
  inputs: string[];
  outputs: string[];
  dependencies: string[];
  assumptions: string[];
  confidence: number;
  suggested_automation: string;
  provider: string;
  model: string;
  created_at: string;
  updated_at: string;
}

export interface WorkflowUnderstandingList {
  understandings: WorkflowUnderstanding[];
  count: number;
}

export type WorkflowDraftStatus =
  | "draft"
  | "pending_approval"
  | "approved"
  | "rejected";

export type WorkflowTriggerType = "event" | "schedule" | "manual";

export interface WorkflowTrigger {
  type: WorkflowTriggerType;
  application: string;
  action: string;
}

export interface GeneratedWorkflowStep {
  step_number: number;
  application: string;
  action: string;
  purpose: string;
  input?: string | null;
  output?: string | null;
  parameters: Record<string, unknown>;
}

export interface WorkflowDraft {
  id: string;
  workflow_candidate_id: string;
  understanding_id: string;
  name: string;
  description: string;
  trigger: WorkflowTrigger;
  steps: GeneratedWorkflowStep[];
  inputs: string[];
  outputs: string[];
  applications: string[];
  conditions: string[];
  dependencies: string[];
  assumptions: string[];
  confidence: number;
  status: WorkflowDraftStatus;
  rejection_reason?: string | null;
  generated_by: string;
  model: string;
  created_at: string;
  updated_at: string;
  approved_at?: string | null;
}

export interface WorkflowDraftList {
  drafts: WorkflowDraft[];
  count: number;
}

export interface RejectWorkflowRequest {
  reason?: string;
}

export type ExecutionStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type ExecutionStepStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped";

export interface ExecutionStepResult {
  id: string;
  execution_id: string;
  step_number: number;
  application: string;
  action: string;
  purpose: string;
  status: ExecutionStepStatus;
  action_type?: string | null;
  input?: string | null;
  output?: string | null;
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
}

export interface ExecutionRecord {
  id: string;
  draft_id: string;
  workflow_name: string;
  status: ExecutionStatus;
  current_step: number;
  total_steps: number;
  completed_steps: number;
  failed_step?: string | null;
  error?: string | null;
  result_summary?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  created_at: string;
  updated_at: string;
}

export interface ExecutionDetail extends ExecutionRecord {
  steps: ExecutionStepResult[];
}

export interface ExecutionList {
  executions: ExecutionRecord[];
  count: number;
}

export interface ExecutionStepList {
  execution_id: string;
  steps: ExecutionStepResult[];
  count: number;
}

export type AutomationTriggerType =
  | "manual"
  | "schedule"
  | "gmail"
  | "slack"
  | "calendar"
  | "demo_gmail"
  | "demo_slack"
  | "demo_calendar";

export interface AutomationExecutionSummary {
  id: string;
  status: ExecutionStatus;
  completed_steps: number;
  total_steps: number;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface Automation {
  id: string;
  name: string;
  description: string;
  draft_id: string;
  workflow_name: string;
  draft_status: string;
  trigger_type: AutomationTriggerType;
  trigger_config: string;
  enabled: boolean;
  execution_count: number;
  last_execution?: AutomationExecutionSummary | null;
  next_run?: string | null;
  last_run?: string | null;
  run_count: number;
  failure_count: number;
  created_at: string;
  updated_at: string;
}

export interface AutomationList {
  automations: Automation[];
  count: number;
}

export interface AutomationPayload {
  name: string;
  draft_id: string;
  description?: string;
  trigger_type: AutomationTriggerType;
  trigger_config?: string;
  enabled?: boolean;
}

export interface AutomationUpdatePayload {
  name?: string;
  description?: string;
  trigger_type?: AutomationTriggerType;
  trigger_config?: string;
  enabled?: boolean;
}

export interface AutomationRunResult {
  automation_id: string;
  execution_id: string;
  execution_status: ExecutionStatus;
  total_steps: number;
  completed_steps: number;
}

export interface AnalyticsSummary {
  total_workflows: number;
  total_automations: number;
  enabled_automations: number;
  total_executions: number;
  successful_executions: number;
  failed_executions: number;
  other_executions: number;
  success_rate: number;
  queued_jobs: number;
  running_jobs: number;
  failed_jobs: number;
  completed_jobs: number;
  active_automations: number;
  total_jobs: number;
}

export interface AnalyticsActivityBucket {
  date: string;
  completed: number;
  failed: number;
  other: number;
  total: number;
}

export interface WorkflowPerformanceRow {
  workflow_candidate_id: string | null;
  workflow_name: string;
  status: string;
  executions: number;
  successful: number;
  failed: number;
  success_rate: number;
}

export interface AnalyticsRecentExecution {
  id: string;
  draft_id: string;
  workflow_name: string;
  status: ExecutionStatus;
  completed_steps: number;
  total_steps: number;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

export interface AnalyticsFailureDetail {
  execution_id: string;
  job_id?: string | null;
  automation_id?: string | null;
  trigger?: string | null;
  retry_count?: number | null;
  error_type?: string | null;
  integration?: string | null;
  draft_id: string;
  workflow_name: string;
  failed_step: {
    step_number: number;
    application: string;
    action: string;
    error: string | null;
    action_type?: string | null;
    integration?: string | null;
  } | null;
  failed_step_label: string | null;
  error: string | null;
  completed_steps: number;
  total_steps: number;
  completed_at: string | null;
}

export interface AnalyticsJobRow {
  id: string;
  automation_id: string;
  execution_id?: string | null;
  trigger: string;
  status: string;
  retry_count: number;
  max_retries: number;
  error?: string | null;
  error_type?: string | null;
  created_at: string;
  completed_at?: string | null;
  duration_ms?: number | null;
}

export interface AnalyticsIntegrationRow {
  provider: string;
  status: string;
  count: number;
}

export interface AnalyticsResponse {
  summary: AnalyticsSummary;
  jobs: {
    counts: JobCounts;
    recent: AnalyticsJobRow[];
  };
  integrations: AnalyticsIntegrationRow[];
  activity: AnalyticsActivityBucket[];
  workflow_performance: WorkflowPerformanceRow[];
  recent_failures: AnalyticsFailureDetail[];
  recent_executions: AnalyticsRecentExecution[];
  window_days: number;
  generated_at: string;
}

export interface OllamaStatus {
  endpoint: string;
  model: string;
  timeout_seconds: number;
  status: "ok" | "unreachable";
  models: string[];
  configured_model_present: boolean;
}

export interface SystemStatus {
  application: {
    name: string;
    version: string;
    environment: string;
    debug: boolean;
  };
  server: {
    host: string;
    port: number;
    health_endpoint: string;
    readiness_endpoint: string;
  };
  ai: {
    provider: string;
    ollama: OllamaStatus;
  };
  discovery: {
    similarity_threshold: number;
  };
  database: {
    path: string;
    name: string;
    engine: string;
    status: "ok" | "unavailable";
    exists: boolean;
  };
  execution: {
    approval_required: boolean;
    allowed_actions: string[];
    external_integrations: string[];
    note: string;
  };
  oauth_diagnostics: OAuthDiagnostics;
  scheduler: {
    scheduler: SchedulerStatus;
    worker: WorkerStatus;
    demo_mode: boolean;
  };
}

// ---------------------------------------------------------------- Phase 8

export type ScheduleFrequency = "once" | "interval" | "daily" | "weekly";

export interface Schedule {
  id: string;
  automation_id: string;
  frequency: ScheduleFrequency;
  timezone: string;
  run_at?: string | null;
  interval_seconds?: number | null;
  time_of_day?: string | null;
  days_of_week: number[];
  enabled: boolean;
  next_run?: string | null;
  last_run?: string | null;
  run_count: number;
  failure_count: number;
  created_at: string;
  updated_at: string;
}

export type JobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type JobTriggerKind =
  | "manual"
  | "schedule"
  | "gmail"
  | "slack"
  | "calendar"
  | "demo"
  | "retry";

export interface BackgroundJob {
  id: string;
  automation_id: string;
  execution_id?: string | null;
  trigger: JobTriggerKind;
  event_id?: string | null;
  status: JobStatus;
  payload: Record<string, unknown>;
  error?: string | null;
  error_type?: string | null;
  retryable?: boolean | null;
  retry_count: number;
  max_retries: number;
  next_attempt_at?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
}

export interface JobList {
  jobs: BackgroundJob[];
  count: number;
}

export interface JobCounts {
  queued: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
}

export interface SchedulerStatus {
  running: boolean;
  enabled: boolean;
  tick_seconds: number;
  started_at?: string | null;
  active_schedules: number;
  next_due?: string | null;
  last_tick_at?: string | null;
  ticks: number;
  jobs_enqueued: number;
  queue?: JobCounts;
}

export interface WorkerStatus {
  running: boolean;
  enabled: boolean;
  poll_seconds: number;
  started_at?: string | null;
  last_processed_job_id?: string | null;
  last_processed_at?: string | null;
  jobs_processed: number;
  queue: JobCounts;
}

export type IntegrationState =
  | "connected"
  | "disconnected"
  | "not_configured"
  | "error";

export interface IntegrationStatus {
  provider: string;
  label: string;
  is_mock: boolean;
  configured: boolean;
  connected: boolean;
  state: IntegrationState;
  message: string;
  scopes?: string[];
  account_label?: string | null;
  account_email?: string | null;
  events_seen?: number;
  poll_interval_seconds?: number;
  stands_in_for?: string;
  token?: Record<string, unknown>;
  /** Gmail/Calendar: which scopes WorkFlowOS asks Google for. */
  requested_scopes?: string[];
  /** Gmail/Calendar: "credentials-file" | "environment" | "none". */
  credential_source?: string;
  /** Six-state connection diagnosis from the test endpoint. */
  diagnosis?: GmailDiagnosis;
  /** Gmail/Calendar: the exact redirect URI registered in Google Cloud. */
  redirect_uri?: string;
}

export interface IntegrationList {
  integrations: IntegrationStatus[];
  count: number;
  actions: string[];
}

export interface IntegrationActionInfo {
  action: string;
  provider: string | null;
  is_mock: boolean;
}

export type GmailConnectionState =
  | "not_configured"
  | "configured_not_connected"
  | "connected"
  | "token_expired_refreshable"
  | "permission_denied"
  | "authentication_error"
  | "error";

export interface GmailDiagnosis {
  state: GmailConnectionState;
  ok: boolean;
  provider: string;
  message: string;
  account_email?: string | null;
  messages_total?: number;
  threads_total?: number;
  token_refresh?: "available" | "unavailable" | "rejected";
  tested_at?: string;
}

export interface OAuthProviderDiagnostics {
  provider: string;
  is_mock: boolean;
  configured: boolean;
  connected: boolean;
  state?: string;
  account_email?: string | null;
  refresh_capability?: string;
  last_test?: string | null;
  message?: string;
}

export interface OAuthDiagnostics {
  oauth_configured: boolean;
  oauth_source: string;
  oauth_client_file_present: boolean;
  redirect_uri?: string | null;
  providers: OAuthProviderDiagnostics[];
}

export interface IntegrationEvent {
  id: string;
  provider: string;
  automation_id?: string | null;
  external_event_id: string;
  status: string;
  created_at: string;
  processed_at?: string | null;
  payload: Record<string, unknown>;
}


/* ------------------------------------------------------ gmail message picker */

export interface GmailMessageSummary {
  message_id: string;
  thread_id: string | null;
  sender: string;
  recipient: string;
  subject: string;
  date: string | null;
  snippet: string;
  unread: boolean;
  body: string | null;
  /** True when the connected account sent this (a WorkFlowOS reply). */
  is_self_sent?: boolean;
  authenticated_account?: string;
}

export interface GmailMessageList {
  provider: string;
  messages: GmailMessageSummary[];
  count: number;
  query: string;
  include_body: boolean;
}
