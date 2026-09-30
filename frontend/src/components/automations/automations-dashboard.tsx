"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ApiError,
  createAutomation,
  deleteAutomation,
  disableAutomation,
  enableAutomation,
  getAutomations,
  getBackgroundJobs,
  getExecutions,
  getIntegrations,
  getWorkflowDrafts,
  runAutomation,
  runAutomationDemo,
} from "@/lib/api";
import { StatCard } from "@/components/stat-card";
import { IconAutomation, IconSpark, IconWorkflow } from "@/components/icons";
import type {
  Automation,
  AutomationPayload,
  AutomationTriggerType,
  BackgroundJob,
  ExecutionRecord,
  ExecutionStatus,
  WorkflowDraft,
} from "@/types/api";

const TRIGGER_LABELS: Record<AutomationTriggerType, string> = {
  manual: "Manual",
  schedule: "Schedule",
  gmail: "Gmail",
  slack: "Slack",
  calendar: "Calendar",
  demo_gmail: "Gmail (demo)",
  demo_slack: "Slack (demo)",
  demo_calendar: "Calendar (demo)",
};

const SCHEDULE_FREQUENCIES = [
  "interval",
  "daily",
  "weekly",
  "once",
] as const;

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function isDemoTrigger(trigger: AutomationTriggerType): boolean {
  return trigger.startsWith("demo_");
}

const EXECUTION_STYLES: Record<ExecutionStatus, string> = {
  queued: "border-zinc-400/25 bg-zinc-500/10 text-zinc-300",
  running: "border-sky-400/25 bg-sky-500/10 text-sky-300",
  completed: "border-emerald-400/25 bg-emerald-500/10 text-emerald-300",
  failed: "border-rose-400/25 bg-rose-500/10 text-rose-300",
  cancelled: "border-zinc-400/25 bg-zinc-500/10 text-zinc-400",
};

function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString([], {
    hour12: false,
  })}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.detail) return error.detail;
    return error.status === 0
      ? "Backend unreachable — is the API running?"
      : `Backend request failed (HTTP ${error.status})`;
  }
  return error instanceof Error ? error.message : "Something went wrong";
}

function StatusPill({ enabled }: { enabled: boolean }) {
  return (
    <span
      className={
        enabled
          ? "shrink-0 rounded-md border border-emerald-400/25 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-emerald-300"
          : "shrink-0 rounded-md border border-zinc-400/25 bg-zinc-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-zinc-400"
      }
    >
      {enabled ? "Enabled" : "Disabled"}
    </span>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
        {label}
      </p>
      <p className="mt-0.5 text-sm text-zinc-300">{value}</p>
    </div>
  );
}

