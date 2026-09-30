"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ApiError,
  discoverWorkflows,
  getDiscoveredWorkflows,
  getWorkflowUnderstandings,
  understandWorkflow,
} from "@/lib/api";
import { StatCard } from "@/components/stat-card";
import { IconSpark, IconWorkflow } from "@/components/icons";
import { DraftPanel } from "@/components/workflows/draft-panel";
import {
  EmailPicker,
  EmailSelectionProvider,
} from "@/components/workflows/email-selection";
import type {
  WorkflowCandidate,
  WorkflowStatus,
  WorkflowStep,
  WorkflowUnderstanding,
} from "@/types/api";

const STATUS_STYLES: Record<WorkflowStatus, string> = {
  detected: "border-brand-400/25 bg-brand-500/10 text-brand-300",
  reviewed: "border-sky-400/25 bg-sky-500/10 text-sky-300",
  approved: "border-emerald-400/25 bg-emerald-500/10 text-emerald-300",
  rejected: "border-rose-400/25 bg-rose-500/10 text-rose-300",
};

function humanize(action: string): string {
  return action
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour12: false })}`;
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

interface SequenceNode {
  kind: "application" | "action";
  label: string;
}

function toSequenceNodes(steps: WorkflowStep[]): SequenceNode[] {
  const nodes: SequenceNode[] = [];
  let currentApplication: string | null = null;
  for (const step of steps) {
    if (step.application !== currentApplication) {
      nodes.push({ kind: "application", label: step.application });
      currentApplication = step.application;
    }
    nodes.push({ kind: "action", label: humanize(step.action) });
  }
  return nodes;
}

function WorkflowDashboardContent() {
  const [workflows, setWorkflows] = useState<WorkflowCandidate[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [understandings, setUnderstandings] = useState<
    Record<string, WorkflowUnderstanding>
  >({});
  const [aiLoading, setAiLoading] = useState<Record<string, boolean>>({});
  const [aiErrors, setAiErrors] = useState<Record<string, string>>({});
  const [jsonOpenId, setJsonOpenId] = useState<string | null>(null);

  const load = useCallback((): Promise<void> => {
    return getDiscoveredWorkflows()
      .then(
        (response) => {
          setWorkflows(response.workflows);
          setError(null);
        },
        (failure: unknown) => setError(errorMessage(failure)),
      )
      .then(() =>
        getWorkflowUnderstandings().then(
          (list) => {
            const map: Record<string, WorkflowUnderstanding> = {};
            for (const item of list.understandings) {
              map[item.workflow_candidate_id] = item;
            }
            setUnderstandings(map);
          },
          () => undefined,
        ),
      );
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const understand = async (candidateId: string) => {
    setAiLoading((prev) => ({ ...prev, [candidateId]: true }));
    setAiErrors((prev) => ({ ...prev, [candidateId]: "" }));
    try {
      const record = await understandWorkflow(candidateId);
      setUnderstandings((prev) => ({ ...prev, [candidateId]: record }));
      // Open the detail view so the understanding and the next step
      // (Generate Workflow) are on screen without another click.
      setExpandedId(candidateId);
    } catch (failure) {
      setAiErrors((prev) => ({
        ...prev,
        [candidateId]: errorMessage(failure),
      }));
    } finally {
      setAiLoading((prev) => ({ ...prev, [candidateId]: false }));
    }
  };

  const discover = async () => {
    setDiscovering(true);
    setNotice(null);
    setError(null);
    try {
      const result = await discoverWorkflows();
      setNotice(
        `Analysed ${result.sequences_analyzed} sequences — detected ${result.count} workflow(s) at threshold ${result.threshold}`,
      );
      await load();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setDiscovering(false);
    }
  };

  const totalSequences = workflows.reduce(
    (sum, workflow) => sum + workflow.occurrence_count,
    0,
  );
  const mostFrequent = workflows.length > 0 ? workflows[0] : null;
  const averageConfidence =
    workflows.length > 0
      ? workflows.reduce((sum, workflow) => sum + workflow.confidence, 0) /
        workflows.length
      : 0;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-white">
            Workflow Discovery
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Repetition detection over stored activity events, then optional AI
            understanding and workflow draft generation for human review.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Link
            href="/activity"
            className="rounded-xl border border-white/[0.1] bg-white/[0.04] px-4 py-2.5 text-sm font-medium text-zinc-200 transition-colors hover:border-white/[0.2] hover:bg-white/[0.07]"
          >
            Open Activity
          </Link>
          <button
            type="button"
            onClick={() => void discover()}
            disabled={discovering}
            className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2.5 text-sm font-medium text-white shadow-lg shadow-brand-600/20 transition-all hover:from-brand-400 hover:to-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <IconSpark className="h-4 w-4" />
            {discovering ? "Discovering…" : "Discover Workflows"}
          </button>
        </div>
      </div>

      <EmailPicker />

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
          label="Workflows Detected"
          value={String(workflows.length)}
          sub={
            workflows.length
              ? "repeated sequences found"
              : "No repeated workflows detected yet."
          }
        />
        <StatCard
          label="Total Repeated Sequences"
          value={String(totalSequences)}
          sub="occurrences across all candidates"
        />
        <StatCard
          label="Most Frequent Workflow"
          value={mostFrequent ? String(mostFrequent.occurrence_count) : "—"}
          sub={mostFrequent ? mostFrequent.name : "none detected"}
        />
        <StatCard
          label="Average Confidence"
          value={workflows.length ? percent(averageConfidence) : "—"}
          sub={
            workflows.length
              ? "0.5·similarity + 0.3·repetition + 0.2·consistency"
              : "no candidates"
          }
        />
      </section>

      <section>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-zinc-500">
            Detected Workflows
          </h2>
          <span className="font-mono text-xs text-zinc-600">
            {workflows.length} candidate{workflows.length === 1 ? "" : "s"}
          </span>
        </div>

        {workflows.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/[0.12] bg-[#0c0c11] px-4 py-12 text-center">
            <IconWorkflow className="mx-auto h-6 w-6 text-zinc-600" />
            <p className="mt-3 text-sm text-zinc-400">
              No repeated workflows detected yet.
            </p>
            <p className="mt-1 text-xs text-zinc-600">
              Run an approved workflow to record real activity, then{" "}
              <span className="text-zinc-400">Discover Workflows</span>.
            </p>
            <Link
              href="/activity"
              className="mt-4 inline-block rounded-lg border border-white/[0.1] px-4 py-2 text-xs text-zinc-300 transition-colors hover:border-brand-400/40 hover:text-brand-300"
            >
              Go to Activity →
            </Link>
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {workflows.map((workflow) => {
              const expanded = expandedId === workflow.id;
              const nodes = toSequenceNodes(workflow.sequence);
              const understanding = understandings[workflow.id];
              return (
                <article
                  key={workflow.id}
                  className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5 transition-colors hover:border-brand-400/25"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-base font-semibold text-white">
                        {workflow.name}
                      </p>
                      <p className="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-zinc-500">
                        {workflow.applications.map((application, index) => (
                          <span key={application} className="flex items-center gap-2">
                            {index > 0 && (
                              <span className="text-zinc-700">→</span>
                            )}
                            {application}
                          </span>
                        ))}
                      </p>
                    </div>
                    <span
                      className={`shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${STATUS_STYLES[workflow.status]}`}
                    >
                      {workflow.status}
                    </span>
                  </div>

                  <p className="mt-3 text-sm text-zinc-400">
                    Detected{" "}
                    <span className="font-medium text-white">
                      {workflow.occurrence_count} times
                    </span>{" "}
                    · {workflow.session_ids.length} session
                    {workflow.session_ids.length === 1 ? "" : "s"}
                  </p>

                  <div className="mt-4 grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                        Similarity
                      </p>
                      <p className="mt-1 font-mono text-lg font-semibold text-white">
                        {percent(workflow.similarity_score)}
                      </p>
                      <div className="mt-1.5 h-1 rounded-full bg-white/[0.06]">
                        <div
                          className="h-1 rounded-full bg-brand-500"
                          style={{
                            width: `${Math.round(workflow.similarity_score * 100)}%`,
                          }}
                        />
                      </div>
                    </div>
                    <div>
                      <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                        Confidence
                      </p>
                      <p className="mt-1 font-mono text-lg font-semibold text-white">
                        {percent(workflow.confidence)}
                      </p>
                      <div className="mt-1.5 h-1 rounded-full bg-white/[0.06]">
                        <div
                          className="h-1 rounded-full bg-emerald-500"
                          style={{
                            width: `${Math.round(workflow.confidence * 100)}%`,
                          }}
                        />
                      </div>
                    </div>
                  </div>

                  <div className="mt-4 flex flex-wrap gap-1.5">
                    {workflow.applications.map((application) => (
                      <span
                        key={application}
                        className="rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 text-[11px] text-zinc-400"
                      >
                        {application}
                      </span>
                    ))}
                  </div>

                  {aiErrors[workflow.id] && (
                    <div className="mt-4 rounded-xl border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                      {aiErrors[workflow.id]}
                      <button
                        type="button"
                        onClick={() => void understand(workflow.id)}
                        className="ml-2 underline transition-colors hover:text-rose-200"
                      >
                        Try again
                      </button>
                    </div>
                  )}

                  {!understanding && !aiLoading[workflow.id] && (
                    <div className="mt-4">
                      <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                        Step 1 &middot; AI understanding
                      </p>
                      <button
                        type="button"
                        onClick={() => void understand(workflow.id)}
                        className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-brand-500 to-brand-700 px-4 py-2.5 text-sm font-medium text-white shadow-lg shadow-brand-600/20 transition-all hover:from-brand-400 hover:to-brand-600"
                      >
                        <IconSpark className="h-4 w-4" />
                        Understand with AI
                      </button>
                      <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-600">
                        Runs a real local Ollama call to turn this detected
                        pattern into intent, steps and a suggested automation.
                        Nothing is executed.
                      </p>
                    </div>
                  )}

                  {!understanding && aiLoading[workflow.id] && (
                    <p className="mt-4 flex items-center gap-2 text-sm text-zinc-400">
                      <IconSpark className="h-4 w-4 animate-pulse text-brand-400" />
                      Understanding with local AI&hellip;
                    </p>
                  )}

                  <button
                    type="button"
                    onClick={() => setExpandedId(expanded ? null : workflow.id)}
                    className="mt-4 w-full rounded-xl border border-white/[0.1] bg-white/[0.03] px-4 py-2 text-sm font-medium text-zinc-300 transition-colors hover:border-brand-400/40 hover:text-brand-200"
                  >
                    {expanded ? "Hide Details" : "View Details"}
                  </button>

                  {expanded && (
                    <div className="mt-4 space-y-4 rounded-xl border border-white/[0.07] bg-black/20 p-4">
                      <div className="grid gap-3 sm:grid-cols-2">
                        <div>
                          <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                            Occurrences
                          </p>
                          <p className="mt-0.5 text-sm text-zinc-300">
                            {workflow.occurrence_count}
                          </p>
                        </div>
                        <div>
                          <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                            Confidence
                          </p>
                          <p className="mt-0.5 text-sm text-zinc-300">
                            {workflow.confidence.toFixed(3)} (
                            {workflow.confidence_label})
                          </p>
                        </div>
                        <div>
                          <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                            First seen
                          </p>
                          <p className="mt-0.5 font-mono text-xs text-zinc-400">
                            {formatDateTime(workflow.first_seen)}
                          </p>
                        </div>
                        <div>
                          <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                            Last seen
                          </p>
                          <p className="mt-0.5 font-mono text-xs text-zinc-400">
                            {formatDateTime(workflow.last_seen)}
                          </p>
                        </div>
                      </div>

                      <div>
                        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                          Sessions
                        </p>
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {workflow.session_ids.map((session) => (
                            <span
                              key={session}
                              className="rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 font-mono text-[11px] text-zinc-400"
                            >
                              {session}
                            </span>
                          ))}
                        </div>
                      </div>

                      <div>
                        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                          Sequence
                        </p>
                        <ol className="mt-2 space-y-1">
                          {nodes.map((node, index) => (
                            <li key={`${node.kind}-${node.label}-${index}`}>
                              {node.kind === "application" ? (
                                <span className="inline-block rounded-lg border border-white/[0.08] bg-white/[0.05] px-3 py-1.5 text-sm font-medium text-white">
                                  {node.label}
                                </span>
                              ) : (
                                <span className="block pl-4 text-sm text-zinc-400">
                                  {node.label}
                                </span>
                              )}
                              {index < nodes.length - 1 && (
                                <span className="block pl-2 text-xs text-zinc-700">
                                  ↓
                                </span>
                              )}
                            </li>
                          ))}
                        </ol>
                      </div>

                      <div>
                        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
                          AI Understanding
                        </p>

                        {aiErrors[workflow.id] && (
                          <div className="mt-2 rounded-xl border border-rose-400/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                            {aiErrors[workflow.id]}
                            <button
                              type="button"
                              onClick={() => void understand(workflow.id)}
                              className="ml-2 underline transition-colors hover:text-rose-200"
                            >
                              Try again
                            </button>
                          </div>
                        )}

                        {!understanding && !aiLoading[workflow.id] && (
                          <p className="mt-2 text-xs text-zinc-600">
                            Use &ldquo;Understand with AI&rdquo; above to analyse
                            this candidate.
                          </p>
                        )}

                        {!understanding && aiLoading[workflow.id] && (
                          <p className="mt-2 flex items-center gap-2 text-sm text-zinc-400">
                            <IconSpark className="h-4 w-4 animate-pulse text-brand-400" />
                            Understanding…
                          </p>
                        )}

                        {understanding && (
                          <UnderstandingView
                            understanding={understanding}
                            busy={Boolean(aiLoading[workflow.id])}
                            showJson={jsonOpenId === understanding.id}
                            onToggleJson={() =>
                              setJsonOpenId(
                                jsonOpenId === understanding.id
                                  ? null
                                  : understanding.id,
                              )
                            }
                            onRerun={() => void understand(workflow.id)}
                          />
                        )}
                      </div>

                      <div>
                        <DraftPanel
                          candidateId={workflow.id}
                          understandingReady={Boolean(understanding)}
                        />
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
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

interface UnderstandingViewProps {
  understanding: WorkflowUnderstanding;
  busy: boolean;
  showJson: boolean;
  onToggleJson: () => void;
  onRerun: () => void;
}

function UnderstandingView({
  understanding,
  busy,
  showJson,
  onToggleJson,
  onRerun,
}: UnderstandingViewProps) {
  const view = understanding;

  return (
    <div className="mt-3 space-y-4 rounded-xl border border-brand-400/20 bg-brand-500/[0.04] p-4">
      <div>
        <p className="text-base font-semibold text-white">{view.workflow_name}</p>
        <p className="mt-0.5 text-sm text-brand-300">{view.intent}</p>
      </div>

      <div>
        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
          Description
        </p>
        <p className="mt-1 text-sm leading-relaxed text-zinc-300">
          {view.description}
        </p>
      </div>

      <div>
        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
          Trigger
        </p>
        <p className="mt-1 text-sm text-zinc-300">{view.trigger}</p>
      </div>

      <div>
        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
          Steps
        </p>
        <ol className="mt-2 space-y-2">
          {view.steps.map((step) => (
            <li
              key={`${step.order}-${step.action}`}
              className="rounded-lg border border-white/[0.07] bg-black/25 px-3 py-2"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm text-white">
                  <span className="mr-2 font-mono text-xs text-zinc-500">
                    {step.order}.
                  </span>
                  {step.application} → {step.action}
                </span>
                {step.category && (
                  <span className="rounded-md border border-white/[0.08] bg-white/[0.03] px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-zinc-500">
                    {step.category}
                  </span>
                )}
              </div>
              {step.purpose && (
                <p className="mt-1 text-xs text-zinc-500">{step.purpose}</p>
              )}
            </li>
          ))}
        </ol>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <ListBlock label="Applications" items={view.applications} />
        <ListBlock label="Categories" items={view.categories} />
        <ListBlock label="Inputs" items={view.inputs} />
        <ListBlock label="Outputs" items={view.outputs} />
        <ListBlock label="Dependencies" items={view.dependencies} />
        <ListBlock label="Assumptions" items={view.assumptions} />
      </div>

      <div>
        <div className="flex items-center justify-between">
          <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
            Confidence
          </p>
          <p className="font-mono text-sm font-semibold text-white">
            {percent(view.confidence)}
          </p>
        </div>
        <div className="mt-1.5 h-1 rounded-full bg-white/[0.06]">
          <div
            className="h-1 rounded-full bg-brand-500"
            style={{ width: `${Math.round(view.confidence * 100)}%` }}
          />
        </div>
      </div>

      <div>
        <p className="text-[11px] uppercase tracking-[0.16em] text-zinc-600">
          Suggested Automation
        </p>
        <p className="mt-1 text-sm leading-relaxed text-zinc-300">
          {view.suggested_automation}
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.07] pt-3">
        <p className="font-mono text-[11px] text-zinc-600">
          {view.provider} · {view.model} · {formatDateTime(view.updated_at)}
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onRerun}
            disabled={busy}
            className="rounded-lg border border-white/[0.1] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:border-brand-400/40 hover:text-brand-200 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? "Understanding…" : "Re-run"}
          </button>
          <button
            type="button"
            onClick={onToggleJson}
            className="rounded-lg border border-white/[0.1] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:border-white/[0.25] hover:text-zinc-100"
          >
            {showJson ? "Hide JSON" : "View JSON"}
          </button>
        </div>
      </div>

      {showJson && (
        <pre className="max-h-64 overflow-auto rounded-lg border border-white/[0.07] bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-zinc-400">
          {JSON.stringify(view, null, 2)}
        </pre>
      )}
    </div>
  );
}

/**
 * Wraps the discovery screen in the email-selection provider so the chosen
 * Gmail message is available to the execution panel further down the tree.
 */
export function WorkflowDashboard() {
  return (
    <EmailSelectionProvider>
      <WorkflowDashboardContent />
    </EmailSelectionProvider>
  );
}
