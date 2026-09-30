"use client";

import type { ReactElement } from "react";
import { usePathname } from "next/navigation";
import { NAV_ITEMS } from "@/lib/nav";
import {
  IconActivity,
  IconAutomation,
  IconAnalytics,
  IconLogo,
  IconOverview,
  IconSettings,
  IconWorkflow,
} from "@/components/icons";

const ICONS: Record<string, (props: { className?: string }) => ReactElement> = {
  Overview: IconOverview,
  Activity: IconActivity,
  Workflows: IconWorkflow,
  Automations: IconAutomation,
  Analytics: IconAnalytics,
  Settings: IconSettings,
};

interface SidebarProps {
  mobileOpen: boolean;
  onClose: () => void;
}

export function Sidebar({ mobileOpen, onClose }: SidebarProps) {
  const pathname = usePathname();
  const groups = Array.from(new Set(NAV_ITEMS.map((item) => item.group)));

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <>
      {mobileOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={onClose}
          className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm md:hidden"
        />
      )}

      <aside
        className={[
          "fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-white/[0.06]",
          "bg-[#0a0a0e] transition-transform duration-200 md:static md:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        ].join(" ")}
      >
        <div className="flex h-16 items-center gap-3 border-b border-white/[0.06] px-5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-brand-700 text-white shadow-lg shadow-brand-600/25">
            <IconLogo className="h-5 w-5" />
          </span>
          <div className="leading-tight">
            <p className="text-sm font-semibold tracking-tight text-white">
              WorkFlowOS
            </p>
            <p className="text-[11px] text-zinc-500">Automation engine</p>
          </div>
        </div>

        <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-5">
          {groups.map((group) => (
            <div key={group}>
              <p className="px-3 pb-2 text-[10px] font-medium uppercase tracking-[0.18em] text-zinc-600">
                {group}
              </p>
              <ul className="space-y-1">
                {NAV_ITEMS.filter((item) => item.group === group).map((item) => {
                  const Icon = ICONS[item.label];
                  const active = isActive(item.href);
                  return (
                    <li key={item.href}>
                      <a
                        href={item.href}
                        onClick={onClose}
                        className={[
                          "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                          active
                            ? "bg-white/[0.06] text-white"
                            : "text-zinc-400 hover:bg-white/[0.03] hover:text-zinc-200",
                        ].join(" ")}
                      >
                        <span
                          className={[
                            "flex h-7 w-7 items-center justify-center rounded-md transition-colors",
                            active
                              ? "bg-brand-500/15 text-brand-300"
                              : "text-zinc-500 group-hover:text-zinc-300",
                          ].join(" ")}
                        >
                          <Icon className="h-[18px] w-[18px]" />
                        </span>
                        {item.label}
                      </a>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="border-t border-white/[0.06] px-5 py-4">
          <div className="flex items-center justify-between text-[11px] text-zinc-500">
            <span className="rounded-md border border-white/[0.06] bg-white/[0.03] px-2 py-1 font-mono">
              local · mvp
            </span>
            <span>v0.1.0</span>
          </div>
        </div>
      </aside>
    </>
  );
}
