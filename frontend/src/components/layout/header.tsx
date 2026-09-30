"use client";

import { usePathname } from "next/navigation";
import { BackendStatus } from "@/components/backend-status";
import { IconMenu } from "@/components/icons";
import { findNavItem } from "@/lib/nav";

interface HeaderProps {
  onMenuClick: () => void;
}

export function Header({ onMenuClick }: HeaderProps) {
  const pathname = usePathname();
  const page = findNavItem(pathname);

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-4 border-b border-white/[0.06] bg-[#08080b]/85 px-4 backdrop-blur-md sm:px-6">
      <button
        type="button"
        onClick={onMenuClick}
        aria-label="Open navigation"
        className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[0.08] text-zinc-400 transition-colors hover:text-zinc-200 md:hidden"
      >
        <IconMenu className="h-5 w-5" />
      </button>

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold tracking-tight text-white">
          {page.label}
        </p>
        <p className="truncate text-xs text-zinc-500">{page.description}</p>
      </div>

      <div className="flex items-center gap-3">
        <span className="hidden rounded-md border border-white/[0.07] px-2.5 py-1 font-mono text-[11px] text-zinc-500 lg:inline">
          observe → automate
        </span>
        <BackendStatus variant="pill" />
      </div>
    </header>
  );
}
