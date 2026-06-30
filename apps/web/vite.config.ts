/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("vis-network") || id.includes("vis-data") || id.includes("@egjs/hammerjs")) {
            return "vendor-network";
          }
          if (id.includes("echarts-wordcloud")) return "vendor-echarts-wordcloud";
          if (id.includes("echarts") || id.includes("zrender")) return "vendor-echarts";
          if (id.includes("marked") || id.includes("dompurify")) return "vendor-markdown";
          return undefined;
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
