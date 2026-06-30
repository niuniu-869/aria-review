/**
 * SocialPanel — 合作网络（A3 知识结构组）
 *
 * 作者合作 + 国家合作 双网络，各自独立 NetworkCard（独立 Top-N 滑块状态 + 独立导出 + 独立空态）。
 * 纯渲染 useSocial 既有数据；PDF 语料常缺机构/国家字段 → 对应 graph 空 → 各自诚实空态。
 */
import { useSocial } from "../api/hooks";
import { NetworkCard } from "./viz";

export function SocialPanel({ projectId, corpusId }: { projectId: string; corpusId: string }) {
  const { data, isLoading, isError, error } = useSocial(projectId, corpusId);
  const err = isError ? error : undefined;
  const authorCollab = data?.authorCollab ?? { nodes: [], edges: [] };
  const countryCollab = data?.countryCollab ?? { nodes: [], edges: [] };

  return (
    <section>
      <h2>合作关系网</h2>
      <NetworkCard
        title="作者合作网络"
        subtitle="合著关系构成的学者协作网"
        graph={authorCollab}
        loading={isLoading}
        error={err}
        height={400}
        filename="作者合作网络"
      />
      <NetworkCard
        title="国家合作网络"
        subtitle="跨国合著揭示的国际协作格局"
        graph={countryCollab}
        loading={isLoading}
        error={err}
        height={400}
        filename="国家合作网络"
      />
    </section>
  );
}
