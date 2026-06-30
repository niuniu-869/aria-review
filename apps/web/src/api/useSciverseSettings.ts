import { useCallback, useSyncExternalStore } from "react";

export interface SciverseSettings {
  apiToken: string;
  baseUrl: string;
}

export const DEFAULT_SCIVERSE_SETTINGS: SciverseSettings = {
  apiToken: "",
  baseUrl: "https://api.sciverse.space",
};

const STORAGE_KEY = "bibliocn.sciverse";

let cachedSettings: SciverseSettings = DEFAULT_SCIVERSE_SETTINGS;
let cachedRaw: string | null = null;

function readStorage(): SciverseSettings {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch {
    raw = null;
  }
  if (raw === cachedRaw) return cachedSettings;

  cachedRaw = raw;
  if (!raw) {
    cachedSettings = DEFAULT_SCIVERSE_SETTINGS;
    return cachedSettings;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<SciverseSettings>;
    cachedSettings = {
      apiToken: parsed.apiToken ?? "",
      baseUrl: parsed.baseUrl ?? DEFAULT_SCIVERSE_SETTINGS.baseUrl,
    };
  } catch {
    cachedSettings = DEFAULT_SCIVERSE_SETTINGS;
  }
  return cachedSettings;
}

function getServerSnapshot(): SciverseSettings {
  return DEFAULT_SCIVERSE_SETTINGS;
}

const listeners = new Set<() => void>();

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  const handler = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY || e.key === null) cb();
  };
  window.addEventListener("storage", handler);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", handler);
  };
}

function notifyAll() {
  listeners.forEach((cb) => cb());
}

export function useSciverseSettings() {
  const settings = useSyncExternalStore(subscribe, readStorage, getServerSnapshot);

  const save = useCallback((next: SciverseSettings) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // ignore private mode/quota failures
    }
    cachedRaw = undefined as unknown as null;
    notifyAll();
  }, []);

  const clear = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
    cachedRaw = undefined as unknown as null;
    notifyAll();
  }, []);

  return { settings, save, clear };
}
