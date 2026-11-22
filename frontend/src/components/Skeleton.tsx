/** 轻量骨架屏（A2-7）：替代各页重复的"加载中…"文本。 */
export function Skeleton({ lines = 3, className = '' }: { lines?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`} aria-busy="true" aria-label="加载中">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-3 animate-pulse rounded bg-surface-700/70"
          style={{ width: `${92 - i * 10}%` }}
        />
      ))}
    </div>
  )
}

export function CardSkeleton({ className = '' }: { className?: string }) {
  return (
    <div className={`rounded-xl border border-surface-700 bg-surface-850 p-4 ${className}`}>
      <div className="mb-3 h-4 w-1/3 animate-pulse rounded bg-surface-700/70" />
      <Skeleton lines={4} />
    </div>
  )
}
