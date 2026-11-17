import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { FileText, RefreshCw, Trash2, Send } from 'lucide-react'

interface ReviewReport {
  id: string
  as_of: string
  content: string
  summary: string
  emotion_score?: number
  emotion_label?: string
}

export default function ReviewPage() {
  const [selectedReport, setSelectedReport] = useState<ReviewReport | null>(null)
  const [focus, setFocus] = useState('')
  const [reviewDate, setReviewDate] = useState('') // 空 = 最新交易日
  const [genError, setGenError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  // 获取历史复盘列表
  const reportsQuery = useQuery({
    queryKey: ['review-reports'],
    queryFn: () => api.reviewReports(),
    staleTime: 60_000,
  })

  // 可选交易日（情绪周期历史，升序 → 倒序展示）
  const regimeQuery = useQuery({
    queryKey: ['regime-trade-days'],
    queryFn: () => api.regime(20, false),
    staleTime: 5 * 60_000,
  })
  const tradeDays: string[] = (() => {
    const hist = (regimeQuery.data?.data as any)?.history as { date: string }[] | undefined
    return (hist ?? []).map((h) => h.date).reverse()
  })()
  const latestDay = tradeDays[0] ?? ''
  const now = new Date()
  const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`

  // 生成复盘。默认项显示"最新交易日(latestDay)"时必须把该日期显式传过去：
  // 不传日期后端会按 auto 模式处理——盘中返回的是今日未收盘的实时帧，
  // 会出现"选的 22 号、生成变 23 号"（2026-10-23 实测，后端同时已加盘中守卫）。
  const generateMutation = useMutation({
    mutationFn: () => api.reviewAnalyze(focus, reviewDate || latestDay || undefined),
    onSuccess: (data) => {
      if (data.ok && data.content) {
        setGenError(null)
        setSelectedReport({
          id: `review_${data.as_of.replace(/-/g, '')}`,
          as_of: data.as_of,
          content: data.content,
          summary: data.summary ?? '',
          emotion_score: data.emotion_score,
          emotion_label: data.emotion_label,
        })
      } else {
        setGenError(data.error ?? '复盘生成失败')
      }
    },
    onError: (e) => setGenError(`复盘生成失败：${(e as Error).message}`),
  })

  // 保存复盘
  const saveMutation = useMutation({
    mutationFn: (report: ReviewReport) => api.reviewSave(report),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['review-reports'] })
    },
  })

  // 删除复盘
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.reviewDelete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['review-reports'] })
      if (selectedReport?.id === selectedReport?.id) {
        setSelectedReport(null)
      }
    },
  })

  const reports = reportsQuery.data?.reports ?? []

  return (
    <div className="space-y-4">
      <header className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">大盘复盘</h1>
          <div className="mt-1 text-xs text-slate-500">
            AI 生成每日市场复盘报告 · 7段式结构 · 支持历史查看与自选日期
          </div>
        </div>
        <div className="flex w-full items-center gap-2 md:w-auto">
          <select
            value={reviewDate}
            onChange={(e) => setReviewDate(e.target.value)}
            title="选择复盘基准日期（默认最新交易日；盘中当天数据未闭环，只可选历史交易日）"
            className="min-w-0 flex-1 rounded-lg border border-surface-600 bg-surface-800 px-2.5 py-2 text-sm text-slate-200 focus:border-brand focus:outline-none md:flex-none"
          >
            <option value="">最新交易日{latestDay ? `（${latestDay}）` : ''}</option>
            {tradeDays.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
          <button
            onClick={() => generateMutation.mutate()}
            disabled={generateMutation.isPending}
            className="flex shrink-0 items-center gap-2 whitespace-nowrap rounded-lg bg-brand/20 px-4 py-2 text-sm text-brand transition-colors hover:bg-brand/30 disabled:opacity-50"
          >
            {generateMutation.isPending ? (
              <RefreshCw className="h-4 w-4 animate-spin" />
            ) : (
              <FileText className="h-4 w-4" />
            )}
            {generateMutation.isPending
              ? '生成中…'
              : selectedReport
                ? '重新生成'
                : reviewDate
                  ? `生成${reviewDate}复盘`
                  : latestDay && latestDay !== todayStr
                    ? `生成${latestDay}复盘`
                    : '生成今日复盘'}
          </button>
        </div>
      </header>

      {genError && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {genError}
        </div>
      )}

      {/* 关注点输入 */}
      <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <label className="mb-2 block text-xs text-slate-500">特别关注（可选）</label>
        <input
          type="text"
          value={focus}
          onChange={(e) => setFocus(e.target.value)}
          placeholder="例如：关注北向资金动向、分析科技板块机会"
          className="w-full rounded-lg border border-surface-600 bg-surface-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:border-brand focus:outline-none"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* 历史复盘列表 */}
        <div className="lg:col-span-1">
          <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
            <h2 className="mb-3 text-sm font-medium text-slate-200">历史复盘</h2>
            {reportsQuery.isLoading ? (
              <div className="py-4 text-center text-xs text-slate-400">加载中...</div>
            ) : reports.length === 0 ? (
              <div className="py-4 text-center text-xs text-slate-500">暂无历史复盘</div>
            ) : (
              <div className="space-y-2">
                {reports.map((report) => (
                  <div
                    key={report.id}
                    onClick={() => setSelectedReport(report)}
                    className={`cursor-pointer rounded-lg p-3 transition-colors ${
                      selectedReport?.id === report.id
                        ? 'bg-brand/15 border border-brand/30'
                        : 'bg-surface-800 hover:bg-surface-700'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-slate-200">{report.as_of}</span>
                      {report.emotion_label && (
                        <span className={`rounded px-1.5 py-0.5 text-[10px] ${
                          (report.emotion_score ?? 50) >= 60 ? 'bg-emerald-500/20 text-emerald-300' :
                          (report.emotion_score ?? 50) >= 40 ? 'bg-amber-500/20 text-amber-300' :
                          'bg-rose-500/20 text-rose-300'
                        }`}>
                          {report.emotion_label}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 line-clamp-2 text-[11px] text-slate-500">
                      {report.summary || '暂无摘要'}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* 复盘报告内容 */}
        <div className="lg:col-span-2">
          {selectedReport ? (
            <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-medium text-slate-200">
                    {selectedReport.as_of} 大盘复盘
                  </h2>
                  {selectedReport.emotion_label && (
                    <span className="mt-1 inline-block rounded px-1.5 py-0.5 text-[10px] text-slate-400">
                      情绪：{selectedReport.emotion_label}（{selectedReport.emotion_score}分）
                    </span>
                  )}
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => saveMutation.mutate(selectedReport)}
                    disabled={saveMutation.isPending}
                    className="flex items-center gap-1 rounded-lg bg-surface-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-surface-600"
                  >
                    <FileText className="h-3 w-3" />
                    保存
                  </button>
                  <button
                    onClick={() => deleteMutation.mutate(selectedReport.id)}
                    disabled={deleteMutation.isPending}
                    className="flex items-center gap-1 rounded-lg bg-rose-500/10 px-3 py-1.5 text-xs text-rose-300 hover:bg-rose-500/20"
                  >
                    <Trash2 className="h-3 w-3" />
                    删除
                  </button>
                </div>
              </div>
              <div className="prose prose-invert prose-sm max-w-none">
                <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-slate-300">
                  {selectedReport.content}
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-surface-600 bg-surface-850/50 py-16 text-center">
              <FileText className="mx-auto mb-3 h-8 w-8 text-slate-600" />
              <p className="text-sm text-slate-500">
                点击「生成今日复盘」或选择历史复盘查看
              </p>
              <p className="mt-1 text-xs text-slate-600">
                基于实时看板数据，AI 生成7段式复盘报告
              </p>
            </div>
          )}
        </div>
      </div>

      {/* 生成中提示 */}
      {generateMutation.isPending && (
        <div className="fixed bottom-4 right-4 rounded-lg bg-brand/20 px-4 py-2 text-sm text-brand shadow-lg">
          <RefreshCw className="mr-2 inline h-4 w-4 animate-spin" />
          正在生成复盘报告...
        </div>
      )}
    </div>
  )
}
