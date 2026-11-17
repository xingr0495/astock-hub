import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getJson, postJson, deleteJson } from '../lib/api'
import { useAuth } from '../auth'

type LlmKind = 'api' | 'claude-cli'

interface LlmProfile {
  id: string
  base_url: string
  model: string
  kind?: LlmKind
  has_api_key: boolean
  key_preview?: string
  max_output_tokens?: number
  context_window?: number
}
interface CredView {
  llm: { profiles: LlmProfile[]; default: string | null }
}

interface FormState {
  id: string
  kind: LlmKind
  base_url: string
  model: string
  api_key: string
  max_tokens: string
  context_window: string
}

// 快速预设（OpenAI 兼容源 + 免 Key 的本机订阅通道）
const PRESETS: { label: string; url: string; model: string; kind?: LlmKind }[] = [
  { label: 'Claude 订阅（本机命令行，零 API 费用）', url: '', model: '', kind: 'claude-cli' },
  { label: 'MiMo 按量', url: 'https://api.xiaomimimo.com/v1', model: 'mimo-v2.6-flash' },
  { label: 'MiMo Token Plan', url: 'https://token-plan-cn.xiaomimimo.com/v1', model: 'mimo-v2.6-flash' },
  { label: 'OpenAI', url: 'https://api.openai.com/v1', model: 'gpt-5.5' },
  { label: 'DeepSeek', url: 'https://api.deepseek.com', model: 'deepseek-v4-pro' },
  { label: '火山方舟 Coding Plan', url: 'https://ark.cn-beijing.volces.com/api/coding/v3', model: 'ark-code-latest' },
  { label: '火山方舟 Agent Plan', url: 'https://ark.cn-beijing.volces.com/api/plan/v3', model: 'ark-code-latest' },
  { label: '通义千问', url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen3.6-plus' },
  { label: '智谱 GLM', url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-5.3' },
  { label: 'Kimi', url: 'https://api.moonshot.cn/v1', model: 'kimi-k3' },
  { label: '自定义', url: '', model: '' },
]

const EMPTY: FormState = {
  kind: 'api', id: '', base_url: '', model: '', api_key: '',
  max_tokens: '8192', context_window: '64000',
}

export default function LlmSettingsPage() {
  const qc = useQueryClient()
  const { isAdmin } = useAuth()   // 订阅通道（本机 claude 命令行）是机主专属，访客不展示该预设
  const credQuery = useQuery({
    queryKey: ['credentials'],
    queryFn: () => getJson<CredView>('/api/credentials'),
  })
  const llm = credQuery.data?.llm ?? { profiles: [], default: null }

  const [form, setForm] = useState<FormState>(EMPTY)
  const [busy, setBusy] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showKey, setShowKey] = useState(false)
  const [makeDefault, setMakeDefault] = useState(false)
  const [activePreset, setActivePreset] = useState('MiMo Token Plan')

  function update(patch: Partial<FormState>) {
    setForm((f) => ({ ...f, ...patch }))
  }

  function applyPreset(p: { label: string; url: string; model: string; kind?: LlmKind }) {
    setActivePreset(p.label)
    const kind = p.kind ?? 'api'
    setForm((f) => ({
      ...f, kind, base_url: p.url, model: p.model,
      // 切到订阅通道时清空 Key 输入，避免误提交
      api_key: kind === 'claude-cli' ? '' : f.api_key,
    }))
  }

  const toPositiveInt = (v: string) => {
    const n = parseInt(v, 10)
    return Number.isInteger(n) && n > 0 ? n : undefined
  }

  async function save() {
    setBusy('save')
    setError(null)
    try {
      const id = form.id.trim() || 'default'
      const payload: Record<string, unknown> = {
        id,
        kind: form.kind,
        base_url: form.kind === 'claude-cli' ? '' : form.base_url.trim(),
        model: form.kind === 'claude-cli' ? '' : form.model.trim(),
        max_output_tokens: form.kind === 'api' ? toPositiveInt(form.max_tokens) : undefined,
        context_window: form.kind === 'api' ? toPositiveInt(form.context_window) : undefined,
      }
      if (form.kind === 'api' && form.api_key) payload.api_key = form.api_key
      payload.make_default = makeDefault
      await postJson('/api/credentials/llm', payload)
      setForm(EMPTY)
      setShowKey(false)
      setMakeDefault(false)
      qc.invalidateQueries({ queryKey: ['credentials'] })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function test() {
    setBusy('test')
    setTestResult(null)
    setError(null)
    try {
      const payload: Record<string, unknown> = { kind: 'llm', llm_kind: form.kind }
      if (form.id.trim()) payload.id = form.id.trim()
      if (form.kind === 'api') {
        if (form.base_url.trim()) payload.base_url = form.base_url.trim()
        if (form.model.trim()) payload.model = form.model.trim()
        if (form.api_key) payload.api_key = form.api_key
      }
      const r = await postJson<Record<string, unknown>>(
        '/api/credentials/test', payload)
      setTestResult(r)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function remove(id: string) {
    setBusy(id)
    try {
      await deleteJson(`/api/credentials/llm?id=${encodeURIComponent(id)}`)
      qc.invalidateQueries({ queryKey: ['credentials'] })
    } finally {
      setBusy(null)
    }
  }

  async function setDefault(id: string) {
    setBusy(`default-${id}`)
    setError(null)
    try {
      await postJson('/api/credentials/llm/default', { id })
      qc.invalidateQueries({ queryKey: ['credentials'] })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const inputCls =
    'w-full rounded-lg border border-surface-600 bg-surface-800 px-2.5 py-2 text-xs text-slate-200 placeholder:text-slate-600 focus:border-brand/50 focus:outline-none'

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">大模型接口</h1>
        <p className="mt-1 text-xs text-slate-500">
          配置大模型通道：OpenAI 兼容接口（需 Key）或本机 Claude 订阅（零 API 费用）。保存并设为默然后，
          纪要汇总、本地资料摘要、AI 对话、产业链深度研究、资讯翻译立即全部切换；
          TradingAgents 深度分析在订阅档下自动回退 API 通道。
        </p>
      </header>

      {/* 已保存的模型配置 */}
      {llm.profiles.length > 0 && (
        <div className="space-y-2">
          {llm.profiles.map((p) => (
            <section
              key={p.id}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-surface-700 bg-surface-850 px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-slate-100">{p.id}</span>
                  {llm.default === p.id && (
                    <span className="rounded bg-brand/20 px-1.5 py-0.5 text-[10px] text-brand">
                      默认
                    </span>
                  )}
                <div className="ml-auto flex items-center gap-1.5">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] ${
                      p.kind === 'claude-cli'
                        ? 'bg-emerald-500/15 text-emerald-300'
                        : 'bg-slate-500/15 text-slate-400'
                    }`}
                  >
                    {p.kind === 'claude-cli' ? '订阅' : 'API'}
                  </span>
                  {p.kind !== 'claude-cli' && (
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] ${
                        p.has_api_key
                          ? 'bg-emerald-500/15 text-emerald-300'
                          : 'bg-amber-500/15 text-amber-300'
                      }`}
                    >
                      {p.has_api_key ? `Key 已配置（${p.key_preview}）` : '无 Key'}
                    </span>
                  )}
                </div>
                </div>
                <p className="mt-1 truncate text-[11px] text-slate-500">
                  {p.kind === 'claude-cli'
                    ? '本机 Claude 订阅（claude 命令行），零 API 费用；TA 深度分析自动回退 API'
                    : `${p.base_url} · model: ${p.model}`}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                {llm.default !== p.id && (
                  <button
                    onClick={() => setDefault(p.id)}
                    disabled={busy === `default-${p.id}`}
                    title="切为默认后：纪要汇总/本地资料摘要/AI 对话/深度研究/深度分析 全部立即改用此接口"
                    className="rounded-lg border border-brand/50 bg-brand/10 px-2.5 py-1 text-xs text-brand hover:bg-brand/20 disabled:opacity-50"
                  >
                    {busy === `default-${p.id}` ? '切换中…' : '设为默认'}
                  </button>
                )}
                <button
                  onClick={() => remove(p.id)}
                  disabled={busy === p.id}
                  className="text-xs text-slate-400 hover:text-rose-300 disabled:opacity-50"
                >
                  移除
                </button>
              </div>
            </section>
          ))}
        </div>
      )}

      {/* 快速预设 */}
      <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <h2 className="text-sm font-semibold text-slate-100">快速预设</h2>
        <p className="mt-1 text-[11px] text-slate-500">
          套餐地址与 Key 不通用：火山 Coding Plan / Agent Plan 各用套餐专属 Key（不能用方舟普通
          按量 Key）；MiMo Token Plan 也要用套餐 Key，不能用按量 Key。
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {PRESETS.filter((p) => isAdmin || p.kind !== 'claude-cli').map((p) => (
            <button
              key={p.label}
              onClick={() => applyPreset(p)}
              className={`rounded-lg border px-3 py-1.5 text-xs transition-colors ${
                activePreset === p.label
                  ? 'border-brand/50 bg-brand/10 text-brand'
                  : 'border-surface-600 text-slate-300 hover:border-brand/40 hover:text-brand'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* 配置表单 */}
        <div className="mt-4 space-y-3">
          {/* 通道类型 */}
          <div className="flex flex-wrap gap-4 rounded-lg border border-surface-700 bg-surface-800/50 px-3 py-2.5 text-xs text-slate-300">
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="radio"
                name="llm-kind"
                className="accent-brand"
                checked={form.kind === 'api'}
                onChange={() => update({ kind: 'api' })}
              />
              OpenAI 兼容 API（需网关与 Key）
            </label>
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="radio"
                name="llm-kind"
                className="accent-brand"
                checked={form.kind === 'claude-cli'}
                onChange={() => update({ kind: 'claude-cli' })}
              />
              Claude 订阅（本机命令行，零 API 费用）
            </label>
          </div>
          {form.kind === 'claude-cli' && (
            <p className="rounded-lg border border-emerald-500/25 bg-emerald-500/5 px-3 py-2 text-[11px] leading-relaxed text-slate-400">
              使用本机已 <code className="text-slate-300">claude login</code> 的订阅身份，不消耗 API
              额度；仅在安装了 Claude Code 命令行的机器上可用（云服务器需同样安装登录）。无需填写网关与
              Key。TA 深度分析只认 API 网关，订阅档下会自动回退配置文件中的 API 通道。
            </p>
          )}

          <div className={`grid gap-3 sm:grid-cols-2 ${form.kind === 'claude-cli' ? 'sm:grid-cols-1' : ''}`}>
            <label className="space-y-1.5">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">
                配置名称
              </span>
              <input
                className={inputCls}
                placeholder="default（留空=default）"
                value={form.id}
                onChange={(e) => update({ id: e.target.value })}
              />
            </label>
            {form.kind === 'api' && (
              <label className="space-y-1.5">
                <span className="text-[10px] uppercase tracking-wider text-slate-500">
                  模型 model
                </span>
                <input
                  className={inputCls}
                  placeholder="mimo-v2.6-flash"
                  value={form.model}
                  onChange={(e) => update({ model: e.target.value })}
                />
              </label>
            )}
          </div>

          {form.kind === 'api' && (
            <>
              <label className="block space-y-1.5">
                <span className="text-[10px] uppercase tracking-wider text-slate-500">
                  API 地址 base_url
                </span>
                <input
                  className={inputCls}
                  placeholder="https://api.example.com/v1"
                  value={form.base_url}
                  onChange={(e) => update({ base_url: e.target.value })}
                />
              </label>

              <label className="block space-y-1.5">
                <span className="text-[10px] uppercase tracking-wider text-slate-500">
                  API Key
                </span>
                <div className="relative">
                  <input
                    type={showKey ? 'text' : 'password'}
                    className={`${inputCls} pr-9 font-mono`}
                    placeholder="填入 API Key（新配置必填）"
                    value={form.api_key}
                    onChange={(e) => update({ api_key: e.target.value })}
                  />
                  <button
                    type="button"
                    onClick={() => setShowKey((v) => !v)}
                    tabIndex={-1}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                    aria-label={showKey ? '隐藏 Key' : '显示 Key'}
                  >
                    {showKey ? '🙈' : '👁'}
                  </button>
                </div>
              </label>

              <div className="grid gap-3 sm:grid-cols-2">
                <label className="space-y-1.5">
                  <span className="text-[10px] uppercase tracking-wider text-slate-500">
                    输出上限 max_tokens
                  </span>
                  <input
                    type="number"
                    min={1}
                    className={inputCls}
                    value={form.max_tokens}
                    onChange={(e) => update({ max_tokens: e.target.value })}
                  />
                </label>
                <label className="space-y-1.5">
                  <span className="text-[10px] uppercase tracking-wider text-slate-500">
                    上下文窗口 context_window
                  </span>
                  <input
                    type="number"
                    min={1}
                    className={inputCls}
                    value={form.context_window}
                    onChange={(e) => update({ context_window: e.target.value })}
                  />
                </label>
              </div>
            </>
          )}
        </div>

        {error && (
          <div className="mt-3 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
            {error}
          </div>
        )}
        {testResult && (
          <div
            className={`mt-3 px-3 py-2 text-xs ${
              testResult.ok
                ? 'border border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                : 'border border-amber-500/40 bg-amber-500/10 text-amber-300'
            }`}
          >
            {(() => {
              if (testResult.ok) {
                return testResult.via === 'claude-cli'
                  ? '订阅通道可用 ✓（本机 claude 命令行）'
                  : `连通成功 ✓（model: ${testResult.model}）`
              }
              const code = testResult.code as string | undefined
              if (code === 'cli_not_installed') {
                return '本机未检测到 Claude Code 命令行（功能已就绪，安装并 claude login 后即可用）'
              }
              if (code === 'cli_not_logged_in') {
                return '检测到命令行但订阅未登录，请先在终端执行 claude login'
              }
              return `连接失败：${testResult.error ?? testResult.http_status}`
            })()}
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={makeDefault}
              onChange={(e) => setMakeDefault(e.target.checked)}
              className="accent-brand"
            />
            保存后设为默认（各模块立即切换，无需重启）
          </label>
          <div className="ml-auto flex gap-2">
            <button
              onClick={save}
              disabled={busy === 'save'}
              className="rounded-lg bg-brand/20 px-6 py-2 text-sm font-semibold text-brand hover:bg-brand/30 disabled:opacity-50"
            >
              {busy === 'save' ? '保存中…' : '保存配置'}
            </button>
            <button
              onClick={test}
              disabled={busy === 'test'}
              className="rounded-lg border border-surface-600 px-4 py-2 text-sm text-slate-300 hover:border-brand/50 hover:text-brand disabled:opacity-50"
            >
              {busy === 'test' ? '测试中…' : '测试连接'}
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
