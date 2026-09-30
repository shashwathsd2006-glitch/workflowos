"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  approveWorkflowDraft,
  deleteWorkflowDraft,
  generateWorkflowDraft,
  getWorkflowDrafts,
  rejectWorkflowDraft,
} from "@/lib/api";
import { IconSpark } from "@/components/icons";
import { ExecutionPanel } from "@/components/workflows/execution-panel";
import type {
  WorkflowDraft,
  WorkflowDraftStatus,
  WorkflowTriggerType,
} from "@/types/api";

const STATUS_STYLES: Record<WorkflowDraftStatus, string> = {
  draft: "border-zinc-500/25 bg-zinc-500/10 text-zinc-300",
  pending_approval: "border-amber-400/25 bg-amber-500/10 text-amber-300",
  approved: "border-emerald-400/25 bg-emerald-500/10 text-emerald-300",
  rejected: "border-rose-400/25 bg-rose-500/10 text-rose-300",
};

const STATUS_LABELS: Record<WorkflowDraftStatus, string> = {
  draft: "Draft",
  pending_approval: "Pending Approval",
  approved: "Approved",
  rejected: "Rejected",
};

const TRIGGER_STYLES: Record<WorkflowTriggerType, string> = {
  event: "border-sky-400/25 bg-sky-500/10 text-sky-300",
  schedule: "border-violet-400/25 bg-violet-500/10 text-violet-300",
  manual: "border-zinc-400/25 bg-zinc-500/10 text-zinc-300",
};

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString([], {
    hour12: false,
  })}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.detail) return error.detail;
    return error.status === 0
      ? error.message
      : `Backend request failed (HTTP ${error.status})`;
  }
  return error instanceof Error ? error.message : "Something went wrong";
}

