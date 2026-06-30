import { useCallback, useSyncExternalStore } from "react";

export interface ImageSettings {
  apiKey: string;
  baseUrl: string;
  model: string;
  size: string;
}

export const DEFAULT_IMAGE_SETTINGS: ImageSettings = {
  apiKey: "",
  baseUrl: "https://api.openai.com/v1",
  model: "gpt-image-1",
  size: "1024x1024",
};

const STORAGE_KEY = "bibliocn.image";

let cachedSettings: ImageSettings = DEFAULT_IMAGE_SETTINGS;
let cachedRaw: string | null = null;

function readStorage(): ImageSettings {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch {
    raw = null;
  }
  if (raw === cachedRaw) return cachedSettings;

  cachedRaw = raw;
  if (!raw) {
    cachedSettings = DEFAULT_IMAGE_SETTINGS;
    return cachedSettings;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<ImageSettings>;
    cachedSettings = {
      apiKey: parsed.apiKey ?? "",
      baseUrl: parsed.baseUrl ?? DEFAULT_IMAGE_SETTINGS.baseUrl,
      model: parsed.model ?? DEFAULT_IMAGE_SETTINGS.model,
      size: parsed.size ?? DEFAULT_IMAGE_SETTINGS.size,
    };
  } catch {
    cachedSettings = DEFAULT_IMAGE_SETTINGS;
  }
  return cachedSettings;
}

function getServerSnapshot(): ImageSettings {
  return DEFAULT_IMAGE_SETTINGS;
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

export function useImageSettings() {
  const settings = useSyncExternalStore(subscribe, readStorage, getServerSnapshot);

  const save = useCallback((next: ImageSettings) => {
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
