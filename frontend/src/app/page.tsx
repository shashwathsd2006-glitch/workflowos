import { BackendStatus } from "@/components/backend-status";
import { IconActivity, IconArrow, IconAutomation, IconDatabase, IconShield } from "@/components/icons";
import { ActivityStatsPanel } from "@/components/overview/activity-stats";
import { WorkflowStatsPanel } from "@/components/overview/workflow-stats";

const PIPELINE = [
  "Observe",
  "Understand",
  "Detect",
  "Generate",
  "Approve",
  "Automate",
  "Learn",
];

const MODULES = [
  {
    name: "Activity Agent",
    phase: "Phase 3",
    description: "Records real application activity as structured events.",
  },
  {
    name: "Discovery Engine",
    phase: "Phase 4",
    description: "Groups events into sequences and detects repetition.",
  },
  {
    name: "AI Understanding",
    phase: "Phase 5",
    description: "Infers intent locally with Ollama and emits workflow JSON.",
  },
  {
    name: "Approval Flow",
    phase: "Phase 5",
    description: "Requires explicit approval before anything runs.",
  },
  {
    name: "Automation Engine",
    phase: "Phase 6",
    description: "Runs approved workflows against real connected services.",
  },
  {
    name: "Learning & Analytics",
    phase: "Phase 7",
    description: "Tracks success, failure and reliability over time.",
  },
];

export default function OverviewPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <section className="relative overflow-hidden rounded-3xl border border-white/[0.07] bg-gradient-to-br from-[#101018] via-[#0c0c11] to-[#0c0c11] p-6 sm:p-10">
        <div
          aria-hidden
          className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-brand-600/20 blur-3xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-32 left-10 h-64 w-64 rounded-full bg-indigo-500/10 blur-3xl"
        />

        <div className="relative">
          <span className="inline-flex items-center gap-2 rounded-full border border-brand-400/25 bg-brand-500/10 px-3 py-1 text-[11px] font-medium text-brand-300">
            <span className="h-1.5 w-1.5 rounded-full bg-brand-400" />
            MVP foundation
          </span>

          <h1 className="mt-5 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
            WorkFlowOS
          </h1>
          <p className="mt-2 max-w-2xl text-base leading-relaxed text-zinc-400">
            AI-powered workflow automation that learns from how you work.
          </p>

          <div className="mt-7 flex flex-wrap items-center gap-2">
            {PIPELINE.map((step, index) => (
              <div key={step} className="flex items-center gap-2">
                <span className="rounded-lg border border-white/[0.07] bg-white/[0.03] px-3 py-1.5 text-xs font-medium text-zinc-300">
                  {step}
                </span>
                {index < PIPELINE.length - 1 && (
                  <IconArrow className="h-3.5 w-3.5 text-zinc-600" />
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      <section>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-zinc-500">
            Activity
          </h2>
          <a
            href="/activity"
            className="text-xs text-zinc-500 transition-colors hover:text-brand-300"
          >
            Open activity feed →
          </a>
        </div>
        <ActivityStatsPanel />
      </section>

      <section>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-zinc-500">
            Workflows
          </h2>
          <a
            href="/workflows"
            className="text-xs text-zinc-500 transition-colors hover:text-brand-300"
          >
            Open workflow discovery →
          </a>
        </div>
        <WorkflowStatsPanel />
      </section>

      <section className="grid gap-5 lg:grid-cols-3">
        <BackendStatus variant="card" />

        <div className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5">
          <div className="flex items-center gap-2 text-zinc-500">
            <IconDatabase className="h-4 w-4" />
            <p className="text-xs font-medium uppercase tracking-[0.16em]">
              Storage
            </p>
          </div>
          <p className="mt-3 text-lg font-semibold text-white">SQLite</p>
          <p className="mt-1 text-xs leading-relaxed text-zinc-500">
            Structured events land in the local <code className="font-mono">activity_events</code>{" "}
            table, with metadata stored as JSON.
          </p>
        </div>

        <div className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5">
          <div className="flex items-center gap-2 text-zinc-500">
            <IconShield className="h-4 w-4" />
            <p className="text-xs font-medium uppercase tracking-[0.16em]">
              Safety
            </p>
          </div>
          <p className="mt-3 text-lg font-semibold text-white">
            Approval required
          </p>
          <p className="mt-1 text-xs leading-relaxed text-zinc-500">
            No workflow executes without explicit user approval, and no real
            integrations are connected yet.
          </p>
        </div>
      </section>

      <section>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-zinc-500">
            Core modules
          </h2>
          <span className="font-mono text-xs text-zinc-600">6 modules</span>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {MODULES.map((module) => (
            <div
              key={module.name}
              className="group rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5 transition-colors hover:border-brand-400/30"
            >
              <div className="flex items-center justify-between">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-white/[0.04] text-zinc-400 transition-colors group-hover:bg-brand-500/15 group-hover:text-brand-300">
                  {module.name.includes("Activity") ? (
                    <IconActivity className="h-4 w-4" />
                  ) : module.name.includes("Automation") ? (
                    <IconAutomation className="h-4 w-4" />
                  ) : (
                    <IconShield className="h-4 w-4" />
                  )}
                </span>
                <span className="rounded-md border border-white/[0.07] px-2 py-0.5 font-mono text-[10px] text-zinc-500">
                  {module.phase}
                </span>
              </div>
              <p className="mt-4 text-sm font-medium text-white">{module.name}</p>
              <p className="mt-1 text-xs leading-relaxed text-zinc-500">
                {module.description}
              </p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
