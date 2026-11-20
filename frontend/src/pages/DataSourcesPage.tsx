import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getJson, postJson, deleteJson } from '../lib/api'

/** 可配置的行情数据源目录（id/名称/说明/是否需要key；defaultBase 预填官方地址） */
const SOURCE_CATALOG = [
  { id: 'tencent', name: '腾讯财经', needKey: false,
    desc: '免费、无需凭证，默认兜底行情源' },
  { id: 'tushare', name: 'Tushare', needKey: true, testable: true,
    defaultBase: 'https://api.tushare.pro',
    desc: '付费（有免费额度），历史行情/财务/资金面，token 在 tushare.pro 获取。测试连接会真实调用一次交易日历接口验证 token' },
  { id: 'hithink', name: '同花顺 hithink', needKey: false,
    desc: 'hithink-finance CLI（同花顺同源），需本地安装并登录' },
  { id: 'tdxquant', name: '通达信量化 TdxQuant', needKey: true, testable: true, hideBase: true,
    desc: '付费（通达信商城数据服务），A股全市场快照/日K/分钟K/五档/除权；Key 为 TDX- 开头，在通达信商城开通数据服务获取。原生协议直连通达信行情服务器，无需接口地址。测试连接约需 30-60 秒' },
  { id: 'tickflow', name: 'TickFlow 在线行情', needKey: true, testable: true,
    defaultBase: 'https://api.tickflow.org',
    desc: '付费在线行情 API（tickflow.org，与旧本地 tickflow 项目无关）：实时行情/日K分钟K/五档/财务三表。Key 在 tickflow.org 控制台一键生成。免费服务无需 Key（free-api.tickflow.org，仅历史日K）。测试连接会真实调用一次交易所列表接口' },
]

interface SourceState {
  has_api_key: boolean
  api_base_url?: string
  key_preview?: string
}
interface CredView {
  sources: Record<string, SourceState>
}

