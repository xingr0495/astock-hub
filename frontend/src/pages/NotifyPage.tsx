import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { Bell, Clock, Check, AlertTriangle, TrendingUp, FileText } from 'lucide-react'

interface NotifyConfig {
  enabled: boolean
  review_enabled: boolean
  review_time: string  // HH:MM
  review_no_early: string  // HH:MM，不早于时间
  manual_enabled: boolean
  manual_push_time: string   // HH:MM
  manual_push_top_n: number
  alert_enabled: boolean
  alert_webhook_url: string
  targets: { url: string; type: string; enabled: boolean; content_filter: string[] }[]
  content_filters: {
    review: boolean
    analysis: boolean
    alert: boolean
    signal: boolean
    manual: boolean
  }
}

const CHANNEL_TYPES = [
  { type: 'feishu', label: '飞书', icon: '🔔', color: 'text-blue-400' },
  { type: 'wechat', label: '企业微信', icon: '💬', color: 'text-green-400' },
  { type: 'dingtalk', label: '钉钉', icon: '📱', color: 'text-orange-400' },
]

const CONTENT_TYPES = [
  { key: 'review', label: '大盘复盘', icon: FileText, desc: '收盘后自动推送当日总结' },
  { key: 'analysis', label: '个股分析', icon: TrendingUp, desc: 'TA/Serenity 分析完成后推送' },
  { key: 'alert', label: '价格告警', icon: AlertTriangle, desc: '价格突破/跌破时推送' },
  { key: 'signal', label: '策略信号', icon: Bell, desc: '策略选股命中时推送' },
  { key: 'manual', label: '纪要池个股', icon: FileText, desc: '每日到点推送纪要池里符合手册的个股' },
]

