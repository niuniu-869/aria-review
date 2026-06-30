// TanStack Query 封装 (服务端态)
import { useQuery } from "@tanstack/react-query";
import {
  getAuthorProduction,
  getAuthors,
  getCitedRefs,
  getConceptual,
  getCorpus,
  getDocuments,
  getEvolution,
  getHealth,
  getHistcite,
  getIntellectual,
  getKeywordTrend,
  getOverview,
  getSocial,
  getSources,
  getThematic,
  getThreeField,
} from "./client";

export function useHealth() {
  return useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 15000 });
}

// corpus 状态轮询: parsing 时每 1.5s 重拉, ready/failed 停止 (Codex step4-P1)
export function useCorpusStatus(projectId: string, corpusId: string) {
  return useQuery({
    queryKey: ["corpus", projectId, corpusId],
    queryFn: () => getCorpus(projectId, corpusId),
    refetchInterval: (q) => (q.state.data?.status === "parsing" ? 1500 : false),
  });
}

// overview 仅在 corpus ready 后才取 (避免 409 CORPUS_NOT_READY, Codex step4-P1)
export function useOverview(projectId: string, corpusId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["overview", projectId, corpusId],
    queryFn: () => getOverview(projectId, corpusId),
    enabled,
  });
}

export function useSources(projectId: string, corpusId: string) {
  return useQuery({ queryKey: ["sources", projectId, corpusId], queryFn: () => getSources(projectId, corpusId) });
}

export function useAuthors(projectId: string, corpusId: string) {
  return useQuery({ queryKey: ["authors", projectId, corpusId], queryFn: () => getAuthors(projectId, corpusId) });
}

export function useDocuments(projectId: string, corpusId: string) {
  return useQuery({ queryKey: ["documents", projectId, corpusId], queryFn: () => getDocuments(projectId, corpusId) });
}

// --- A4 高级图 hooks (消费可用性信封) ---
export function useAuthorProduction(projectId: string, corpusId: string) {
  return useQuery({
    queryKey: ["authorProduction", projectId, corpusId],
    queryFn: () => getAuthorProduction(projectId, corpusId),
  });
}

export function useKeywordTrend(projectId: string, corpusId: string) {
  return useQuery({
    queryKey: ["keywordTrend", projectId, corpusId],
    queryFn: () => getKeywordTrend(projectId, corpusId),
  });
}

export function useCitedRefs(projectId: string, corpusId: string) {
  return useQuery({
    queryKey: ["citedRefs", projectId, corpusId],
    queryFn: () => getCitedRefs(projectId, corpusId),
  });
}

export function useConceptual(projectId: string, corpusId: string) {
  return useQuery({ queryKey: ["conceptual", projectId, corpusId], queryFn: () => getConceptual(projectId, corpusId) });
}

export function useIntellectual(projectId: string, corpusId: string) {
  return useQuery({ queryKey: ["intellectual", projectId, corpusId], queryFn: () => getIntellectual(projectId, corpusId) });
}

export function useSocial(projectId: string, corpusId: string) {
  return useQuery({ queryKey: ["social", projectId, corpusId], queryFn: () => getSocial(projectId, corpusId) });
}

// --- A5 高级图② hooks (消费可用性信封) ---
export function useThematic(projectId: string, corpusId: string) {
  return useQuery({
    queryKey: ["thematic", projectId, corpusId],
    queryFn: () => getThematic(projectId, corpusId),
  });
}

export function useEvolution(projectId: string, corpusId: string) {
  return useQuery({
    queryKey: ["evolution", projectId, corpusId],
    queryFn: () => getEvolution(projectId, corpusId),
  });
}

export function useHistcite(projectId: string, corpusId: string) {
  return useQuery({
    queryKey: ["histcite", projectId, corpusId],
    queryFn: () => getHistcite(projectId, corpusId),
  });
}

export function useThreefield(projectId: string, corpusId: string) {
  return useQuery({
    queryKey: ["threefield", projectId, corpusId],
    queryFn: () => getThreeField(projectId, corpusId),
  });
}
