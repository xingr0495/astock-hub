import { Modal } from './Modal'

export interface NewsItem {
  title?: string
  zh?: string
  zh_summary?: string
  summary?: string
  source?: string
  time?: string
  url?: string
}

interface Props {
  item: NewsItem
  onClose: () => void
}

/**
 * 今日产业要点·详情弹窗：完整标题 + 中文译题 + 摘要正文 + 来源/时间 + 原文链接。
 */
export default function NewsDetailDialog({ item, onClose }: Props) {
  return (
    <Modal
      onClose={onClose}
      labelledBy="news-detail-title"
      panelClassName="w-[92vw] max-w-xl rounded-xl border border-surface-700 bg-surface-850 shadow-2xl"
    >
      <header className="border-b border-surface-700 p-4">
        <div className="flex items-start gap-3">
          <div className="flex-1">
            <h2 id="news-detail-title" className="text-sm font-semibold leading-relaxed text-slate-100">
              {item.zh || item.title || '新闻详情'}
            </h2>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
              {item.source && <span className="rounded bg-surface-700/60 px-1.5 py-0.5">{item.source}</span>}
              {item.time && <span>{item.time}</span>}
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-md px-2 py-1 text-slate-400 hover:bg-surface-700 hover:text-slate-200"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>
      </header>

      <div className="space-y-3 p-4">
        {item.title && item.zh && (
          <p className="text-[11px] leading-relaxed text-slate-500">
            <span className="text-slate-600">原标题：</span>
            {item.title}
          </p>
        )}

        {item.zh_summary ? (
          <p className="text-[13px] leading-relaxed text-slate-200">
            {item.zh_summary}
          </p>
        ) : null}

        {item.summary ? (
          <p className="whitespace-pre-line text-[12px] leading-relaxed text-slate-400">
            {item.zh_summary && <span className="text-slate-600">原摘要：</span>}
            {item.summary}
          </p>
        ) : (
          !item.zh_summary && <p className="text-[12px] text-slate-500">该条目暂无摘要正文。</p>
        )}

        {item.url && (
          <div className="flex justify-end pt-1">
            <a
              href={item.url}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg bg-brand/15 px-3 py-1.5 text-xs text-brand hover:bg-brand/25"
            >
              查看原文 ↗
            </a>
          </div>
        )}
      </div>
    </Modal>
  )
}
