"use client";

import { useCallback, useEffect, useState } from "react";
import { getActivityStats } from "@/lib/api";
import { StatCard } from "@/components/stat-card";
import { IconActivity } from "@/components/icons";
import type { ActivityStats } from "@/types/api";

function formatTime(value: string): string {
  return new Date(value).toLocaleTimeString([], { hour12: false });
}

export function ActivityStatsPanel() {
  const [stats, setStats] = useState<ActivityStats | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback((): Promise<void> => {
    return getActivityStats().then(
      (next) => {
        setStats(next);
        setFailed(false);
      },
      () => setFailed(true),
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
        Could not reach the backend for activity statistics.
      </div>
    );
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label="Activities Today"
        value={stats ? String(stats.total_today) : "—"}
        sub={stats ? `${stats.total_events} total events` : "loading"}
      />
      <StatCard
        label="Applications Used"
        value={stats ? String(stats.application_count) : "—"}
        sub={
          stats && stats.applications.length
            ? stats.applications.join(", ")
            : "none yet"
        }
      />
      <StatCard
        label="Last Activity"
        value={stats?.last_activity ? formatTime(stats.last_activity) : "—"}
        sub={stats ? (stats.last_activity ? "from activity feed" : "no activity yet") : "loading"}
        icon={<IconActivity className="h-4 w-4" />}
      />
      <StatCard
        label="Current Activity Count"
        value={stats ? String(stats.session_event_count) : "—"}
        sub={stats?.current_session ? `session ${stats.current_session}` : "no active session"}
      />
    </div>
  );
}