export default function DataSourcesPage() {
  const qc = useQueryClient()
  const credQuery = useQuery({
    queryKey: ['credentials'],
    queryFn: () => getJson<CredView>('/api/credentials'),
  })
  const configured = credQuery.data?.sources ?? {}
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [testMsg, setTestMsg] = useState<Record<string, { ok: boolean; msg: string }>>({})

  // 每个源的草稿输入（base 初始化优先级：已保存 > 目录预填官方地址 > 空）
  const [drafts, setDrafts] = useState<Record<string, { key: string; base: string }>>(
    {},
  )
  const defaultBase = (id: string) =>
    SOURCE_CATALOG.find((c) => c.id === id && 'defaultBase' in c &&
      (c as { defaultBase?: string }).defaultBase)?.defaultBase ?? ''
  const draft = (id: string) =>
    drafts[id] ?? { key: '', base: configured[id]?.api_base_url ?? defaultBase(id) }
  const setDraft = (id: string, patch: Partial<{ key: string; base: string }>) =>
    setDrafts((d) => ({ ...d, [id]: { ...draft(id), ...patch } }))

  async function save(id: string) {
    const d = draft(id)
    setBusy(id); setError(null)
    try {
      const payload: Record<string, unknown> = { id }
      // 只有用户填了新 key 才传（空串=清除）；未改动则不传
      if (d.key) payload.api_key = d.key
      if (d.base) payload.api_base_url = d.base
      await postJson('/api/credentials/source', payload)
      setDraft(id, { key: '' })
      qc.invalidateQueries({ queryKey: ['credentials'] })
    } catch (e) {
      setError(`保存失败：${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  async function remove(id: string) {
    setBusy(id)
    try {
      await deleteJson(`/api/credentials/source?id=${id}`)
      qc.invalidateQueries({ queryKey: ['credentials'] })
    } finally {
      setBusy(null)
    }
  }

  async function test(id: string) {
    // 用表单草稿里的候选 Key 测试（未填则测已保存的）；TdxQuant 子进程探测约 30-60s
    setBusy(`test-${id}`)
    const pending = id === 'tdxquant' ? '探测中（原生握手约 30-60 秒）…' : '探测中…'
    setTestMsg((m) => ({ ...m, [id]: { ok: true, msg: pending } }))
    try {
      const payload: Record<string, unknown> = { kind: 'source', id }
      const d = draft(id)
      if (d.key) payload.api_key = d.key
      const r = await postJson<{ ok: boolean; message?: string; error?: string }>(
        '/api/credentials/test', payload)
      setTestMsg((m) => ({
        ...m,
        [id]: { ok: !!r.ok, msg: r.message ?? r.error ?? (r.ok ? 'ok' : '失败') },
      }))
    } catch (e) {
      setTestMsg((m) => ({ ...m, [id]: { ok: false, msg: (e as Error).message } }))
    } finally {
      setBusy(null)
    }
  }

  const cards = useMemo(() => SOURCE_CATALOG, [])
  const freeCards = cards.filter((s) => !s.needKey)
  const paidCards = cards.filter((s) => s.needKey)

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-slate-100">数据源</h1>
        <p className="mt-1 text-xs text-slate-500">
          在此填入各行情源的 API Key，保存后即时生效，无需手动编辑配置文件。
          免费源默认可用，填入付费源可解锁更高质量数据。
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </div>
      )}

      {/* 免费源：统一一张卡 */}
      <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-100">免费数据源</h2>
          <span className="ml-auto rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-300">
            默认可用
          </span>
        </div>
        <div className="mt-3 divide-y divide-surface-700/60">
          {freeCards.map((s) => (
            <div key={s.id} className="flex items-start gap-2 py-2 first:pt-0 last:pb-0">
              <div className="min-w-0 flex-1">
                <div className="text-xs font-medium text-slate-200">{s.name}</div>
                <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500">{s.desc}</p>
              </div>
              <span className="mt-0.5 shrink-0 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-300">
                已就绪
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* 付费源：各自一卡 */}
      <div className="grid gap-3 md:grid-cols-2">
        {paidCards.map((s) => {
          const st = configured[s.id]
          const d = draft(s.id)
          const isConfigured = st?.has_api_key || s.id === 'tencent' || s.id === 'hithink'
          return (
            <section
              key={s.id}
              className="rounded-xl border border-surface-700 bg-surface-850 p-4"
            >
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold text-slate-100">{s.name}</h2>
                <span
                  className={`ml-auto rounded px-1.5 py-0.5 text-[10px] ${
                    isConfigured
                      ? 'bg-emerald-500/15 text-emerald-300'
                      : 'bg-slate-600/30 text-slate-400'
                  }`}
                >
                  {isConfigured ? '已就绪' : '未配置'}
                </span>
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
                {s.desc}
              </p>

              {s.needKey && (
                <div className="mt-3 space-y-2">
                  <input
                    type="password"
                    value={d.key}
                    placeholder={
                      st?.key_preview ? `已配置（${st.key_preview}），留空不改` : '填入 API Key / Token'
                    }
                    onChange={(e) => setDraft(s.id, { key: e.target.value })}
                    className="w-full rounded-lg border border-surface-600 bg-surface-800 px-2.5 py-1.5 text-xs text-slate-200 placeholder:text-slate-600"
                  />
                  <input
                    type="text"
                    value={d.base}
                    disabled={(s as { hideBase?: boolean }).hideBase}
                    placeholder={
                      (s as { defaultBase?: string }).defaultBase
                        ? `接口地址已预填官方默认（${(s as { defaultBase?: string }).defaultBase}），可改用自建代理`
                        : '接口 base_url（可选，默认用官方地址）'
                    }
                    onChange={(e) => setDraft(s.id, { base: e.target.value })}
                    className="w-full rounded-lg border border-surface-600 bg-surface-800 px-2.5 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 disabled:opacity-40"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => save(s.id)}
                      disabled={busy === s.id}
                      className="rounded-lg bg-brand/20 px-3 py-1 text-xs text-brand hover:bg-brand/30 disabled:opacity-50"
                    >
                      {busy === s.id ? '保存中…' : '保存'}
                    </button>
                    {(s as { testable?: boolean }).testable && (
                      <button
                        onClick={() => test(s.id)}
                        disabled={busy === `test-${s.id}`}
                        title="用表单里的 Key（未填则用已保存的）做一次真实连通探测"
                        className="rounded-lg border border-surface-600 px-3 py-1 text-xs text-slate-300 hover:border-brand/50 hover:text-brand disabled:opacity-50"
                      >
                        {busy === `test-${s.id}` ? '探测中…' : '测试连接'}
                      </button>
                    )}
                    {st?.has_api_key && (
                      <button
                        onClick={() => remove(s.id)}
                        className="rounded-lg px-2 py-1 text-xs text-slate-400 hover:text-rose-300"
                      >
                        移除凭证
                      </button>
                    )}
                  </div>
                  {testMsg[s.id] && (
                    <p
                      className={`mt-2 rounded-md border px-2.5 py-1.5 text-[11px] ${
                        testMsg[s.id].ok
                          ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                          : busy === `test-${s.id}`
                            ? 'border-surface-600 bg-surface-800 text-slate-400'
                            : 'border-rose-500/30 bg-rose-500/10 text-rose-300'
                      }`}
                    >
                      {testMsg[s.id].msg}
                    </p>
                  )}
                </div>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}
