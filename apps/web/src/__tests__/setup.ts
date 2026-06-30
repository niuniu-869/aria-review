import "@testing-library/jest-dom";

// jsdom opaque origin 下 localStorage 不完整(removeItem/clear 缺失或抛错),competition
// 组件(ChatPanel/AiToolsPanel)的 restore 会用 localStorage.removeItem 而炸。全局注入
// 完整 mock,供所有测试文件使用(settingsM5 另有自身 mock,相同语义,不冲突)。
const __localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value; },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
    get length() { return Object.keys(store).length; },
    key: (i: number) => Object.keys(store)[i] ?? null,
  };
})();
Object.defineProperty(window, "localStorage", { value: __localStorageMock, writable: true });

const originalWarn = console.warn;
console.warn = (...args: unknown[]) => {
  if (typeof args[0] === "string" && args[0].includes("React Router Future Flag Warning")) {
    return;
  }
  originalWarn(...args);
};
