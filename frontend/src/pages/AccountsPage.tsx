import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth'
import { getJson, sendJson } from '../lib/api'
import { Users, KeyRound, Copy, Check } from 'lucide-react'

/**
 * 账号管理页（任务 11，仅管理员）：
 * - 注册密钥卡：可生成多把（分别发给不同人），列表内复制/单独吊销/全部关闭
 * - 用户表：10 项权限开关（管理员行与自己的行禁用，全量 PATCH）、快捷档、
 *   停用/启用、重置密码、删除（均 confirm 防误触）
 */

interface AccountRow {
  id: string
  username: string
  is_admin: boolean
  disabled: boolean
  perms: Record<string, boolean>
  created_at: string
}

const PERMS: { key: string; label: string }[] = [
  { key: 'view_market', label: '看行情' },
  { key: 'view_review', label: '复盘' },
  { key: 'view_news', label: '资讯' },
  { key: 'watchlist', label: '自选' },
  { key: 'ai_chat', label: 'AI 对话' },
  { key: 'data_sources', label: '数据源' },
  { key: 'llm_settings', label: '大模型' },
]

const VIEW_MARKET_ONLY: Record<string, boolean> = { view_market: true }
const ALL_ON: Record<string, boolean> = Object.fromEntries(PERMS.map((p) => [p.key, true]))

