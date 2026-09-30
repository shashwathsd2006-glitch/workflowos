"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, clearActivity, getActivityStats, listActivity } from "@/lib/api";
import { StatCard } from "@/components/stat-card";
import { IconActivity } from "@/components/icons";
import type {
  ActivityCategory,
  ActivityEvent,
  ActivityStats,
} from "@/types/api";

const APPLICATION_FILTERS = [
  "All applications",
  "Gmail",
  "Chrome",
  "CRM",
  "Slack",
  "Finder",
  "Calendar",
  "Excel",
];

const CATEGORY_FILTERS: { label: string; value: string }[] = [
  { label: "All categories", value: "" },
  { label: "Communication", value: "communication" },
  { label: "CRM", value: "crm" },
  { label: "File", value: "file" },
  { label: "Browser", value: "browser" },
  { label: "Application", value: "application" },
  { label: "UI", value: "ui" },
  { label: "System", value: "system" },
];

const CATEGORY_STYLES: Record<ActivityCategory, string> = {
  application: "border-brand-400/25 bg-brand-500/10 text-brand-300",
  browser: "border-cyan-400/25 bg-cyan-500/10 text-cyan-300",
  file: "border-amber-400/25 bg-amber-500/10 text-amber-300",
  communication: "border-sky-400/25 bg-sky-500/10 text-sky-300",
  crm: "border-violet-400/25 bg-violet-500/10 text-violet-300",
  ui: "border-zinc-400/25 bg-zinc-500/10 text-zinc-300",
  system: "border-emerald-400/25 bg-emerald-500/10 text-emerald-300",
};

const POLL_INTERVAL_MS = 2500;

function formatTime(value: string): string {
  return new Date(value).toLocaleTimeString([], { hour12: false });
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour12: false })}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status === 0
      ? error.message
      : `Backend request failed (HTTP ${error.status})`;
  }
  return error instanceof Error ? error.message : "Something went wrong";
}

export function ActivityDashboard() {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [stats, setStats] = useState<ActivityStats | null>(null);
  const [total, setTotal] = useState(0);
  const [application, setApplication] = useState("");
  const [category, setCategory] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState<"one" | "three" | "clear" | null>(null);

  const load = useCallback((): Promise<void> => {
    const listRequest = listActivity({
      limit: 100,
      application: application || undefined,
      category: category || undefined,
    });
    const statsRequest = getActivityStats();

    return Promise.all([listRequest, statsRequest]).then(
      ([list, nextStats]) => {
        setEvents(list.events);
        setTotal(list.total);
        setStats(nextStats);
        setError(null);
      },
      (failure: unknown) => setError(errorMessage(failure)),
    );
  }, [application, category]);

  useEffect(() => {
    void load();
    const interval = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [load]);

  const clear = async () => {
    if (!window.confirm("Clear all stored activity events?")) return;
    setPending("clear");
    setNotice(null);
    try {
      const response = await clearActivity();
      setNotice(`Cleared ${response.cleared} events`);
      await load();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setPending(null);
    }
  };

  const applications = stats?.applications ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-white">
            Activity
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Real events recorded by workflow executions in this session,
            refreshed every {POLL_INTERVAL_MS / 1000}s.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void clear()}
            disabled={pending !== null}
            className="rounded-xl border border-white/[0.07] px-3 py-2.5 text-sm text-zinc-500 transition-colors hover:border-rose-400/30 hover:text-rose-300 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {pending === "clear" ? "Clearing…" : "Clear"}
          </button>
        </div>
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
          label="Total Events"
          value={stats ? String(stats.total_events) : "—"}
          sub={stats ? `${stats.total_today} today` : "loading"}
        />
        <StatCard
          label="Applications Used"
          value={stats ? String(stats.application_count) : "—"}
          sub={
            applications.length
              ? applications.join(", ")
              : "no applications yet"
          }
        />
        <StatCard
          label="Current Session"
          value={stats?.current_session ?? "—"}
          sub={
            stats
              ? `${stats.session_event_count} events in session`
              : "loading"
          }
        />
        <StatCard
          label="Last Activity"
          value={stats?.last_activity ? formatTime(stats.last_activity) : "—"}
          sub={stats?.last_activity ? formatDateTime(stats.last_activity) : "no activity yet"}
        />
      </section>

      <section className="flex flex-wrap items-center gap-3 rounded-2xl border border-white/[0.07] bg-[#0c0c11] px-4 py-3">
        <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-zinc-600">
          Filters
        </span>

        <select
          value={application}
          onChange={(event) => setApplication(event.target.value)}
          className="rounded-lg border border-white/[0.1] bg-[#101016] px-3 py-2 text-sm text-zinc-200 outline-none transition-colors hover:border-white/[0.2] focus:border-brand-400/50"
        >
          {APPLICATION_FILTERS.map((name) => (
            <option key={name} value={name === "All applications" ? "" : name}>
              {name}
            </option>
          ))}
        </select>

        <select
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          className="rounded-lg border border-white/[0.1] bg-[#101016] px-3 py-2 text-sm text-zinc-200 outline-none transition-colors hover:border-white/[0.2] focus:border-brand-400/50"
        >
          {CATEGORY_FILTERS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>

        <span className="ml-auto font-mono text-xs text-zinc-600">
          {events.length} shown / {total} matching
        </span>
      </section>

      <section className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5 sm:p-6">
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-zinc-500">
            Activity Timeline
          </h2>
          <span className="flex items-center gap-1.5 font-mono text-[11px] text-zinc-600">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-400" />
            live
          </span>
        </div>

        {events.length === 0 ? (
          <div className="rounded-xl border border-dashed border-white/[0.12] px-4 py-10 text-center">
            <IconActivity className="mx-auto h-6 w-6 text-zinc-600" />
            <p className="mt-3 text-sm text-zinc-400">
              {error
                ? "Activity could not be loaded."
                : "No activity matches the current filters."}
            </p>
            {!error && (
              <p className="mt-1 text-xs text-zinc-600">
                Approve and run a workflow to record real execution events.
              </p>
            )}
          </div>
        ) : (
          <ol className="space-y-0">
            {events.map((event, index) => (
              <li key={event.id} className="flex gap-4">
                <div className="flex flex-col items-center">
                  <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-brand-400 ring-4 ring-brand-500/15" />
                  {index < events.length - 1 && (
                    <span className="w-px flex-1 bg-white/[0.08]" />
                  )}
                </div>

                <div
                  className={`min-w-0 flex-1 ${index < events.length - 1 ? "pb-6" : "pb-1"}`}
                >
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <time
                      className="font-mono text-xs text-zinc-500"
                      dateTime={event.timestamp}
                    >
                      {formatTime(event.timestamp)}
                    </time>
                    <span className="text-sm font-medium text-white">
                      {event.application}
                    </span>
                    <span
                      className={`rounded-md border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider ${CATEGORY_STYLES[event.category]}`}
                    >
                      {event.category}
                    </span>
                    <code className="font-mono text-[11px] text-zinc-600">
                      {event.action}
                    </code>
                  </div>
                  <p className="mt-1 text-sm text-zinc-400">
                    {event.description}
                  </p>
                  <p className="mt-0.5 font-mono text-[11px] text-zinc-700">
                    {event.id} · {event.session_id}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
