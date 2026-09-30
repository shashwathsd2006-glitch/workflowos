import type { ReactNode } from "react";

interface StatCardProps {
  label: string;
  value: string;
  sub?: string;
  icon?: ReactNode;
}

export function StatCard({ label, value, sub, icon }: StatCardProps) {
  return (
    <div className="rounded-2xl border border-white/[0.07] bg-[#0c0c11] p-5">
      <div className="flex items-center gap-2 text-zinc-500">
        {icon}
        <p className="text-xs font-medium uppercase tracking-[0.16em]">{label}</p>
      </div>
      <p className="mt-3 truncate text-2xl font-semibold tracking-tight text-white">
        {value}
      </p>
      {sub && <p className="mt-1 truncate text-xs text-zinc-500">{sub}</p>}
    </div>
  );
}