function TriggerConfigFields({
  trigger,
  config,
  onChange,
}: {
  trigger: AutomationTriggerType;
  config: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const label =
    "block text-[11px] uppercase tracking-[0.16em] text-zinc-600";
  const input =
    "mt-1 w-full rounded-lg border border-white/[0.1] bg-white/[0.03] px-3 py-2 text-sm text-white outline-none placeholder:text-zinc-600 focus:border-brand-400/50";

  if (trigger === "schedule") {
    const frequency = String(config.frequency ?? "interval");
    return (
      <div className="grid gap-3 sm:col-span-2">
        <div>
          <label className={label} htmlFor="sched-frequency">
            Frequency
          </label>
          <select
            id="sched-frequency"
            className={input}
            value={frequency}
            onChange={(event) =>
              onChange({ ...config, frequency: event.target.value })
            }
          >
            {SCHEDULE_FREQUENCIES.map((option) => (
              <option key={option} value={option} className="bg-[#0c0c11]">
                {option}
              </option>
            ))}
          </select>
        </div>

        {frequency === "interval" && (
          <div>
            <label className={label} htmlFor="sched-interval">
              Interval (seconds)
            </label>
            <input
              id="sched-interval"
              className={input}
              type="number"
              min={10}
              value={String(config.interval_seconds ?? 3600)}
              onChange={(event) =>
                onChange({
                  ...config,
                  interval_seconds: Number(event.target.value),
                })
              }
            />
          </div>
        )}

        {(frequency === "daily" || frequency === "weekly") && (
          <div>
            <label className={label} htmlFor="sched-time">
              Time (HH:MM, 24h)
            </label>
            <input
              id="sched-time"
              className={input}
              placeholder="09:00"
              value={String(config.time_of_day ?? "09:00")}
              onChange={(event) =>
                onChange({ ...config, time_of_day: event.target.value })
              }
            />
          </div>
        )}

        {frequency === "weekly" && (
          <div className="sm:col-span-2">
            <p className={label}>Days of week</p>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {DAYS.map((day, index) => {
                const selected = ((config.days_of_week as number[]) ?? []).includes(
                  index,
                );
                return (
                  <button
                    key={day}
                    type="button"
                    onClick={() => {
                      const current =
                        (config.days_of_week as number[]) ?? [];
                      onChange({
                        ...config,
                        days_of_week: selected
                          ? current.filter((value) => value !== index)
                          : [...current, index],
                      });
                    }}
                    className={
                      selected
                        ? "rounded-lg border border-brand-400/40 bg-brand-500/10 px-3 py-1.5 text-xs text-brand-200"
                        : "rounded-lg border border-white/[0.1] px-3 py-1.5 text-xs text-zinc-400 hover:border-white/[0.2]"
                    }
                  >
                    {day}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div>
          <label className={label} htmlFor="sched-tz">
            Timezone
          </label>
          <input
            id="sched-tz"
            className={input}
            placeholder="UTC or Asia/Tokyo"
            value={String(config.timezone ?? "UTC")}
            onChange={(event) =>
              onChange({ ...config, timezone: event.target.value })
            }
          />
        </div>
      </div>
    );
  }

  if (trigger === "gmail" || trigger === "demo_gmail") {
    return (
      <div className="grid gap-3 sm:col-span-2">
        <div>
          <label className={label} htmlFor="gmail-from">
            From
          </label>
          <input
            id="gmail-from"
            className={input}
            placeholder="support@example.com"
            value={String(config.from ?? "")}
            onChange={(event) =>
              onChange({ ...config, from: event.target.value })
            }
          />
        </div>
        <div>
          <label className={label} htmlFor="gmail-subject">
            Subject contains
          </label>
          <input
            id="gmail-subject"
            className={input}
            placeholder="urgent"
            value={String(config.subject ?? "")}
            onChange={(event) =>
              onChange({ ...config, subject: event.target.value })
            }
          />
        </div>
        <div>
          <label className={label} htmlFor="gmail-interval">
            Poll interval (seconds)
          </label>
          <input
            id="gmail-interval"
            className={input}
            type="number"
            min={5}
            value={String(config.poll_interval_seconds ?? 60)}
            onChange={(event) =>
              onChange({
                ...config,
                poll_interval_seconds: Number(event.target.value),
              })
            }
          />
        </div>
        <label className="flex items-center gap-2 text-sm text-zinc-300">
          <input
            type="checkbox"
            checked={config.unread !== false}
            onChange={(event) =>
              onChange({ ...config, unread: event.target.checked })
            }
            className="h-4 w-4 rounded border-white/[0.2] bg-white/[0.05]"
          />
          Only unread messages
        </label>
      </div>
    );
  }

  if (trigger === "calendar" || trigger === "demo_calendar") {
    return (
      <div className="grid gap-3 sm:col-span-2">
        <div>
          <label className={label} htmlFor="cal-id">
            Calendar ID
          </label>
          <input
            id="cal-id"
            className={input}
            placeholder="primary"
            value={String(config.calendar_id ?? "primary")}
            onChange={(event) =>
              onChange({ ...config, calendar_id: event.target.value })
            }
          />
        </div>
        <div>
          <label className={label} htmlFor="cal-criteria">
            Title contains
          </label>
          <input
            id="cal-criteria"
            className={input}
            placeholder="standup"
            value={String(config.criteria ?? "")}
            onChange={(event) =>
              onChange({ ...config, criteria: event.target.value })
            }
          />
        </div>
        <div>
          <label className={label} htmlFor="cal-interval">
            Poll interval (seconds)
          </label>
          <input
            id="cal-interval"
            className={input}
            type="number"
            min={5}
            value={String(config.poll_interval_seconds ?? 60)}
            onChange={(event) =>
              onChange({
                ...config,
                poll_interval_seconds: Number(event.target.value),
              })
            }
          />
        </div>
      </div>
    );
  }

  return (
    <p className="text-xs text-zinc-600 sm:col-span-2">
      Manual automations only run when you press Run now.
    </p>
  );
}

function CreateAutomationForm({
  drafts,
  demoAvailable,
  onCreated,
  onCancel,
}: {
  drafts: WorkflowDraft[];
  demoAvailable: boolean;
  onCreated: (automation: Automation) => void;
  onCancel: () => void;
}) {
  const approved = useMemo(
    () => drafts.filter((draft) => draft.status === "approved"),
    [drafts],
  );

  const [name, setName] = useState("");
  const [draftId, setDraftId] = useState(approved[0]?.id ?? "");
  const [description, setDescription] = useState("");
  const [triggerType, setTriggerType] =
    useState<AutomationTriggerType>("manual");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const effectiveDraftId = draftId || approved[0]?.id || "";

  const triggerOptions: { value: AutomationTriggerType; label: string }[] = [
    { value: "manual", label: "Manual" },
    { value: "schedule", label: "Schedule" },
    { value: "gmail", label: "Gmail" },
    { value: "calendar", label: "Calendar" },
  ];
  if (demoAvailable) {
    triggerOptions.push(
      { value: "demo_gmail", label: "Gmail (demo integration)" },
      { value: "demo_slack", label: "Slack (demo integration)" },
      { value: "demo_calendar", label: "Calendar (demo integration)" },
    );
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!effectiveDraftId) {
      setError("Select an approved workflow draft first.");
      return;
    }
    setBusy(true);
    setError("");
    const payload: AutomationPayload = {
      name: name.trim() || "Untitled automation",
      draft_id: effectiveDraftId,
      description: description.trim(),
      trigger_type: triggerType,
      trigger_config: JSON.stringify(config),
      enabled,
    };
    try {
      onCreated(await createAutomation(payload));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  const label = "block text-[11px] uppercase tracking-[0.16em] text-zinc-600";
  const input =
    "mt-1 w-full rounded-lg border border-white/[0.1] bg-white/[0.03] px-3 py-2 text-sm text-white outline-none placeholder:text-zinc-600 focus:border-brand-400/50";

  return (
    <form
      onSubmit={submit}
      className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5"
    >
      <h2 className="text-sm font-semibold text-white">Create Automation</h2>
      <p className="mt-1 text-xs text-zinc-500">
        Bind an approved workflow draft to a trigger. Scheduled and event
        automations are picked up by the background worker.
      </p>

      {approved.length === 0 ? (
        <p className="mt-4 rounded-xl border border-amber-400/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          No approved workflow drafts yet. Generate and approve a draft on the{" "}
          <Link href="/workflows" className="underline">
            Workflows
          </Link>{" "}
          page first.
        </p>
      ) : (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label className={label} htmlFor="automation-name">
              Name
            </label>
            <input
              id="automation-name"
              className={input}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Customer request automation"
            />
          </div>

          <div>
            <label className={label} htmlFor="automation-draft">
              Workflow
            </label>
            <select
              id="automation-draft"
              className={input}
              value={effectiveDraftId}
              onChange={(event) => setDraftId(event.target.value)}
            >
              {approved.map((draft) => (
                <option key={draft.id} value={draft.id} className="bg-[#0c0c11]">
                  {draft.name} ({draft.id})
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className={label} htmlFor="automation-trigger">
              Trigger
            </label>
            <select
              id="automation-trigger"
              className={input}
              value={triggerType}
              onChange={(event) => {
                setTriggerType(event.target.value as AutomationTriggerType);
                setConfig({});
              }}
            >
              {triggerOptions.map((option) => (
                <option key={option.value} value={option.value} className="bg-[#0c0c11]">
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <TriggerConfigFields
            trigger={triggerType}
            config={config}
            onChange={setConfig}
          />

          <div className="sm:col-span-2">
            <label className={label} htmlFor="automation-description">
              Description
            </label>
            <input
              id="automation-description"
              className={input}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="What this automation is for"
            />
          </div>

          <label className="flex items-center gap-2 text-sm text-zinc-300 sm:col-span-2">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
              className="h-4 w-4 rounded border-white/[0.2] bg-white/[0.05]"
            />
            Enabled
          </label>
        </div>
      )}

      {error && (
        <p className="mt-3 rounded-lg border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </p>
      )}

      <div className="mt-4 flex gap-2">
        <button
          type="submit"
          disabled={busy || approved.length === 0}
          className="rounded-xl bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-brand-600/20 transition-all hover:from-brand-400 hover:to-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Creating…" : "Create Automation"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-xl border border-white/[0.1] px-4 py-2 text-sm text-zinc-300 transition-colors hover:border-white/[0.25]"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

const JOB_STYLES: Record<string, string> = {
  queued: "border-zinc-400/25 bg-zinc-500/10 text-zinc-300",
  running: "border-sky-400/25 bg-sky-500/10 text-sky-300",
  completed: "border-emerald-400/25 bg-emerald-500/10 text-emerald-300",
  failed: "border-rose-400/25 bg-rose-500/10 text-rose-300",
  cancelled: "border-zinc-400/25 bg-zinc-500/10 text-zinc-400",
};

function AutomationDetail({
  automation,
  executions,
  jobs,
  busy,
  onToggle,
  onRun,
  onDemo,
  onDelete,
}: {
  automation: Automation;
  executions: ExecutionRecord[];
  jobs: BackgroundJob[];
  busy: boolean;
  onToggle: () => void;
  onRun: () => void;
  onDemo: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="mt-4 space-y-4 rounded-xl border border-white/[0.07] bg-black/20 p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" value={automation.name} />
        <Field
          label="Workflow"
          value={`${automation.workflow_name} (${automation.draft_id})`}
        />
        <Field
          label="Trigger"
          value={`${TRIGGER_LABELS[automation.trigger_type]}${
            automation.trigger_config ? ` · ${automation.trigger_config}` : ""
          }`}
        />
        <Field label="Draft status" value={automation.draft_status} />
        <Field label="Description" value={automation.description || "—"} />
        <Field label="Executions" value={String(automation.execution_count)} />
        <Field
          label="Scheduled runs"
          value={`${automation.run_count} (${automation.failure_count} failed)`}
        />
        <Field label="Last run" value={formatDateTime(automation.last_run)} />
        <Field label="Next run" value={formatDateTime(automation.next_run)} />
      </div>

      {isDemoTrigger(automation.trigger_type) && (
        <p className="rounded-lg border border-amber-400/25 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-300">
          DEMO MODE — this trigger uses a local simulated integration. No real
          Gmail, Slack or Calendar account is connected and nothing leaves this
          machine.
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={onToggle}
          className="rounded-lg border border-white/[0.1] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:border-brand-400/40 hover:text-brand-200 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {automation.enabled ? "Disable" : "Enable"}
        </button>
        <button
          type="button"
          disabled={busy || !automation.enabled}
          onClick={onRun}
          className="rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 transition-colors hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Working…" : "Run now"}
        </button>
        {isDemoTrigger(automation.trigger_type) && (
          <button
            type="button"
            disabled={busy || !automation.enabled}
            onClick={onDemo}
            className="rounded-lg border border-amber-400/30 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-300 transition-colors hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? "Working…" : "Run demo event"}
          </button>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={onDelete}
          className="rounded-lg border border-white/[0.1] px-3 py-1.5 text-xs text-zinc-400 transition-colors hover:border-rose-400/40 hover:text-rose-300 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Delete
        </button>
      </div>

      <div>
        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
          Execution history
        </p>
        {executions.length === 0 ? (
          <p className="mt-1 text-sm text-zinc-600">
            No executions yet for this automation.
          </p>
        ) : (
          <ul className="mt-2 space-y-1.5">
            {executions.map((execution) => (
              <li
                key={execution.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/[0.07] bg-black/25 px-3 py-2"
              >
                <span className="font-mono text-[11px] text-zinc-400">
                  {execution.id}
                </span>
                <span className="text-xs text-zinc-500">
                  {execution.completed_steps}/{execution.total_steps} steps ·{" "}
                  {formatDateTime(execution.started_at)}
                </span>
                <span
                  className={`rounded-md border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${EXECUTION_STYLES[execution.status]}`}
                >
                  {execution.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
          Background jobs
        </p>
        {jobs.length === 0 ? (
          <p className="mt-1 text-sm text-zinc-600">
            No queued jobs yet for this automation.
          </p>
        ) : (
          <ul className="mt-2 space-y-1.5">
            {jobs.slice(0, 8).map((job) => (
              <li
                key={job.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/[0.07] bg-black/25 px-3 py-2"
              >
                <span className="min-w-0">
                  <span className="block font-mono text-[11px] text-zinc-400">
                    {job.id} · {job.trigger}
                  </span>
                  <span className="block text-[11px] text-zinc-600">
                    {formatDateTime(job.created_at)}
                    {job.retry_count > 0
                      ? ` · retry ${job.retry_count}/${job.max_retries}`
                      : ""}
                  </span>
                  {job.error && (
                    <span className="block text-[11px] text-rose-300">
                      {job.error}
                    </span>
                  )}
                </span>
                <span
                  className={`shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${JOB_STYLES[job.status] ?? JOB_STYLES.queued}`}
                >
                  {job.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {executions.some((execution) => execution.status === "failed") && (
        <div>
          <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
            Recent failures
          </p>
          <ul className="mt-2 space-y-1.5">
            {executions
              .filter((execution) => execution.status === "failed")
              .slice(0, 3)
              .map((execution) => (
                <li
                  key={`fail-${execution.id}`}
                  className="rounded-lg border border-rose-400/25 bg-rose-500/10 px-3 py-2"
                >
                  <p className="font-mono text-[11px] text-rose-300">
                    {execution.id} · {execution.failed_step ?? "step failed"}
                  </p>
                  {execution.error && (
                    <p className="mt-0.5 text-[11px] text-rose-200/80">
                      {execution.error}
                    </p>
                  )}
                </li>
              ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function AutomationsDashboard() {
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [drafts, setDrafts] = useState<WorkflowDraft[]>([]);
  const [executions, setExecutions] = useState<ExecutionRecord[]>([]);
  const [jobs, setJobs] = useState<BackgroundJob[]>([]);
  const [demoAvailable, setDemoAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback((): Promise<void> => {
    return Promise.all([
      getAutomations(),
      getWorkflowDrafts(),
      getIntegrations(),
    ]).then(
      ([automationList, draftList, integrationList]) => {
        setAutomations(automationList.automations);
        setDrafts(draftList.drafts);
        // Demo trigger options only appear when demo mode is actually on.
        setDemoAvailable(
          integrationList.integrations.some(
            (item) => item.is_mock && item.configured,
          ),
        );
        setError("");
      },
      (failure: unknown) => setError(errorMessage(failure)),
    );
  }, []);

  useEffect(() => {
    load().then(() => setLoading(false));
  }, [load]);

  const openDetail = async (automation: Automation) => {
    setSelectedId(automation.id);
    setError("");
    try {
      const [list, jobList] = await Promise.all([
        getExecutions({ draft_id: automation.draft_id }),
        getBackgroundJobs({ automation_id: automation.id, limit: 20 }),
      ]);
      setExecutions(list.executions);
      setJobs(jobList.jobs);
    } catch (failure) {
      setError(errorMessage(failure));
    }
  };

  const act = async (
    automationId: string,
    action: () => Promise<Automation>,
    message: string,
  ) => {
    setBusyId(automationId);
    setError("");
    setNotice("");
    try {
      setAction(automationId, await action());
      setNotice(message);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusyId(null);
    }
  };

  const setAction = (automationId: string, updated: Automation) => {
    setAutomations((prev) =>
      prev.map((item) => (item.id === automationId ? updated : item)),
    );
  };

  const enabled = automations.filter((item) => item.enabled).length;
  const totalRuns = automations.reduce(
    (sum, item) => sum + item.execution_count,
    0,
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-white">
            Automations
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Bind approved workflow drafts to a trigger and run them through the
            execution engine.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowCreate((prev) => !prev)}
          className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2.5 text-sm font-medium text-white shadow-lg shadow-brand-600/20 transition-all hover:from-brand-400 hover:to-brand-600"
        >
          <IconSpark className="h-4 w-4" />
          {showCreate ? "Close" : "Create Automation"}
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-400/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}
      {notice && !error && (
        <div className="rounded-xl border border-emerald-400/25 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
          {notice}
        </div>
      )}

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Automations"
          value={loading ? "—" : String(automations.length)}
          sub="bound to approved drafts"
          icon={<IconAutomation className="h-4 w-4" />}
        />
        <StatCard
          label="Enabled"
          value={loading ? "—" : String(enabled)}
          sub="may be run now"
        />
        <StatCard
          label="Total Executions"
          value={loading ? "—" : String(totalRuns)}
          sub="across all automations"
        />
        <StatCard
          label="Approved Drafts"
          value={loading ? "—" : String(drafts.filter((d) => d.status === "approved").length)}
          sub="available to automate"
          icon={<IconWorkflow className="h-4 w-4" />}
        />
      </section>

      {showCreate && (
        <CreateAutomationForm
          drafts={drafts}
          demoAvailable={demoAvailable}
          onCancel={() => setShowCreate(false)}
          onCreated={(created) => {
            setAutomations((prev) => [...prev, created]);
            setShowCreate(false);
            setExecutions([]);
            setNotice(`Created ${created.name}`);
            void openDetail(created);
          }}
        />
      )}

      <section>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-zinc-500">
            Automations
          </h2>
          <span className="font-mono text-xs text-zinc-600">
            {automations.length} configured
          </span>
        </div>

        {loading ? (
          <div className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] px-4 py-12 text-center text-sm text-zinc-500">
            Loading automations…
          </div>
        ) : automations.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/[0.12] bg-[#0c0c11] px-4 py-12 text-center">
            <IconAutomation className="mx-auto h-6 w-6 text-zinc-600" />
            <p className="mt-3 text-sm text-zinc-400">No automations yet.</p>
            <p className="mt-1 text-xs text-zinc-600">
              Approve a workflow draft, then create an automation from it.
            </p>
            <div className="mt-4 flex justify-center gap-3">
              <Link
                href="/workflows"
                className="rounded-lg border border-white/[0.1] px-4 py-2 text-xs text-zinc-300 transition-colors hover:border-brand-400/40 hover:text-brand-300"
              >
                Go to Workflows →
              </Link>
              <button
                type="button"
                onClick={() => setShowCreate(true)}
                className="rounded-lg border border-brand-400/30 px-4 py-2 text-xs text-brand-300 transition-colors hover:bg-brand-500/10"
              >
                Create Automation
              </button>
            </div>
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {automations.map((automation) => (
              <article
                key={automation.id}
                className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5 transition-colors hover:border-brand-400/25"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-base font-semibold text-white">
                      {automation.name}
                    </p>
                    <p className="mt-1 truncate text-sm text-zinc-500">
                      {automation.workflow_name}
                    </p>
                  </div>
                  <StatusPill enabled={automation.enabled} />
                </div>

                <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <div>
                    <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                      Trigger
                    </p>
                    <p className="mt-0.5 text-sm text-zinc-300">
                      {TRIGGER_LABELS[automation.trigger_type]}
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                      Runs
                    </p>
                    <p className="mt-0.5 font-mono text-sm text-zinc-300">
                      {automation.execution_count}
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                      Last run
                    </p>
                    <p className="mt-0.5 text-sm text-zinc-300">
                      {automation.last_execution?.status ?? "never"}
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                      Next run
                    </p>
                    <p className="mt-0.5 font-mono text-[11px] text-zinc-300">
                      {automation.next_run
                        ? formatDateTime(automation.next_run)
                        : "—"}
                    </p>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => {
                    if (selectedId === automation.id) {
                      setSelectedId(null);
                      setExecutions([]);
                    } else {
                      void openDetail(automation);
                    }
                  }}
                  className="mt-4 w-full rounded-xl border border-white/[0.1] bg-white/[0.03] px-4 py-2 text-sm font-medium text-zinc-300 transition-colors hover:border-brand-400/40 hover:text-brand-200"
                >
                  {selectedId === automation.id ? "Hide Details" : "View Details"}
                </button>

                {selectedId === automation.id && (
                  <AutomationDetail
                    automation={automation}
                    executions={executions}
                    jobs={jobs}
                    busy={busyId === automation.id}
                    onToggle={() =>
                      act(
                        automation.id,
                        () =>
                          automation.enabled
                            ? disableAutomation(automation.id)
                            : enableAutomation(automation.id),
                        automation.enabled
                          ? "Automation disabled"
                          : "Automation enabled",
                      )
                    }
                    onRun={() =>
                      act(
                        automation.id,
                        async () => {
                          await runAutomation(automation.id);
                          await load();
                          return automation;
                        },
                        "Automation executed",
                      )
                    }
                    onDemo={async () => {
                      setBusyId(automation.id);
                      setError("");
                      setNotice("");
                      try {
                        const result = await runAutomationDemo(automation.id);
                        setNotice(
                          `Demo run ${result.execution_status} (${result.completed_steps}/${result.total_steps} steps) via the background worker`,
                        );
                        await load();
                        await openDetail(automation);
                      } catch (failure) {
                        setError(errorMessage(failure));
                      } finally {
                        setBusyId(null);
                      }
                    }}
                    onDelete={async () => {
                      setBusyId(automation.id);
                      setError("");
                      try {
                        await deleteAutomation(automation.id);
                        setAutomations((prev) =>
                          prev.filter((item) => item.id !== automation.id),
                        );
                        setSelectedId(null);
                        setExecutions([]);
                        setNotice("Automation deleted");
                      } catch (failure) {
                        setError(errorMessage(failure));
                      } finally {
                        setBusyId(null);
                      }
                    }}
                  />
                )}
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