function StatusBadge({ status }: { status: WorkflowDraftStatus }) {
  return (
    <span
      className={`shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${STATUS_STYLES[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

function ListBlock({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
        {label}
      </p>
      {items.length === 0 ? (
        <p className="mt-0.5 text-sm text-zinc-600">—</p>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {items.map((item, index) => (
            <li key={`${item}-${index}`} className="text-sm text-zinc-300">
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DraftBody({ draft }: { draft: WorkflowDraft }) {
  return (
    <div className="mt-3 space-y-4">
      <div>
        <p className="text-base font-semibold text-white">{draft.name}</p>
        <p className="mt-0.5 text-sm leading-relaxed text-zinc-400">
          {draft.description}
        </p>
      </div>

      <div>
        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
          Trigger
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <span
            className={`rounded-md border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${TRIGGER_STYLES[draft.trigger.type]}`}
          >
            {draft.trigger.type}
          </span>
          <span className="text-sm text-zinc-300">
            {draft.trigger.application} → {draft.trigger.action}
          </span>
        </div>
      </div>

      <div>
        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
          Steps
        </p>
        <ol className="mt-2 space-y-2">
          {draft.steps.map((step) => (
            <li
              key={`${step.step_number}-${step.action}`}
              className="rounded-lg border border-white/[0.07] bg-black/25 px-3 py-2"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm text-white">
                  <span className="mr-2 font-mono text-xs text-zinc-500">
                    {step.step_number}.
                  </span>
                  {step.application} → {step.action}
                </span>
              </div>
              {step.purpose && (
                <p className="mt-1 text-xs text-zinc-500">{step.purpose}</p>
              )}
              {(step.input || step.output) && (
                <p className="mt-1 font-mono text-[11px] text-zinc-600">
                  {step.input ? `in: ${step.input}` : ""}
                  {step.input && step.output ? " · " : ""}
                  {step.output ? `out: ${step.output}` : ""}
                </p>
              )}
            </li>
          ))}
        </ol>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <ListBlock label="Applications" items={draft.applications} />
        <ListBlock label="Inputs" items={draft.inputs} />
        <ListBlock label="Outputs" items={draft.outputs} />
        <ListBlock label="Dependencies" items={draft.dependencies} />
        <ListBlock label="Assumptions" items={draft.assumptions} />
        <ListBlock label="Conditions" items={draft.conditions} />
      </div>

      <div>
        <div className="flex items-center justify-between">
          <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
            Confidence
          </p>
          <p className="font-mono text-sm font-semibold text-white">
            {percent(draft.confidence)}
          </p>
        </div>
        <div className="mt-1.5 h-1 rounded-full bg-white/[0.06]">
          <div
            className="h-1 rounded-full bg-brand-500"
            style={{ width: `${Math.round(draft.confidence * 100)}%` }}
          />
        </div>
      </div>

      {draft.rejection_reason && (
        <div className="rounded-lg border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          Rejected: {draft.rejection_reason}
        </div>
      )}

      <p className="font-mono text-[11px] text-zinc-600">
        {draft.generated_by} · {draft.model} · updated{" "}
        {formatDateTime(draft.updated_at)}
        {draft.approved_at
          ? ` · approved ${formatDateTime(draft.approved_at)}`
          : ""}
      </p>

      <p className="rounded-lg border border-white/[0.07] bg-black/20 px-3 py-2 text-[11px] leading-relaxed text-zinc-500">
        This is a proposal. Approving it records your decision — it does not by
        itself run anything. Execution is a separate, explicit action below.
      </p>
    </div>
  );
}

interface DraftPanelProps {
  candidateId: string;
  understandingReady: boolean;
}

export function DraftPanel({ candidateId, understandingReady }: DraftPanelProps) {
  const [draft, setDraft] = useState<WorkflowDraft | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback((): Promise<void> => {
    return getWorkflowDrafts().then(
      (list) => {
        setDraft(
          list.drafts.find((item) => item.workflow_candidate_id === candidateId) ??
            null,
        );
        setError("");
      },
      (failure: unknown) => setError(errorMessage(failure)),
    );
  }, [candidateId]);

  useEffect(() => {
    void load();
  }, [load]);

  const generate = async () => {
    setLoading(true);
    setError("");
    setNotice("");
    try {
      setDraft(await generateWorkflowDraft(candidateId));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  };

  const act = async (
    label: string,
    action: () => Promise<WorkflowDraft>,
  ) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setDraft(await action());
      setNotice(label);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await deleteWorkflowDraft(draft!.id);
      setDraft(null);
      setNotice("Draft deleted");
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  const pending = draft?.status === "pending_approval";

  return (
    <div className="rounded-xl border border-white/[0.07] bg-black/20 p-4">
      <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
        Workflow Draft
      </p>

      {!understandingReady && !draft && (
        <p className="mt-2 text-xs text-zinc-600">
          Understand this candidate with AI first — generation plans from the
          interpreted workflow.
        </p>
      )}

      {understandingReady && !draft && !loading && (
        <div className="mt-2 space-y-2">
          <button
            type="button"
            onClick={() => void generate()}
            className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2 text-sm font-medium text-white shadow-lg shadow-brand-600/20 transition-all hover:from-brand-400 hover:to-brand-600"
          >
            <IconSpark className="h-4 w-4" />
            Generate Workflow
          </button>
          <p className="text-xs text-zinc-600">
            Produces a structured draft for review before anything can be
            executed.
          </p>
        </div>
      )}

      {loading && (
        <p className="mt-2 flex items-center gap-2 text-sm text-zinc-400">
          <IconSpark className="h-4 w-4 animate-pulse text-brand-400" />
          Generating draft…
        </p>
      )}

      {error && (
        <div className="mt-2 rounded-lg border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
          {understandingReady && !draft && (
            <button
              type="button"
              onClick={() => void generate()}
              className="ml-2 underline transition-colors hover:text-rose-200"
            >
              Try again
            </button>
          )}
        </div>
      )}

      {notice && !error && (
        <div className="mt-2 rounded-lg border border-emerald-400/25 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">
          {notice}
        </div>
      )}

      {draft && (
        <>
          <div className="mt-2 flex items-start justify-between gap-3">
            <p className="text-xs text-zinc-600">
              Draft <span className="font-mono text-zinc-400">{draft.id}</span>
            </p>
            <StatusBadge status={draft.status} />
          </div>

          <DraftBody draft={draft} />

          <div className="mt-4 flex flex-wrap gap-2 border-t border-white/[0.07] pt-3">
            {pending && (
              <>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void act("Workflow approved", () =>
                      approveWorkflowDraft(draft.id),
                    )
                  }
                  className="rounded-lg border border-emerald-400/30 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 transition-colors hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {busy ? "Working…" : "Approve Workflow"}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void act("Workflow rejected", () =>
                      rejectWorkflowDraft(draft.id, {
                        reason: "Rejected during review",
                      }),
                    )
                  }
                  className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-1.5 text-xs font-medium text-rose-300 transition-colors hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {busy ? "Working…" : "Reject Workflow"}
                </button>
              </>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => void generate()}
              className="rounded-lg border border-white/[0.1] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:border-brand-400/40 hover:text-brand-200 disabled:cursor-not-allowed disabled:opacity-60"
            >
              Regenerate
            </button>
            {!pending && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void remove()}
                className="rounded-lg border border-white/[0.1] px-3 py-1.5 text-xs text-zinc-400 transition-colors hover:border-rose-400/40 hover:text-rose-300 disabled:cursor-not-allowed disabled:opacity-60"
              >
                Delete Draft
              </button>
            )}
          </div>

          <div className="mt-3 border-t border-white/[0.07] pt-3">
            <ExecutionPanel draftId={draft.id} draftStatus={draft.status} />
          </div>
        </>
      )}
    </div>
  );
}
