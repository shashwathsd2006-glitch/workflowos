import type { Metadata } from "next";
import { AutomationsDashboard } from "@/components/automations/automations-dashboard";

export const metadata: Metadata = { title: "Automations" };

export default function AutomationsPage() {
  return <AutomationsDashboard />;
}
