/**
 * TrustBadgeStrip.tsx — 静态「可信」徽章条（Phase 5）
 *
 * 一行三枚 chip，表达平台的全局可信主张（区别于 TrustCard 的「本次运行实测」）：
 *   - 可验证 RunLog 哈希链
 *   - grounding 溯源
 *   - 零伪造约束
 *
 * 纯静态、无 props、无网络，宋体/宣纸调，单色 ✓（var(--ok)），不使用彩色 emoji。
 */

const BADGES = ["可验证 RunLog 哈希链", "grounding 溯源", "零伪造约束"] as const;

export function TrustBadgeStrip() {
  return (
    <div className="trust-badge-strip" role="list" aria-label="平台可信保障">
      {BADGES.map((label) => (
        <span key={label} className="trust-badge" role="listitem">
          <span className="trust-badge-check" aria-hidden="true">
            ✓
          </span>
          {label}
        </span>
      ))}
    </div>
  );
}
