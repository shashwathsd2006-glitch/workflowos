import type { Metadata } from "next";
import { WorkflowDashboard } from "@/components/workflows/workflow-dashboard";

export const metadata: Metadata = { title: "Workflows" };

export default function WorkflowsPage() {
  return <WorkflowDashboard />;
}
