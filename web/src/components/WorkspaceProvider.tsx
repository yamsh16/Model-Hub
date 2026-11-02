"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { WORKSPACE_KEY } from "@/lib/storage";
import type { Evaluation, TestSet, TrainingLog } from "@/lib/types";
import { useAuth } from "./AuthProvider";

// What the user has loaded so far. The connect page fills it in, the report page reads it.
// It lives in sessionStorage so a reload keeps it; the server holds the real data and the
// ids here are only pointers to it.
type Data = {
  testset: TestSet | null;
  evaluation: Evaluation | null; // result of running `testset` through an endpoint
  log: TrainingLog | null;
};

const EMPTY: Data = { testset: null, evaluation: null, log: null };

type Workspace = Data & {
  setTestset: (testset: TestSet | null) => void;
  setEvaluation: (evaluation: Evaluation | null) => void;
  setLog: (log: TrainingLog | null) => void;
};

const WorkspaceContext = createContext<Workspace | null>(null);

export function useWorkspace(): Workspace {
  const ws = useContext(WorkspaceContext);
  if (!ws) throw new Error("useWorkspace must be used inside <WorkspaceProvider>");
  return ws;
}

export default function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [data, setData] = useState<Data>(EMPTY);
  const loaded = useRef(false);
  const lastUserId = useRef<string | null>(null);

  // Read what a reload would otherwise lose. Done after mount so the server render matches.
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(WORKSPACE_KEY);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time restore from storage
      if (saved) setData({ ...EMPTY, ...JSON.parse(saved) });
    } catch {
      // blocked or corrupt: start empty
    }
    loaded.current = true;
  }, []);

  useEffect(() => {
    if (!loaded.current) return;
    try {
      sessionStorage.setItem(WORKSPACE_KEY, JSON.stringify(data));
    } catch {
      // too big or blocked: the workspace just won't survive a reload
    }
  }, [data]);

  // Leaving an account (or being switched to another) empties the workspace.
  useEffect(() => {
    const id = user?.id ?? null;
    if (lastUserId.current && lastUserId.current !== id) setData(EMPTY);
    lastUserId.current = id;
  }, [user]);

  // A new test set, or a new target column, makes the old evaluation meaningless.
  const setTestset = useCallback(
    (testset: TestSet | null) => setData((d) => ({ ...d, testset, evaluation: null })),
    [],
  );
  const setEvaluation = useCallback((evaluation: Evaluation | null) => setData((d) => ({ ...d, evaluation })), []);
  const setLog = useCallback((log: TrainingLog | null) => setData((d) => ({ ...d, log })), []);

  const value = useMemo(
    () => ({ ...data, setTestset, setEvaluation, setLog }),
    [data, setTestset, setEvaluation, setLog],
  );
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}
