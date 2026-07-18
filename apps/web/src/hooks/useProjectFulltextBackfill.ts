import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useBackfillFulltext } from "../api/agentHooks";
import type { FulltextBackfillResult } from "../api/client";
import { useSciverseSettings } from "../api/useSciverseSettings";

const MAX_BATCHES = 20;
const BATCH_SIZE = 50;

export interface FulltextBackfillProgress {
  done: number;
  total: number;
  failed: number;
}

const EMPTY_RESULT: FulltextBackfillResult = {
  total: 0,
  fetched: 0,
  failed: [],
  skipped: 0,
  remaining: 0,
};

export function useProjectFulltextBackfill(pid: number) {
  const queryClient = useQueryClient();
  const mutation = useBackfillFulltext(pid);
  const { settings: sciverse } = useSciverseSettings();
  const runningRef = useRef(false);
  const [isPending, setIsPending] = useState(false);
  const [progress, setProgress] = useState<FulltextBackfillProgress | null>(null);
  const [result, setResult] = useState<FulltextBackfillResult | null>(null);

  const reset = useCallback(() => {
    mutation.reset();
    setProgress(null);
    setResult(null);
  }, [mutation]);

  const run = useCallback(async (): Promise<FulltextBackfillResult> => {
    if (runningRef.current) {
      return result ?? EMPTY_RESULT;
    }

    runningRef.current = true;
    setIsPending(true);
    setProgress(null);
    setResult(null);
    mutation.reset();

    const aggregate: FulltextBackfillResult = { ...EMPTY_RESULT, failed: [] };
    let previousRemaining: number | null = null;

    try {
      for (let i = 0; i < MAX_BATCHES; i += 1) {
        // 排除已失败项，避免它们反复占据前排、饿死后续候选。
        const excludePaperIds = aggregate.failed.map((item) => item.paperId);
        const batch = await mutation.mutateAsync({
          maxPapers: BATCH_SIZE,
          excludePaperIds: excludePaperIds.length > 0 ? excludePaperIds : undefined,
          sciverse: {
            apiToken: sciverse.apiToken || undefined,
            baseUrl: sciverse.baseUrl || undefined,
          },
        });

        // total 取首轮全量；fetched/failed 跨轮累加；skipped/remaining 取最新一轮。
        if (i === 0) aggregate.total = batch.total;
        aggregate.fetched += batch.fetched;
        aggregate.skipped = batch.skipped;
        aggregate.failed = aggregate.failed.concat(batch.failed ?? []);
        aggregate.remaining = batch.remaining;

        const snapshot = { ...aggregate, failed: [...aggregate.failed] };
        setResult(snapshot);
        setProgress({
          done: aggregate.fetched + aggregate.failed.length,
          total: aggregate.total,
          failed: aggregate.failed.length,
        });

        const madeProgress = batch.fetched + (batch.failed?.length ?? 0) > 0;
        if (batch.remaining <= 0 || !madeProgress || batch.remaining === previousRemaining) break;
        previousRemaining = batch.remaining;
      }

      // mutation 每批也会失效缓存；整轮结束后再等待一次，保证调用方可依赖最新项目统计。
      await queryClient.invalidateQueries({ queryKey: ["project", pid] });
      return { ...aggregate, failed: [...aggregate.failed] };
    } finally {
      runningRef.current = false;
      setIsPending(false);
    }
  }, [mutation, pid, queryClient, result, sciverse.apiToken, sciverse.baseUrl]);

  return {
    run,
    reset,
    isPending,
    progress,
    result,
    error: mutation.error,
  };
}
