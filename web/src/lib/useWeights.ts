"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { apiJson } from "./api";
import type { Weights } from "./types";

/** The signed-in user's uploaded model files, and a way to reload them after a change. */
export function useWeights() {
  const { user } = useAuth();
  const [weights, setWeights] = useState<Weights[]>([]);
  const userId = user?.id;

  const reload = useCallback(async () => {
    if (!userId) return setWeights([]); // signed out: the API would answer 401
    try {
      setWeights(await apiJson<Weights[]>("/weights"));
    } catch {
      // backend unreachable: leave the list as it is
    }
  }, [userId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount and when the account changes
    reload();
  }, [reload]);

  return { weights, reload };
}
