export interface NavItem {
  label: string;
  href: string;
  group: string;
  description: string;
}

export const NAV_ITEMS: NavItem[] = [
  {
    label: "Overview",
    href: "/",
    group: "Monitor",
    description: "System snapshot and pipeline status",
  },
  {
    label: "Activity",
    href: "/activity",
    group: "Monitor",
    description: "Observed and simulated activity events",
  },
  {
    label: "Workflows",
    href: "/workflows",
    group: "Build",
    description: "Discovered and generated workflows",
  },
  {
    label: "Automations",
    href: "/automations",
    group: "Build",
    description: "Approved workflows bound to a trigger",
  },
  {
    label: "Analytics",
    href: "/analytics",
    group: "Insight",
    description: "Reliability metrics from real executions",
  },
  {
    label: "Settings",
    href: "/settings",
    group: "System",
    description: "Configuration, health and safety",
  },
];

export function findNavItem(pathname: string): NavItem {
  if (pathname === "/") return NAV_ITEMS[0];

  const match = NAV_ITEMS.filter((item) => item.href !== "/").find((item) =>
    pathname.startsWith(item.href),
  );

  return match ?? NAV_ITEMS[0];
}
