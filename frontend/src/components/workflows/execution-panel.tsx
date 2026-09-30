"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  executeWorkflowDraft,
  getExecution,
  getExecutions,
} from "@/lib/api";
import { useEmailSelection } from "@/components/workflows/email-selection";
import type {
  ExecutionDetail,
  ExecutionRecord,
  ExecutionStatus,
  ExecutionStepResult,
  ExecutionStepStatus,
} from "@/types/api";

const EXECUTION_STYLES: Record<ExecutionStatus, string> = {
  queued: "border-zinc-400/25 bg-zinc-500/10 text-zinc-300",
  running: "border-sky-400/25 bg-sky-500/10 text-sky-300",
  completed: "border-emerald-400/25 bg-emerald-500/10 text-emerald-300",
  failed: "border-rose-400/25 bg-rose-500/10 text-rose-300",
  cancelled: "border-zinc-400/25 bg-zinc-500/10 text-zinc-400",
};

const STEP_STYLES: Record<ExecutionStepStatus, string> = {
  pending: "border-white/[0.08] bg-white/[0.02] text-zinc-600",
  running: "border-sky-400/25 bg-sky-500/10 text-sky-300",
  completed: "border-emerald-400/25 bg-emerald-500/10 text-emerald-300",
  failed: "border-rose-400/25 bg-rose-500/10 text-rose-300",
  skipped: "border-white/[0.08] bg-white/[0.02] text-zinc-600",
};

