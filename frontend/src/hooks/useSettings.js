import { useCallback, useState } from "react";
import { api } from "../api.js";

export function useSettings() {
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSnapshot(await api.get("/api/settings"));
    } finally {
      setLoading(false);
    }
  }, []);

  const save = useCallback(async (changes) => {
    const res = await api.postJson("/api/settings", changes);
    setSnapshot((s) =>
      s ? { ...s, values: { ...s.values, ...changes } } : s
    );
    return res;
  }, []);

  return { snapshot, loading, load, save };
}
