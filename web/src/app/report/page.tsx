import type { Metadata } from "next";
import ReportView from "@/components/report/ReportView";

export const metadata: Metadata = { title: "Model report · Model Hub" };

export default function ReportPage() {
  return <ReportView />;
}
