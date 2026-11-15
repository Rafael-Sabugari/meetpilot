import { useCallback, useEffect, useState } from "react";
import { api, isTauri, toCommandError, type AccountInfo, type Credentials, type UsageDto } from "../lib/api";

export const DEFAULT_SERVER = "http://localhost:8000";
const SERVER_KEY = "meetpilot.server.v1";

export function savedServer(): string {
  try {
    return localStorage.getItem(SERVER_KEY) || DEFAULT_SERVER;
  } catch {
    return DEFAULT_SERVER;
  }
}

export function useAccount() {
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [usage, setUsage] = useState<UsageDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshUsage = useCallback(() => {
    api.usage().then(setUsage).catch(() => setUsage(null));
  }, []);

  useEffect(() => {
    if (!isTauri()) return;
    api.account().then((a) => {
      setAccount(a);
      if (a) refreshUsage();
    });
  }, [refreshUsage]);

  const signIn = async (c: Credentials, create: boolean): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      const a = await (create ? api.register(c) : api.login(c));
      try {
        localStorage.setItem(SERVER_KEY, c.server);
      } catch {
        /* ignore */
      }
      setAccount(a);
      refreshUsage();
      return true;
    } catch (e) {
      setError(toCommandError(e).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    await api.logout().catch(() => undefined);
    setAccount(null);
    setUsage(null);
  };

  return { account, usage, error, busy, signIn, signOut, refreshUsage };
}