export default function NotifyPage() {
  const queryClient = useQueryClient()

  // 读取当前配置
  const { data, isLoading } = useQuery({
    queryKey: ['notify-config'],
    queryFn: () => api.notifyConfig(),
  })

  // 本地状态（可编辑）
  const [config, setConfig] = useState<NotifyConfig>({
    enabled: false,
    review_enabled: true,
    review_time: '16:20',
    review_no_early: '15:00',
    manual_enabled: false,
    manual_push_time: '17:10',
    manual_push_top_n: 3,
    alert_enabled: false,
    alert_webhook_url: '',
    targets: [],
    content_filters: {
      review: true,
      analysis: true,
      alert: true,
      signal: true,
      manual: true,
    },
  })

  // 加载配置
  useEffect(() => {
    if (data) {
      setConfig({
        enabled: data.enabled ?? false,
        review_enabled: data.review_enabled ?? true,
        review_time: data.review_time ?? '16:20',
        review_no_early: data.review_no_early ?? '15:00',
        manual_enabled: data.manual_enabled ?? false,
        manual_push_time: data.manual_push_time ?? '17:10',
        manual_push_top_n: data.manual_push_top_n ?? 3,
        alert_enabled: data.alert_enabled ?? false,
        alert_webhook_url: data.alert_webhook_url ?? '',
        targets: data.targets ?? [],
        content_filters: {
          review: data.content_filters?.review ?? true,
          analysis: data.content_filters?.analysis ?? true,
          alert: data.content_filters?.alert ?? true,
          signal: data.content_filters?.signal ?? true,
          manual: data.content_filters?.manual ?? true,
        },
      })
    }
  }, [data])

  // 测试 Webhook
  const testMutation = useMutation({
    mutationFn: (url: string) => api.notifyTest(url),
  })

  // 保存配置
  const saveMutation = useMutation({
    mutationFn: () => api.notifyConfigUpdate(config),
    onSuccess: () => {
      queryClient.invalidateQueries(['notify-config'])
      alert('配置已保存')
    },
  })

  const [manualTesting, setManualTesting] = useState(false)
  const [manualResult, setManualResult] = useState<{
    ok: boolean; mode?: string | null; counts?: { trend: number; arb: number } | null
    is_empty?: boolean; error?: string | null; reason?: string
  } | null>(null)
  const [alertTestResult, setAlertTestResult] = useState<string | null>(null)

  async function testManualPush() {
    setManualTesting(true)
    setManualResult(null)
    try {
      const r = await api.manualPushTest()
      setManualResult(r)
    } catch (e) {
      setManualResult({ ok: false, error: (e as Error).message })
    } finally {
      setManualTesting(false)
    }
  }

  // 添加新渠道
  const addTarget = (type: string) => {
    setConfig(prev => ({
      ...prev,
      targets: [...prev.targets, { url: '', type, enabled: true, content_filter: [] }],
    }))
  }

  // 删除渠道
  const removeTarget = (index: number) => {
    setConfig(prev => ({
      ...prev,
      targets: prev.targets.filter((_, i) => i !== index),
    }))
  }

  // 更新渠道
  const updateTarget = (index: number, updates: Partial<typeof config.targets[0]>) => {
    setConfig(prev => ({
      ...prev,
      targets: prev.targets.map((t, i) => i === index ? { ...t, ...updates } : t),
    }))
  }

  if (isLoading) {
    return <div className="py-20 text-center text-slate-400">加载配置中...</div>
  }

  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 rounded-xl bg-brand/15 flex items-center justify-center">
          <Bell className="h-5 w-5 text-brand" />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-slate-100">推送设置</h1>
          <p className="text-xs text-slate-500">配置飞书/企微推送，选择推送内容类型</p>
        </div>
      </div>

      {/* 主开关 */}
      <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">启用推送通知</h2>
            <p className="text-xs text-slate-500">开启后将推送选定内容到配置的渠道</p>
          </div>
          <button
            onClick={() => setConfig(prev => ({ ...prev, enabled: !prev.enabled }))}
            className={`relative h-6 w-11 rounded-full transition-colors ${
              config.enabled ? 'bg-brand' : 'bg-surface-600'
            }`}
          >
            <span
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${
                config.enabled ? 'left-[22px]' : 'left-0.5'
              }`}
            />
          </button>
        </div>
      </section>

      {/* 定时复盘 */}
      <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-blue-400" />
            <h2 className="text-sm font-semibold text-slate-100">定时复盘</h2>
          </div>
          <button
            onClick={() => setConfig(prev => ({ ...prev, review_enabled: !prev.review_enabled }))}
            className={`relative h-6 w-11 rounded-full transition-colors ${
              config.review_enabled ? 'bg-brand' : 'bg-surface-600'
            }`}
          >
            <span
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${
                config.review_enabled ? 'left-[22px]' : 'left-0.5'
              }`}
            />
          </button>
        </div>

        {config.review_enabled && (
          <div className="space-y-4">
            {/* 执行时间 */}
            <div className="flex items-center gap-3">
              <span className="text-xs text-slate-400">每日</span>
              <input
                type="time"
                value={config.review_time}
                onChange={(e) => setConfig(prev => ({ ...prev, review_time: e.target.value }))}
                className="rounded-md border border-surface-600 bg-surface-800 px-3 py-1 text-sm text-slate-200"
              />
              <span className="text-xs text-slate-500">不早于 {config.review_no_early} · 工作日执行</span>
            </div>

            {/* 推送内容勾选 */}
            <div>
              <p className="text-xs text-slate-400 mb-2">推送触发方式：自动</p>
              <p className="text-[11px] text-slate-500">定时与手动生成的复盘报告都会自动推送完整报告</p>
            </div>
          </div>
        )}
      </section>

      {/* 主线手册推送 */}
      <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-emerald-400" />
            <h2 className="text-sm font-semibold text-slate-100">个股推送（纪要池）</h2>
            <span className="text-[10px] text-slate-500">纪要池今日提及 ∩ 就绪 · 就绪≠可买</span>
          </div>
          <button
            onClick={() => setConfig(prev => ({ ...prev, manual_enabled: !prev.manual_enabled }))}
            className={`relative h-6 w-11 rounded-full transition-colors ${
              config.manual_enabled ? 'bg-brand' : 'bg-surface-600'
            }`}
          >
            <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${
              config.manual_enabled ? 'left-[22px]' : 'left-0.5'
            }`} />
          </button>
        </div>

        {config.manual_enabled && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-xs text-slate-400">每日</span>
              <input
                type="time"
                value={config.manual_push_time}
                onChange={(e) => setConfig(prev => ({ ...prev, manual_push_time: e.target.value }))}
                className="rounded-md border border-surface-600 bg-surface-800 px-3 py-1 text-sm text-slate-200"
              />
              <span className="text-xs text-slate-500">推送纪要池里今日提及且趋势/套利就绪的个股</span>
              <button
                onClick={testManualPush}
                disabled={manualTesting}
                className="ml-auto rounded-lg border border-surface-600 px-3 py-1.5 text-[12px] text-slate-200 hover:border-brand/50 hover:text-brand disabled:opacity-50"
              >
                {manualTesting ? '推送中…' : '立即推送测试'}
              </button>
            </div>
            {manualResult && (
              <p className={`text-[11px] ${manualResult.ok ? 'text-emerald-300' : 'text-rose-300'}`}>
                {manualResult.ok
                  ? `已推送：纪要池趋势 ${manualResult.counts?.trend ?? 0} 只 / 套利 ${manualResult.counts?.arb ?? 0} 只${manualResult.is_empty ? '（空消息）' : ''}`
                  : `推送失败：${manualResult.error ?? manualResult.reason ?? '未知'}`}
              </p>
            )}
          </div>
        )}
      </section>

      {/* 推送内容选择 */}
      <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <div className="flex items-center gap-2 mb-4">
          <Check className="h-4 w-4 text-green-400" />
          <h2 className="text-sm font-semibold text-slate-100">推送内容</h2>
        </div>

        <div className="space-y-2">
          {CONTENT_TYPES.map(({ key, label, icon: Icon, desc }) => (
            <label
              key={key}
              className="flex items-center gap-3 rounded-lg border border-surface-600 bg-surface-800 p-3 cursor-pointer hover:border-brand/40 transition-colors"
            >
              <input
                type="checkbox"
                checked={config.content_filters[key as keyof typeof config.content_filters]}
                onChange={(e) => {
                  setConfig(prev => ({
                    ...prev,
                    content_filters: {
                      ...prev.content_filters,
                      [key]: e.target.checked,
                    },
                  }))
                }}
                className="rounded border-surface-600 text-brand focus:ring-brand"
              />
              <Icon className="h-4 w-4 text-slate-400" />
              <div className="flex-1">
                <div className="text-xs font-medium text-slate-200">{label}</div>
                <div className="text-[10px] text-slate-500">{desc}</div>
              </div>
            </label>
          ))}
        </div>
      </section>

      {/* 渠道配置 */}
      <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <span className="text-sm">📡</span>
            <h2 className="text-sm font-semibold text-slate-100">推送渠道</h2>
          </div>
          <button
            onClick={() => {
              const type = prompt('选择渠道类型：\n1. 飞书 (feishu)\n2. 企业微信 (wechat)\n3. 钉钉 (dingtalk)')
              if (type) addTarget(type)
            }}
            className="rounded-md bg-brand/15 px-3 py-1.5 text-xs text-brand hover:bg-brand/25"
          >
            + 添加渠道
          </button>
        </div>

        {config.targets.length === 0 ? (
          <div className="py-8 text-center text-xs text-slate-500">
            还没有配置推送渠道
          </div>
        ) : (
          <div className="space-y-3">
            {config.targets.map((target, index) => {
              const channelInfo = CHANNEL_TYPES.find(c => c.type === target.type)
              return (
                <div key={index} className="rounded-lg border border-surface-600 bg-surface-800 p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-sm">{channelInfo?.icon ?? '📡'}</span>
                    <span className="text-xs font-medium text-slate-200">
                      {channelInfo?.label ?? target.type}
                    </span>
                    {target.url ? (
                      <span className="rounded bg-green-500/15 px-2 py-0.5 text-[10px] text-green-400">已配置</span>
                    ) : (
                      <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[10px] text-amber-400">未配置</span>
                    )}
                    <button
                      onClick={() => removeTarget(index)}
                      className="ml-auto text-slate-500 hover:text-rose-400 text-xs"
                    >
                      删除
                    </button>
                  </div>

                  <input
                    type="url"
                    value={target.url}
                    onChange={(e) => updateTarget(index, { url: e.target.value })}
                    placeholder="Webhook URL"
                    className="w-full rounded border border-surface-600 bg-surface-900 px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 mb-2"
                  />

                  {target.url && (
                    <button
                      onClick={() => testMutation.mutate(target.url)}
                      disabled={testMutation.isPending}
                      className="rounded border border-surface-600 px-2 py-0.5 text-[10px] text-slate-400 hover:text-brand hover:border-brand/40"
                    >
                      {testMutation.isPending ? '测试中...' : '测试连通'}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* 失败告警（独立地址） */}
      <section className="rounded-lg border border-surface-600 bg-surface-800 p-4">
        <h3 className="text-sm font-medium text-slate-200 mb-1">失败告警</h3>
        <p className="text-[11px] text-slate-500 mb-3">
          每个工作日 18:00 检查；仅 16:00 数据链 / 17:00 复盘推送硬失败或缺席时通知，正常不发消息。
          与上方推送渠道互不影响（独立地址，建议用只拉自己的机器人）。
        </p>
        <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-300 mb-3">
          <input
            type="checkbox"
            checked={config.alert_enabled}
            onChange={(e) => setConfig({ ...config, alert_enabled: e.target.checked })}
            className="accent-brand"
          />
          启用定时链失败告警
        </label>
        <input
          type="url"
          value={config.alert_webhook_url}
          onChange={(e) => setConfig({ ...config, alert_webhook_url: e.target.value })}
          placeholder="告警专用 Webhook URL（https://open.feishu.cn/...）"
          className="w-full rounded border border-surface-600 bg-surface-900 px-3 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 mb-2"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={async () => {
              setAlertTestResult(null)
              try {
                // 用表单当前值测试（不依赖先保存）；真实告警仍读已保存配置
                const r = await api.notifyAlertTest({
                  alert_enabled: config.alert_enabled,
                  alert_webhook_url: config.alert_webhook_url,
                })
                setAlertTestResult(r.ok ? '✅ 测试消息已发送' : (r.error ?? '发送失败'))
              } catch (e) {
                setAlertTestResult(`请求失败：${(e as Error).message}`)
              }
            }}
            className="rounded border border-surface-600 px-2 py-0.5 text-[10px] text-slate-400 hover:text-brand hover:border-brand/40"
          >
            发送测试告警
          </button>
          {alertTestResult && (
            <span className="text-[11px] text-slate-400">{alertTestResult}</span>
          )}
        </div>
        <p className="mt-2 text-[10px] text-slate-600">
          测试用表单当前值；真实告警（链失败/18:00 守夜）读的是已保存配置——确认测试收到后，记得点页面底部「保存」。
        </p>
      </section>

      {/* 保存按钮 */}
      <div className="flex justify-end gap-3">
        <button
          onClick={() => queryClient.invalidateQueries(['notify-config'])}
          className="rounded-md border border-surface-600 px-4 py-2 text-sm text-slate-300 hover:border-surface-500"
        >
          取消
        </button>
        <button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending}
          className="rounded-md bg-brand px-4 py-2 text-sm text-white hover:bg-brand/90 disabled:opacity-50"
        >
          {saveMutation.isPending ? '保存中...' : '保存'}
        </button>
      </div>
    </div>
  )
}
