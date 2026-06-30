/**
 * useLlmSettings — 读/写 localStorage 的 LLM 配置 hook（M5）
 *
 * 存储键: bibliocn.llm
 * 结构: { provider, apiKey, baseUrl, model }
 *
 * 安全说明:
 *   - key 仅存于浏览器 localStorage，不上传服务器
 *   - 仅通过 X-LLM-* 请求头传递给后端（后端转发到 LLM provider，不落库）
 *   - 不在 console.log / URL 中泄漏 key
 */

import { useCallback, useSyncExternalStore } from "react";

export type LlmProvider = "deepseek" | "openai" | "anthropic";

export interface LlmSettings {
  provider: LlmProvider;
  apiKey: string;
  baseUrl: string;
  model: string;
}

// 每个 provider 的默认模型
export const PROVIDER_DEFAULT_MODELS: Record<LlmProvider, string> = {
  deepseek: "deepseek-chat",
  openai: "gpt-4o-mini",
  anthropic: "claude-3-haiku-20240307",
};

export const PROVIDER_DEFAULT_BASE_URLS: Record<LlmProvider, string> = {
  deepseek: "https://api.deepseek.com/v1",
  openai: "https://api.openai.com/v1",
  anthropic: "",
};

// 默认配置（无 key 时由后端 FakeStreamClient 兜底）
export const DEFAULT_SETTINGS: LlmSettings = {
  provider: "deepseek",
  apiKey: "",
  baseUrl: PROVIDER_DEFAULT_BASE_URLS.deepseek,
  model: PROVIDER_DEFAULT_MODELS.deepseek,
};

const STORAGE_KEY = "bibliocn.llm";

// ---- 稳定快照缓存（避免 useSyncExternalStore getSnapshot 每次返回新对象导致无限渲染）----

let _cachedSettings: LlmSettings = DEFAULT_SETTINGS;
let _cachedRaw: string | null = null; // 上次读到的原始 JSON

function readStorage(): LlmSettings {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch {
    raw = null;
  }
  // 若原始 JSON 未变，返回缓存对象（引用稳定，useSyncExternalStore 不触发重渲染）
  if (raw === _cachedRaw) return _cachedSettings;

  _cachedRaw = raw;
  if (!raw) {
    _cachedSettings = DEFAULT_SETTINGS;
    return _cachedSettings;
  }
  try {
    const parsed = JSON.parse(raw) as Partial<LlmSettings>;
    _cachedSettings = {
      provider: (parsed.provider as LlmProvider) || DEFAULT_SETTINGS.provider,
      apiKey: parsed.apiKey ?? "",
      baseUrl:
        parsed.baseUrl ??
        PROVIDER_DEFAULT_BASE_URLS[parsed.provider as LlmProvider] ??
        DEFAULT_SETTINGS.baseUrl,
      model:
        parsed.model ??
        PROVIDER_DEFAULT_MODELS[parsed.provider as LlmProvider] ??
        DEFAULT_SETTINGS.model,
    };
  } catch {
    _cachedSettings = DEFAULT_SETTINGS;
  }
  return _cachedSettings;
}

// 服务端渲染快照（返回 DEFAULT_SETTINGS，永不触发变更）
function getServerSnapshot(): LlmSettings {
  return DEFAULT_SETTINGS;
}

// ---- 订阅者列表：storage 事件 + 同 tab 手动通知 ----

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

// ---- hook ----

export function useLlmSettings() {
  const settings = useSyncExternalStore(subscribe, readStorage, getServerSnapshot);

  const save = useCallback((next: LlmSettings) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* 隐私模式/容量满时忽略 */
    }
    // 使缓存失效，让下次 readStorage 重新解析
    _cachedRaw = undefined as unknown as null;
    notifyAll();
  }, []);

  const clear = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* 忽略 */
    }
    // 使缓存失效
    _cachedRaw = undefined as unknown as null;
    notifyAll();
  }, []);

  return { settings, save, clear };
}
