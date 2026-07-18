/**
 * track.ts — 极简产品埋点上报（0.6.1 P0 漏斗观测）。
 *
 * best-effort：POST /events，失败静默，绝不阻塞或影响主流程。服务端只追加一行，
 * 分析经直连 SQL 按 event / created_at 聚合，覆盖新用户从登录到回访的主漏斗。
 */
import { API_BASE } from "../api/client";

/** 产品主漏斗事件名（与后端 analytics_event.event 对齐）。 */
export type FunnelEvent =
  | "login_success" // 用户使用账号密码登录成功（不含注册）
  | "project_create" // 用户成功创建项目
  | "search_run_start" // 检索建库入口的 Agent run 已发起（props.entry）
  | "search_run_done" // 检索建库入口的 Agent run 已完成（props.entry/status）
  | "papers_imported" // 用户从前端手动导入文献成功（props.count）
  | "analysis_view" // 用户进入分析区并完成视图挂载
  | "gap_view" // 用户进入研究空白区并完成视图挂载
  | "gap_run" // 用户确认并发起研究空白发现 run
  | "search_next_step_view" // 检索完成下一步推荐卡曝光（props.stage）
  | "search_next_step_click" // 点击推荐卡动作（props.stage/action）
  | "app_open" // 已认证用户当日首次打开应用（每自然日每浏览器一次）
  | "chat_gate_blocked" // chat 入口发送前门禁曝光（props.entry/stage）
  | "review_view" // 综述面板曝光
  | "review_precheck_blocked" // precheck 拦截（props.reason=no_included|no_fulltext）
  | "review_backfill_click" // 在 precheck 卡点击自动补全文
  | "review_backfill_done" // 自动补全文完成（props.succeeded/failed）
  | "review_generate_click" // 点击「生成综述」
  | "review_job_done" // 综述任务成功
  | "review_job_failed" // 综述任务失败
  | "gap_feasibility_click"; // 点击「可行性核验」（props.gapId）

/** 判断当前自然日是否需要上报 app_open；存储读写由调用方负责。 */
export function shouldTrackDailyAppOpen(lastDate: string | null, today: string): boolean {
  return lastDate !== today;
}

/** 使用本地时区生成自然日键，避免 UTC 日期跨日导致回访口径偏移。 */
export function localDateKey(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * 返回"是否确认送达"（HTTP 2xx）。绝大多数调用方可无视返回值（fire-and-forget）；
 * app_open 等去重型事件应在送达成功后才写去重标记（codex 复核 P2：先写标记会把
 * 一次瞬时失败放大成整天漏记，污染 D1/D7 回访口径）。任何失败都静默返回 false。
 */
export function track(
  event: FunnelEvent,
  props?: Record<string, unknown>,
  projectId?: number,
): Promise<boolean> {
  try {
    if (typeof fetch !== "function") return Promise.resolve(false);
    return fetch(`${API_BASE}/events`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event, projectId, props }),
      keepalive: true, // 页面卸载/跳转时仍尽力送达（如 review_job_done 后立即离开）
    }).then(
      (res) => res.ok,
      () => false, // 埋点绝不影响主流程
    );
  } catch {
    return Promise.resolve(false);
  }
}
