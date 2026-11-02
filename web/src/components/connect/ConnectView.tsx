"use client";

import { useEndpoints } from "@/lib/useEndpoints";
import { useAuth } from "../AuthProvider";
import { Card } from "../ui";
import EndpointPanel from "./EndpointPanel";
import LogPanel from "./LogPanel";
import TestSetPanel from "./TestSetPanel";

/** The three inputs the report is built from: a model endpoint, a test set, a training log. */
export default function ConnectView() {
  const { user, ready } = useAuth();
  const { endpoints, reload } = useEndpoints();

  return (
    <>
      {ready && !user && (
        <p className="mb-6 rounded-lg bg-teal-soft px-4 py-3 text-sm text-teal">
          Sign in (top right) to attach a model — your endpoints, test sets and logs are private to your account.
        </p>
      )}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <EndpointPanel endpoints={endpoints} reload={reload} />
        </Card>
        <Card>
          <TestSetPanel endpoints={endpoints} />
        </Card>
        <Card className="lg:col-span-2">
          <LogPanel />
        </Card>
      </div>
    </>
  );
}
