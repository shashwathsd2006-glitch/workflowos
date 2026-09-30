"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, getHealth } from "@/lib/api";
import type { ConnectionState, HealthResponse } from "@/types/api";

interface BackendStatusProps {
  variant?: "pill" | "card";
}

interface StatusState {
  state: ConnectionState;
  detail: string | null;
  lastChecked: Date | null;
}

function toStatusState(health: HealthResponse): StatusState {
  return {
    state: health.status === "ok" ? "connected" : "disconnected",
    detail: health.service,
    lastChecked: new Date(),
  };
}

function toErrorState(error: unknown): StatusState {
  return {
    state: "disconnected",
    detail:
      error instanceof ApiError
        ? error.status === 0
          ? error.message
          : `HTTP ${error.status}`
        : "Unknown error",
    lastChecked: new Date(),
  };
}

export function BackendStatus({ variant = "pill" }: BackendStatusProps) {
  const [status, setStatus] = useState<StatusState>({
    state: "checking",
    detail: null,
    lastChecked: null,
  });

  const runCheck = useCallback(() => {
    getHealth().then(
      (health) => setStatus(toStatusState(health)),
      (error: unknown) => setStatus(toErrorState(error)),
    );
  }, []);

  const retry = useCallback(() => {
    setStatus((prev) => ({ ...prev, state: "checking" }));
    runCheck();
  }, [runCheck]);

  useEffect(() => {
    runCheck();
    const interval = setInterval(runCheck, 20_000);
    return () => clearInterval(interval);
  }, [runCheck]);

  if (variant === "pill") {
    return <StatusPill status={status} onRetry={retry} />;
  }

  return <StatusCard status={status} onRetry={retry} />;
}

function statusMeta(state: ConnectionState) {
  switch (state) {
    case "connected":
      return {
        label: "Connected",
        dot: "bg-emerald-400",
        ring: "ring-emerald-400/25",
        text: "text-emerald-300",
      };
    case "checking":
      return {
        label: "Checking…",
        dot: "bg-amber-400 animate-pulse",
        ring: "ring-amber-400/25",
        text: "text-amber-300",
      };
    default:
      return {
        label: "Disconnected",
        dot: "bg-rose-400",
        ring: "ring-rose-400/25",
        text: "text-rose-300",
      };
  }
}

function StatusPill({
  status,
  onRetry,
}: {
  status: StatusState;
  onRetry: () => void;
}) {
  const meta = statusMeta(status.state);
  return (
    <button
      type="button"
      onClick={onRetry}
      title="Re-check backend health"
      className="flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] py-1.5 pl-3 pr-3 text-xs text-zinc-300 transition-colors hover:border-white/[0.16] hover:bg-white/[0.06]"
    >
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot} ring-4 ${meta.ring}`} />
      <span className="hidden sm:inline">Backend</span>
      <span className={meta.text}>{meta.label}</span>
    </button>
  );
}

function StatusCard({
  status,
  onRetry,
}: {
  status: StatusState;
  onRetry: () => void;
}) {
  const meta = statusMeta(status.state);
  return (
    <div className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-zinc-500">
            Backend Status
          </p>
          <div className="mt-3 flex items-center gap-3">
            <span className={`h-2.5 w-2.5 rounded-full ${meta.dot} ring-4 ${meta.ring}`} />
            <span className={`text-lg font-semibold ${meta.text}`}>{meta.label}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="rounded-lg border border-white/[0.08] px-3 py-1.5 text-xs text-zinc-400 transition-colors hover:border-white/[0.18] hover:text-zinc-200"
        >
          Re-check
        </button>
      </div>

      <dl className="mt-5 space-y-2 border-t border-white/[0.06] pt-4 text-xs">
        <div className="flex items-center justify-between">
          <dt className="text-zinc-500">Endpoint</dt>
          <dd className="font-mono text-zinc-300">GET /health</dd>
        </div>
        <div className="flex items-center justify-between gap-4">
          <dt className="text-zinc-500">Detail</dt>
          <dd className="truncate font-mono text-zinc-300">
            {status.state === "connected"
              ? (status.detail ?? "ok")
              : (status.detail ?? "—")}
          </dd>
        </div>
        <div className="flex items-center justify-between">
          <dt className="text-zinc-500">Last checked</dt>
          <dd className="font-mono text-zinc-300">
            {status.lastChecked
              ? status.lastChecked.toLocaleTimeString()
              : "never"}
          </dd>
        </div>
      </dl>
    </div>
  );
}
