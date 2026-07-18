/**
 * SearchNextStepCard — 检索 run 完成时刻的状态化下一步推荐（0.6.2 S7 / P1-3）。
 *
 * 生产观察：多位真实用户建库 55~70 篇后从未触碰综述/GAP 旗舰功能——建库完成
 * 是推荐下一步的黄金时刻。推荐由共享 readiness selector 驱动（按语料成熟度给
 * 最短下一步，不做泛泛 CTA）；卡片可关闭，关闭后本次会话不再弹出。
 */
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import type { ProjectReadiness, ProjectReadinessStage } from "../hooks/useProjectReadiness";
import { track } from "../lib/track";

interface Props {
  projectId: number;
  readiness: ProjectReadiness;
  onClose: () => void;
}

interface StageCopy {
  title: string;
  desc: string;
  /** 主按钮；no_papers 阶段无跳转（留在当前入口换关键词重试）。 */
  primary?: { text: string; href: (pid: number) => string };
  secondary?: { text: string; href: (pid: number) => string };
}

const STAGE_COPY: Record<ProjectReadinessStage, StageCopy> = {
  no_papers: {
    title: "本次检索没有新入库的文献",
    desc: "换个关键词、放宽年限或换数据源，再检索一次。",
  },
  no_included: {
    title: "题录已入库，下一步：筛选纳入",
    desc: "纳入的文献才会进入综述与研究空白的语料。",
    primary: { text: "去筛选纳入", href: (pid) => `/projects/${pid}/library` },
  },
  not_parsed: {
    title: "文献已纳入，尚未解析全文",
    desc: "请先在文献库完成 OCR 解析（或 AI 解析），再生成综述。",
    primary: { text: "去文献库解析全文", href: (pid) => `/projects/${pid}/library` },
  },
  no_fulltext: {
    title: "已纳入文献，还差可读全文",
    desc: "综述页也提供「自动补全文」一键完成。",
    primary: { text: "去补全文", href: (pid) => `/projects/${pid}/library` },
  },
  ready: {
    title: "语料已就绪，可以进入旗舰能力",
    desc: "生成逐句可溯源的综述，或发现值得做的研究空白。",
    primary: { text: "生成综述", href: (pid) => `/projects/${pid}/analysis/review` },
    secondary: { text: "发现研究空白", href: (pid) => `/projects/${pid}/research` },
  },
};

export function SearchNextStepCard({ projectId, readiness, onClose }: Props) {
  const copy = STAGE_COPY[readiness.stage];
  // 曝光埋点：同一 stage 每次挂载只报一次。
  const trackedStageRef = useRef<ProjectReadinessStage | null>(null);
  useEffect(() => {
    if (trackedStageRef.current === readiness.stage) return;
    trackedStageRef.current = readiness.stage;
    track("search_next_step_view", { stage: readiness.stage }, projectId);
  }, [projectId, readiness.stage]);

  const onAction = (action: string) => {
    track("search_next_step_click", { stage: readiness.stage, action }, projectId);
  };

  return (
    <div className="research-readiness search-next-step" role="status" data-testid="search-next-step">
      <div className="research-readiness-head">
        <h3 className="research-readiness-title">{copy.title}</h3>
        <p className="research-readiness-msg">{copy.desc}</p>
      </div>
      <div className="research-readiness-actions">
        {copy.primary && (
          <Link
            className="btn btn-primary"
            to={copy.primary.href(projectId)}
            onClick={() => onAction(copy.primary!.text)}
          >
            {copy.primary.text}
          </Link>
        )}
        {copy.secondary && (
          <Link
            className="btn"
            to={copy.secondary.href(projectId)}
            onClick={() => onAction(copy.secondary!.text)}
          >
            {copy.secondary.text}
          </Link>
        )}
        <button type="button" className="btn btn-ghost" onClick={onClose}>
          关闭
        </button>
      </div>
    </div>
  );
}