export default function AccountsPage() {
  const qc = useQueryClient()
  const { user } = useAuth()
  const meId = user?.id ?? ''

  const q = useQuery({
    queryKey: ['accounts'],
    queryFn: () =>
      getJson<{
        users: AccountRow[]
        registration_key_set: boolean
        registration_keys: { key: string; created_at: string }[]
      }>('/api/accounts'),
    enabled: !!user?.is_admin,
  })

  // 行内复制反馈（哪把密钥刚复制成功）
  const [copiedKey, setCopiedKey] = useState<string | null>(null)

  const users = q.data?.users ?? []
  const keyList = q.data?.registration_keys ?? []
  const keySet = keyList.length > 0

  async function invalidate() {
    await qc.invalidateQueries({ queryKey: ['accounts'] })
  }

  async function keyAction(action: 'generate' | 'revoke_all' | 'revoke', key?: string) {
    if (action === 'revoke' && key &&
        !window.confirm('确认吊销这把密钥？持有者将无法再用它注册，其它密钥不受影响。')) {
      return
    }
    if (action === 'revoke_all' &&
        !window.confirm('确认关闭注册？关闭后任何人都无法注册新账号。')) {
      return
    }
    try {
      await sendJson('POST', '/api/accounts/registration-key',
        action === 'revoke' ? { action, key } : { action })
      await invalidate()
    } catch (e) {
      alert((e as Error).message)
    }
  }

  async function copyKeyValue(k: string) {
    try {
      await navigator.clipboard.writeText(k)
      setCopiedKey(k)
      window.setTimeout(() => setCopiedKey((cur) => (cur === k ? null : cur)), 2000)
    } catch {
      alert('复制失败，请手动选中文本复制')
    }
  }

  async function patchPerms(u: AccountRow, next: Record<string, boolean>) {
    try {
      await sendJson('PATCH', `/api/accounts/${u.id}`, { perms: next })
      await invalidate()
    } catch (e) {
      alert((e as Error).message)
    }
  }

  async function toggleDisabled(u: AccountRow) {
    const next = !u.disabled
    const msg = next
      ? `确认停用账号「${u.username}」？其登录会话将立即失效。`
      : `确认启用账号「${u.username}」？`
    if (!window.confirm(msg)) return
    try {
      await sendJson('PATCH', `/api/accounts/${u.id}`, { disabled: next })
      await invalidate()
    } catch (e) {
      alert((e as Error).message)
    }
  }

  async function resetPassword(u: AccountRow) {
    const pw = window.prompt(`为「${u.username}」设置新密码（至少 8 位）：`)
    if (pw === null) return
    if (pw.length < 8) {
      alert('密码至少 8 位')
      return
    }
    if (!window.confirm(`确认重置「${u.username}」的密码？该账号所有登录会话将失效。`)) return
    try {
      await sendJson('POST', `/api/accounts/${u.id}/reset-password`, { password: pw })
      alert('密码已重置')
    } catch (e) {
      alert((e as Error).message)
    }
  }

  async function removeUser(u: AccountRow) {
    if (!window.confirm(`确认删除账号「${u.username}」？此操作不可恢复。`)) return
    try {
      await sendJson('DELETE', `/api/accounts/${u.id}`)
      await invalidate()
    } catch (e) {
      alert((e as Error).message)
    }
  }

  if (q.isLoading) {
    return <div className="py-20 text-center text-slate-400">加载账号列表…</div>
  }

  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand/15">
          <Users className="h-5 w-5 text-brand" />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-slate-100">账号管理</h1>
          <p className="text-xs text-slate-500">
            账号由注册密钥自助注册；此处逐项开关权限——访客默认只看行情
          </p>
        </div>
      </div>

      {q.isError && (
        <section className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4">
          <p className="text-xs text-rose-300">{(q.error as Error).message}</p>
        </section>
      )}

      {/* 注册密钥（多密钥，2026-11-02 用户拍板"可以生成多个"） */}
      <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-amber-400" />
            <h2 className="text-sm font-semibold text-slate-100">注册密钥</h2>
            <span className={`text-xs ${keySet ? 'text-emerald-400' : 'text-slate-500'}`}>
              {keySet ? `已生成 ${keyList.length} 把` : '未设置'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => void keyAction('generate')}
              className="rounded-md border border-surface-600 px-3 py-1.5 text-xs text-slate-200 hover:border-brand/50"
            >
              生成新密钥
            </button>
            <button
              onClick={() => void keyAction('revoke_all')}
              disabled={!keySet}
              className="rounded-md border border-rose-500/40 px-3 py-1.5 text-xs text-rose-300 hover:bg-rose-500/10 disabled:cursor-not-allowed disabled:opacity-40"
            >
              全部关闭
            </button>
          </div>
        </div>
        {keyList.length > 0 && (
          <ul className="mt-3 space-y-2">
            {keyList.map((k) => (
              <li
                key={k.key}
                className="flex flex-wrap items-center gap-2 rounded-md border border-surface-700 bg-surface-900 px-3 py-2"
              >
                <span className="min-w-0 flex-1 break-all font-mono text-xs text-amber-200">
                  {k.key}
                </span>
                {k.created_at && (
                  <span className="text-[11px] text-slate-500">
                    {k.created_at.slice(0, 16).replace('T', ' ')}
                  </span>
                )}
                <button
                  onClick={() => void copyKeyValue(k.key)}
                  className="flex items-center gap-1 rounded border border-surface-600 px-2 py-1 text-[11px] text-slate-300 hover:border-brand/50"
                >
                  {copiedKey === k.key ? (
                    <><Check className="h-3 w-3" /> 已复制</>
                  ) : (
                    <><Copy className="h-3 w-3" /> 复制</>
                  )}
                </button>
                <button
                  onClick={() => void keyAction('revoke', k.key)}
                  className="rounded border border-rose-500/40 px-2 py-1 text-[11px] text-rose-300 hover:bg-rose-500/10"
                >
                  吊销
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-xs text-slate-500">
          朋友凭任意一把密钥在注册页自助开通账号；可生成多把分别发给不同人，
          某把泄露时单独吊销即可（互不影响）；全部关闭后不能再注册新账号。
        </p>
      </section>

      {/* 用户表 */}
      <section className="rounded-xl border border-surface-700 bg-surface-850 p-4">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-100">用户与权限</h2>
          <span className="text-xs text-slate-500">{users.length} 个账号</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-left text-xs">
            <thead>
              <tr className="border-b border-surface-700 text-[11px] text-slate-500">
                <th className="py-2 pr-3 font-medium">用户名</th>
                <th className="py-2 pr-3 font-medium">创建时间</th>
                {PERMS.map((p) => (
                  <th key={p.key} className="py-2 pr-2 text-center font-medium" title={p.key}>
                    {p.label}
                  </th>
                ))}
                <th className="py-2 pr-3 font-medium">状态</th>
                <th className="py-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const isSelf = u.id === meId
                const permLocked = u.is_admin || isSelf
                const editable = !u.is_admin && !isSelf
                return (
                  <tr key={u.id} className="border-b border-surface-800 last:border-0">
                    <td className="py-2 pr-3 whitespace-nowrap text-slate-200">
                      {u.is_admin && <span title="管理员">👑 </span>}
                      {u.username}
                      {isSelf && <span className="ml-1 text-[10px] text-slate-500">（自己）</span>}
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap text-slate-400">
                      {(u.created_at || '').slice(0, 10)}
                    </td>
                    {PERMS.map((p) => (
                      <td key={p.key} className="py-2 pr-2 text-center">
                        <input
                          type="checkbox"
                          aria-label={`${u.username} ${p.label}`}
                          title={permLocked ? '管理员与自己的权限不可在此修改' : p.label}
                          checked={!!u.perms?.[p.key]}
                          disabled={permLocked}
                          onChange={(e) =>
                            void patchPerms(u, { ...u.perms, [p.key]: e.target.checked })
                          }
                          className="h-3.5 w-3.5 accent-brand"
                        />
                      </td>
                    ))}
                    <td className="py-2 pr-3 whitespace-nowrap">
                      <span className={u.disabled ? 'text-slate-500' : 'text-emerald-400'}>
                        {u.disabled ? '已停用' : '启用'}
                      </span>
                    </td>
                    <td className="py-2 whitespace-nowrap">
                      <div className="flex items-center gap-1.5">
                        {editable && (
                          <>
                            <button
                              onClick={() => void patchPerms(u, VIEW_MARKET_ONLY)}
                              title="仅保留看行情权限"
                              className="rounded border border-surface-600 px-1.5 py-0.5 text-[10px] text-slate-300 hover:border-brand/50"
                            >
                              看行情档
                            </button>
                            <button
                              onClick={() => void patchPerms(u, ALL_ON)}
                              title="开启全部 7 项权限"
                              className="rounded border border-surface-600 px-1.5 py-0.5 text-[10px] text-slate-300 hover:border-brand/50"
                            >
                              全开
                            </button>
                          </>
                        )}
                        <button
                          onClick={() => void toggleDisabled(u)}
                          disabled={isSelf}
                          title={isSelf ? '不能停用自己' : undefined}
                          className="rounded border border-surface-600 px-1.5 py-0.5 text-[10px] text-slate-300 hover:border-amber-500/50 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          {u.disabled ? '启用' : '停用'}
                        </button>
                        <button
                          onClick={() => void resetPassword(u)}
                          className="rounded border border-surface-600 px-1.5 py-0.5 text-[10px] text-slate-300 hover:border-brand/50"
                        >
                          重置密码
                        </button>
                        <button
                          onClick={() => void removeUser(u)}
                          disabled={isSelf}
                          title={isSelf ? '不能删除自己' : undefined}
                          className="rounded border border-rose-500/40 px-1.5 py-0.5 text-[10px] text-rose-300 hover:bg-rose-500/10 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
              {users.length === 0 && (
                <tr>
                  <td colSpan={4 + PERMS.length} className="py-6 text-center text-slate-500">
                    暂无账号
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-[11px] text-slate-600">
          权限开关为整行全量保存；管理员拥有全部权限、自己的权限不可在此修改。
        </p>
      </section>
    </div>
  )
}
