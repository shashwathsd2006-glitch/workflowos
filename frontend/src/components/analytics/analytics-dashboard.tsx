"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, getAnalytics } from "@/lib/api";
import { StatCard } from "@/components/stat-card";
import {
  IconActivity,
  IconAnalytics,
  IconWorkflow,
} from "@/components/icons";
import type {
  AnalyticsResponse,
  ExecutionStatus,
} from "@/types/api";

const EXECUTION_STYLES: Record<ExecutionStatus, string> = {
  queued: "border-zinc-400/25 bg-zinc-500/10 text-zinc-300",
  running: "border-sky-400/25 bg-sky-500/10 text-sky-300",
  completed: "border-emerald-400/25 bg-emerald-500/10 text-emerald-300",
  failed: "border-rose-400/25 bg-rose-500/10 text-rose-300",
  cancelled: "border-zinc-400/25 bg-zinc-500/10 text-zinc-400",
};

const WINDOWS = [7, 14, 30] as const;

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

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

function Panel({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-zinc-500">
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function ActivityChart({ data }: { data: AnalyticsResponse["activity"] }) {
  const max = Math.max(1, ...data.map((bucket) => bucket.total));
  return (
    <div className="flex h-32 items-end gap-1.5">
      {data.map((bucket) => {
        const completedHeight = Math.round((bucket.completed / max) * 100);
        const failedHeight = Math.round((bucket.failed / max) * 100);
        return (
          <div
            key={bucket.date}
            className="group flex flex-1 flex-col items-center justify-end gap-1"
            title={`${bucket.date}: ${bucket.completed} completed, ${bucket.failed} failed`}
          >
            <div className="flex h-28 w-full flex-col justify-end">
              {bucket.failed > 0 && (
                <div
                  className="w-full rounded-t bg-rose-500/70"
                  style={{ height: `${failedHeight}%` }}
                />
              )}
              {bucket.completed > 0 && (
                <div
                  className={`w-full bg-emerald-500/70 ${
                    bucket.failed > 0 ? "rounded-b" : "rounded"
                  }`}
                  style={{ height: `${completedHeight}%` }}
                />
              )}
            </div>
            <span className="font-mono text-[9px] text-zinc-600">
              {bucket.date.slice(5)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function AnalyticsDashboard() {
  const [data, setData] = useState<AnalyticsResponse | null>(null);
  const [days, setDays] = useState<number>(14);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(
    (window: number): Promise<void> =>
      getAnalytics(window).then(
        (response) => {
          setData(response);
          setError("");
        },
        (failure: unknown) => setError(errorMessage(failure)),
      ),
    [],
  );

  useEffect(() => {
    load(days).then(() => setLoading(false));
  }, [load, days]);

  const changeWindow = (next: number) => {
    setLoading(true);
    setDays(next);
  };

  const summary = data?.summary;
  const hasExecutions = (summary?.total_executions ?? 0) > 0;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-white">
            Analytics
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Reliability metrics computed from stored workflows, automations and
            executions.
          </p>
        </div>
        <div className="flex gap-1.5">
          {WINDOWS.map((window) => (
            <button
              key={window}
              type="button"
              onClick={() => changeWindow(window)}
              className={
                days === window
                  ? "rounded-lg border border-brand-400/40 bg-brand-500/10 px-3 py-1.5 text-xs text-brand-200"
                  : "rounded-lg border border-white/[0.1] px-3 py-1.5 text-xs text-zinc-400 transition-colors hover:border-white/[0.2] hover:text-zinc-200"
              }
            >
              {window}d
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-400/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {loading ? (
        <div className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] px-4 py-16 text-center text-sm text-zinc-500">
          Loading analytics…
        </div>
      ) : !data || !summary ? (
        <div className="rounded-2xl border border-dashed border-white/[0.12] bg-[#0c0c11] px-4 py-16 text-center text-sm text-zinc-500">
          Analytics unavailable.
        </div>
      ) : (
        <>
          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            <StatCard
              label="Workflows"
              value={String(summary.total_workflows)}
              sub="discovered candidates"
              icon={<IconWorkflow className="h-4 w-4" />}
            />
            <StatCard
              label="Automations"
              value={String(summary.total_automations)}
              sub={`${summary.enabled_automations} enabled`}
            />
            <StatCard
              label="Executions"
              value={String(summary.total_executions)}
              sub="total runs recorded"
              icon={<IconActivity className="h-4 w-4" />}
            />
            <StatCard
              label="Successful"
              value={String(summary.successful_executions)}
              sub="completed runs"
            />
            <StatCard
              label="Failed"
              value={String(summary.failed_executions)}
              sub="runs that stopped early"
            />
            <StatCard
              label="Success Rate"
              value={hasExecutions ? percent(summary.success_rate) : "—"}
              sub={
                hasExecutions
                  ? "completed / total"
                  : "no executions yet"
              }
              icon={<IconAnalytics className="h-4 w-4" />}
            />
          </section>

          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Active Automations"
              value={String(summary.active_automations)}
              sub="enabled and may fire"
            />
            <StatCard
              label="Queued Jobs"
              value={String(summary.queued_jobs)}
              sub="waiting for the worker"
            />
            <StatCard
              label="Failed Jobs"
              value={String(summary.failed_jobs)}
              sub="after retries"
            />
            <StatCard
              label="Completed Jobs"
              value={String(summary.completed_jobs)}
              sub="total jobs processed"
            />
          </section>

          <Panel title={`Execution activity · last ${data.window_days} days`}>
            <ActivityChart data={data.activity} />
            <div className="mt-3 flex gap-4 text-[11px] text-zinc-500">
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm bg-emerald-500/70" />
                completed
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm bg-rose-500/70" />
                failed
              </span>
            </div>
          </Panel>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Workflow performance">
              {data.workflow_performance.length === 0 ? (
                <p className="text-sm text-zinc-600">
                  No workflows discovered yet. Run discovery on the Workflows
                  page.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-white/[0.07] text-[11px] uppercase tracking-[0.14em] text-zinc-600">
                        <th className="pb-2 font-medium">Workflow</th>
                        <th className="pb-2 font-medium">Runs</th>
                        <th className="pb-2 font-medium">OK</th>
                        <th className="pb-2 font-medium">Failed</th>
                        <th className="pb-2 font-medium">Rate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.workflow_performance.map((row) => (
                        <tr
                          key={row.workflow_candidate_id ?? row.workflow_name}
                          className="border-b border-white/[0.04] last:border-0"
                        >
                          <td className="py-2 pr-3 text-zinc-300">
                            {row.workflow_name}
                          </td>
                          <td className="py-2 pr-3 font-mono text-zinc-400">
                            {row.executions}
                          </td>
                          <td className="py-2 pr-3 font-mono text-emerald-300">
                            {row.successful}
                          </td>
                          <td className="py-2 pr-3 font-mono text-rose-300">
                            {row.failed}
                          </td>
                          <td className="py-2 font-mono text-zinc-300">
                            {row.executions > 0 ? percent(row.success_rate) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>

            <Panel title="Failure analysis">
              {data.recent_failures.length === 0 ? (
                <p className="text-sm text-zinc-600">
                  {hasExecutions
                    ? "No failures recorded in this window."
                    : "No executions yet, so there is nothing to analyse."}
                </p>
              ) : (
                <ul className="space-y-2">
                  {data.recent_failures.map((failure) => (
                    <li
                      key={failure.execution_id}
                      className="rounded-lg border border-rose-400/25 bg-rose-500/10 px-3 py-2"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-sm text-rose-200">
                          {failure.workflow_name}
                        </span>
                        <span className="font-mono text-[11px] text-rose-300/80">
                          {formatDateTime(failure.completed_at)}
                        </span>
                      </div>
                      <p className="mt-1 font-mono text-[11px] text-rose-300/80">
                        {failure.execution_id}
                        {failure.failed_step
                          ? ` · step ${failure.failed_step.step_number}: ${failure.failed_step.action}`
                          : ""}
                      </p>
                      <p className="mt-0.5 font-mono text-[10px] text-rose-200/60">
                        {[
                          failure.automation_id
                            ? `automation ${failure.automation_id}`
                            : null,
                          failure.job_id ? `job ${failure.job_id}` : null,
                          failure.integration
                            ? `integration ${failure.integration}`
                            : null,
                          failure.retry_count
                            ? `retries ${failure.retry_count}`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                      {(failure.failed_step?.error ?? failure.error) && (
                        <p className="mt-1 text-[11px] text-rose-200/80">
                          {failure.failed_step?.error ?? failure.error}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Background jobs">
              {data.jobs.recent.length === 0 ? (
                <p className="text-sm text-zinc-600">
                  No background jobs yet. Schedule or run an automation.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {data.jobs.recent.slice(0, 8).map((job) => (
                    <li
                      key={job.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/[0.07] bg-black/25 px-3 py-2"
                    >
                      <div className="min-w-0">
                        <p className="font-mono text-[11px] text-zinc-400">
                          {job.id} · {job.trigger}
                        </p>
                        <p className="text-[11px] text-zinc-600">
                          {formatDateTime(job.created_at)}
                          {job.retry_count > 0
                            ? ` · retry ${job.retry_count}/${job.max_retries}`
                            : ""}
                          {job.error ? ` · ${job.error}` : ""}
                        </p>
                      </div>
                      <span
                        className={`shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${EXECUTION_STYLES[job.status as ExecutionStatus] ?? "border-zinc-400/25 bg-zinc-500/10 text-zinc-300"}`}
                      >
                        {job.status}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>

            <Panel title="Integration activity">
              {data.integrations.length === 0 ? (
                <p className="text-sm text-zinc-600">
                  No integration events recorded yet.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {data.integrations.map((row) => (
                    <li
                      key={`${row.provider}-${row.status}`}
                      className="flex items-center justify-between gap-2 rounded-lg border border-white/[0.07] bg-black/25 px-3 py-2"
                    >
                      <span className="font-mono text-[11px] text-zinc-300">
                        {row.provider}
                      </span>
                      <span className="text-[11px] text-zinc-500">
                        {row.status}
                      </span>
                      <span className="font-mono text-sm text-white">
                        {row.count}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>

          <Panel title="Recent executions">
            {data.recent_executions.length === 0 ? (
              <div className="py-6 text-center">
                <p className="text-sm text-zinc-400">No executions yet.</p>
                <p className="mt-1 text-xs text-zinc-600">
                  Approve a workflow draft and run it to see results here.
                </p>
                <Link
                  href="/workflows"
                  className="mt-4 inline-block rounded-lg border border-white/[0.1] px-4 py-2 text-xs text-zinc-300 transition-colors hover:border-brand-400/40 hover:text-brand-300"
                >
                  Go to Workflows →
                </Link>
              </div>
            ) : (
              <ul className="space-y-1.5">
                {data.recent_executions.map((execution) => (
                  <li
                    key={execution.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/[0.07] bg-black/25 px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm text-zinc-300">
                        {execution.workflow_name}
                      </p>
                      <p className="font-mono text-[11px] text-zinc-600">
                        {execution.id} · {execution.completed_steps}/
                        {execution.total_steps} steps ·{" "}
                        {formatDateTime(execution.started_at)}
                      </p>
                    </div>
                    <span
                      className={`shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${EXECUTION_STYLES[execution.status]}`}
                    >
                      {execution.status}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}
