"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { apiJson } from "./api";
import type { Endpoint } from "./types";

/** The signed-in user's attached endpoints, and a way to reload them after a change. */
export function useEndpoints() {
  const { user } = useAuth();
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const userId = user?.id;

  const reload = useCallback(async () => {
    if (!userId) return setEndpoints([]); // signed out: the API would answer 401
    try {
      setEndpoints(await apiJson<Endpoint[]>("/endpoints"));
    } catch {
      // backend unreachable: leave the list as it is rather than block the page
    }
  }, [userId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount and when the account changes
    reload();
  }, [reload]);

  return { endpoints, reload };
}
