"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getDiscoveredWorkflows } from "@/lib/api";
import { StatCard } from "@/components/stat-card";
import { IconWorkflow } from "@/components/icons";
import type { WorkflowCandidate } from "@/types/api";

export function WorkflowStatsPanel() {
  const [workflows, setWorkflows] = useState<WorkflowCandidate[]>([]);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback((): Promise<void> => {
    return getDiscoveredWorkflows().then(
      (response) => {
        setWorkflows(response.workflows);
        setFailed(false);
        setLoaded(true);
      },
      () => {
        setFailed(true);
        setLoaded(true);
      },
    );
  }, []);

  useEffect(() => {
    void load();
    const interval = setInterval(() => void load(), 5000);
    return () => clearInterval(interval);
  }, [load]);

  if (failed) {
    return (
      <div className="rounded-2xl border border-rose-400/25 bg-rose-500/10 px-5 py-4 text-sm text-rose-300">
        Could not reach the backend for discovered workflows.
      </div>
    );
  }

  const totalSequences = workflows.reduce(
    (sum, workflow) => sum + workflow.occurrence_count,
    0,
  );
  const mostFrequent = workflows.length > 0 ? workflows[0] : null;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <StatCard
          label="Workflows Detected"
          value={loaded ? String(workflows.length) : "—"}
          sub={
            workflows.length
              ? "repeated sequences found"
              : "No repeated workflows detected yet."
          }
          icon={<IconWorkflow className="h-4 w-4" />}
        />
        <StatCard
          label="Total Repeated Sequences"
          value={loaded ? String(totalSequences) : "—"}
          sub="occurrences across all candidates"
        />
        <StatCard
          label="Most Frequent Workflow"
          value={mostFrequent ? String(mostFrequent.occurrence_count) : "—"}
          sub={mostFrequent ? mostFrequent.name : "none detected"}
        />
      </div>

      <Link
        href="/workflows"
        className="inline-block text-xs text-zinc-500 transition-colors hover:text-brand-300"
      >
        Open workflow discovery →
      </Link>
    </div>
  );
}