const STEP_MARKS: Record<ExecutionStepStatus, string> = {
  pending: "○",
  running: "◐",
  completed: "✓",
  failed: "✕",
  skipped: "–",
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

/**
 * True when the run was refused because the workflow is not approved yet.
 * That is the approval gate doing its job, so it is presented as an
 * explanatory notice rather than a fault. Every other failure stays an error.
 */
function isApprovalRefusal(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.status !== 409) return false;
  return /only approved workflows may be executed/i.test(
    error.detail ?? error.message,
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

function StepRow({ step }: { step: ExecutionStepResult }) {
  return (
    <li
      className={`rounded-lg border px-3 py-2 ${STEP_STYLES[step.status]}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm">
          <span className="mr-2 font-mono text-xs opacity-70">
            {STEP_MARKS[step.status]} {step.step_number}.
          </span>
          <span className="text-white">{step.action}</span>
          <span className="ml-2 text-xs opacity-70">{step.application}</span>
        </span>
        {step.action_type && (
          <span className="rounded-md border border-white/[0.1] bg-black/30 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
            {step.action_type}
          </span>
        )}
      </div>
      {step.purpose && (
        <p className="mt-1 text-[11px] opacity-70">{step.purpose}</p>
      )}
      {step.output && (
        <p className="mt-1 break-words font-mono text-[11px] text-zinc-500">
          → {step.output}
        </p>
      )}
      {step.error && (
        <p className="mt-1 break-words text-[11px] text-rose-300">
          {step.error}
        </p>
      )}
    </li>
  );
}

function ExecutionView({ execution }: { execution: ExecutionDetail }) {
  const progress =
    execution.total_steps > 0
      ? Math.round((execution.completed_steps / execution.total_steps) * 100)
      : 0;

  return (
    <div className="mt-3 space-y-4 rounded-xl border border-white/[0.07] bg-black/25 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-white">
            {execution.workflow_name}
          </p>
          <p className="mt-0.5 font-mono text-[11px] text-zinc-600">
            {execution.id} · draft {execution.draft_id}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${EXECUTION_STYLES[execution.status]}`}
        >
          {execution.status}
        </span>
      </div>

      <div>
        <div className="flex items-center justify-between text-[11px] text-zinc-500">
          <span>
            Step {execution.current_step} of {execution.total_steps}
          </span>
          <span>
            {execution.completed_steps} completed · {progress}%
          </span>
        </div>
        <div className="mt-1 h-1 rounded-full bg-white/[0.06]">
          <div
            className={`h-1 rounded-full ${
              execution.status === "failed" ? "bg-rose-500" : "bg-emerald-500"
            }`}
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Started" value={formatDateTime(execution.started_at)} />
        <Field label="Completed" value={formatDateTime(execution.completed_at)} />
        <Field
          label="Duration"
          value={
            execution.duration_ms !== null && execution.duration_ms !== undefined
              ? `${execution.duration_ms} ms`
              : "—"
          }
        />
        <Field
          label="Current step"
          value={
            execution.failed_step ??
            `${execution.current_step} of ${execution.total_steps}`
          }
        />
      </div>

      {execution.error && (
        <div className="rounded-lg border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {execution.error}
        </div>
      )}
      {execution.result_summary && (
        <div className="rounded-lg border border-white/[0.07] bg-black/20 px-3 py-2 text-xs leading-relaxed text-zinc-400">
          {execution.result_summary}
        </div>
      )}

      <div>
        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
          Steps
        </p>
        <ol className="mt-2 space-y-2">
          {execution.steps.map((step) => (
            <StepRow key={step.id} step={step} />
          ))}
        </ol>
      </div>
    </div>
  );
}

function ExecutionSummary({ record }: { record: ExecutionRecord }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/[0.07] bg-black/20 px-3 py-2">
      <div className="min-w-0">
        <p className="truncate text-sm text-white">{record.workflow_name}</p>
        <p className="font-mono text-[11px] text-zinc-600">
          {record.id} · {record.completed_steps}/{record.total_steps} steps ·{" "}
          {formatDateTime(record.started_at)}
        </p>
      </div>
      <span
        className={`shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${EXECUTION_STYLES[record.status]}`}
      >
        {record.status}
      </span>
    </div>
  );
}

interface ExecutionPanelProps {
  draftId: string | undefined;
  draftStatus: string;
}

export function ExecutionPanel({ draftId, draftStatus }: ExecutionPanelProps) {
  const { selected: selectedEmail } = useEmailSelection();
  const [executions, setExecutions] = useState<ExecutionRecord[]>([]);
  const [selected, setSelected] = useState<ExecutionDetail | null>(null);
  const [running, setRunning] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState("");
  /** The raw failure, so an approval refusal can be recognised and explained. */
  const [lastRefusal, setLastRefusal] = useState<unknown>(null);

  const approved = draftStatus === "approved";

  const loadHistory = useCallback((): Promise<void> => {
    if (!draftId) return Promise.resolve();
    return getExecutions({ draft_id: draftId }).then(
      (list) => setExecutions(list.executions),
      (failure: unknown) => setError(errorMessage(failure)),
    );
  }, [draftId]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const run = async () => {
    if (!draftId || running) return;
    setRunning(true);
    setError("");
    setLastRefusal(null);
    try {
      // Point the run at the real message the user chose. When nothing is
      // selected the backend reads the newest message, and says so.
      const detail = await executeWorkflowDraft(
        draftId,
        selectedEmail ? { message_id: selectedEmail.message_id } : {},
      );
      setSelected(detail);
      await loadHistory();
    } catch (failure) {
      setLastRefusal(failure);
      setError(errorMessage(failure));
    } finally {
      setRunning(false);
    }
  };

  const open = async (executionId: string) => {
    setLoadingDetail(true);
    setError("");
    try {
      setSelected(await getExecution(executionId));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoadingDetail(false);
    }
  };

  return (
    <div className="rounded-xl border border-white/[0.07] bg-black/20 p-4">
      <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
        Execution
      </p>

      {!approved && (
        <p className="mt-2 text-xs text-zinc-600">
          {draftStatus === "pending_approval"
            ? "This workflow is not approved yet. Executing it now will be refused by the approval gate — that refusal is the point of the gate."
            : `A '${draftStatus}' workflow cannot be executed.`}
        </p>
      )}

      <div className="mt-2 space-y-2">
        <button
          type="button"
          onClick={() => void run()}
          disabled={running}
          className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-b from-emerald-500 to-emerald-700 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-emerald-600/20 transition-all hover:from-emerald-400 hover:to-emerald-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {running ? "Executing…" : "Execute Workflow"}
        </button>
        <p className="text-xs text-zinc-600">
          Runs the approved draft through the allowlisted action registry.
          Steps that need an external service call its real API and fail loudly
          if it is unavailable.
        </p>
        <p className="mt-1 text-[11px] text-zinc-500">
          {selectedEmail
            ? `${selectedEmail.is_self_sent ? "Sent by this account" : "Incoming email from"}: ${selectedEmail.is_self_sent ? selectedEmail.sender : selectedEmail.sender} — ${selectedEmail.subject || "(no subject)"} (${selectedEmail.message_id})`
            : "No email selected: step 1 will read the newest Gmail message."}
        </p>
      </div>

      {error && isApprovalRefusal(lastRefusal) ? (
        <div className="mt-2 rounded-lg border border-brand-400/25 bg-brand-500/10 px-3 py-2.5 text-xs leading-relaxed text-brand-200">
          <p className="font-medium">Approval gate &mdash; run refused (by design)</p>
          <p className="mt-1 text-brand-200/80">
            This workflow has not been approved yet, so nothing was executed
            and no step ran. Approval is required before any workflow can send
            email or call an external service.
          </p>
        </div>
      ) : null}
      {error && !isApprovalRefusal(lastRefusal) && (
        <div className="mt-2 rounded-lg border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </div>
      )}

      {executions.length > 0 && (
        <div className="mt-4 space-y-2">
          <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
            History ({executions.length})
          </p>
          {executions.map((record) => (
            <button
              key={record.id}
              type="button"
              onClick={() => void open(record.id)}
              className="block w-full text-left"
            >
              <ExecutionSummary record={record} />
            </button>
          ))}
        </div>
      )}

      {loadingDetail && (
        <p className="mt-3 text-xs text-zinc-500">Loading execution…</p>
      )}

      {selected && (
        <div className="mt-3">
          <ExecutionView execution={selected} />
        </div>
      )}
    </div>
  );
}
