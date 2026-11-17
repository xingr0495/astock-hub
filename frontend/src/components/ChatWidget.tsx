import { Fragment, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { MessageCircle, Send, X } from 'lucide-react'

/**
 * 看板 AI 对话悬浮框（T8；2026-11-01 升级流式 + 页面感知）。
 *
 * - 流式：POST /api/chat/stream（NDJSON 事件流），delta 逐字渲染，
 *   done 带 asof/used_blocks；error 收尾（midway 保留已生成部分）
 * - 页面感知：随请求上送当前页面名 + 个股页标的（?code=），模型知道"该股"指哪只
 * - 分组预设 + 涨跌带符号百分比自动红涨绿跌（与看板惯例统一）
 * - 会话无状态：前端只保存并上送最近 10 轮（20 条）历史
 * - LLM 失败时后端以 error 事件收尾（人话），照常展示
 * - 答案用 react-markdown + remark-gfm 渲染（默认不渲染原始 HTML，安全）
 * - asof/used_blocks 随消息对象存储，答案底部渲染弱化样式溯源行（T10）
 */

type Role = 'user' | 'assistant' | 'error'

interface ChatMessage {
  id: number
  role: Role
  content: string
  /** 助手消息：后端返回的 as-of 日期（答案底部溯源行渲染） */
  asof?: string
  /** 助手消息：本次答案引用的数据块（答案底部溯源行渲染） */
  used_blocks?: string[]
  /** 发送中占位（"思考中…"），不进入 history */
  pending?: boolean
}

interface ChatRequestBody {
  message: string
  history?: { role: 'user' | 'assistant'; content: string }[]
  page?: string
  symbol?: string
}

/** 流式总超时：首 token 通常 <10s，长答案流完可能超过 60s，放宽到 120s */
const TIMEOUT_MS = 120_000
/** 前端上送的历史：最近 10 轮（20 条），与后端截断口径对齐但更收敛 */
const HISTORY_MAX = 20
/** 与后端 _CHAT_MAX_CHARS 对齐的输入上限 */
const MAX_CHARS = 2000

/** 路由 → 页面名（页面感知改进 2；个股页从 ?code= 取标的） */
const PAGE_NAMES: Record<string, string> = {
  '/': '个股分析',
  '/dashboard': '实时看板',
  '/watchlist': '自选股',
  '/sector': '板块分析',
  '/review': '大盘复盘',
  '/backtest': '回测',
  '/pool': '产业链选股',
  '/news': '产业资讯',
  '/settings/notify': '推送设置',
  '/settings/sources': '数据源',
  '/settings/llm': '大模型',
}

/** 预设快捷问题（分组展示；send=点击直接发送；fill=只填入输入框待用户补全） */
interface PresetQuestion {
  label: string
  send?: string
  fill?: string
}

const PRESET_GROUPS: { group: string; items: PresetQuestion[] }[] = [
  {
    group: '大盘',
    items: [
      { label: '今日模式判定', send: '今天模式怎么样' },
      { label: '主线是谁', send: '今天的主线是谁' },
      { label: '今天能出手吗', send: '今天能出手吗' },
    ],
  },
  {
    group: '个股',
    items: [
      { label: '分析XX', fill: '分析 ' },
      { label: '符合趋势手册吗', send: '符合趋势手册吗' },
    ],
  },
]

/** 涨跌带符号百分比自动红涨绿跌（改进 3，与看板惯例统一） */
const PCT_RE = /([+-]\d+(?:\.\d+)?%)/g

function colorizePct(children: React.ReactNode): React.ReactNode {
  if (typeof children === 'string') {
    const parts = children.split(PCT_RE)
    if (parts.length === 1) return children
    return parts.map((part, i) => {
      if (/^[+-]\d+(?:\.\d+)?%$/.test(part)) {
        const up = part.startsWith('+')
        return (
          <span key={i} className={up ? 'text-rose-400' : 'text-emerald-400'}>
            {part}
          </span>
        )
      }
      return <Fragment key={i}>{part}</Fragment>
    })
  }
  if (Array.isArray(children)) {
    return children.map((c, i) => <Fragment key={i}>{colorizePct(c)}</Fragment>)
  }
  if (children && typeof children === 'object' && 'props' in (children as object)) {
    const el = children as React.ReactElement<{ children?: React.ReactNode }>
    if (el.props?.children !== undefined) {
      return { ...el, props: { ...el.props, children: colorizePct(el.props.children) } }
    }
  }
  return children
}

/** 答案 markdown 渲染（GFM：表格/删除线/任务列表等；文本类组件套涨跌着色） */
function MarkdownBody({ text }: { text: string }) {
  return (
    <div className="text-[13px] leading-relaxed break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="my-1.5 first:mt-0 last:mb-0">{colorizePct(children)}</p>,
          h1: ({ children }) => <h3 className="my-2 text-sm font-semibold text-slate-100 first:mt-0">{colorizePct(children)}</h3>,
          h2: ({ children }) => <h3 className="my-2 text-sm font-semibold text-slate-100 first:mt-0">{colorizePct(children)}</h3>,
          h3: ({ children }) => <h4 className="my-1.5 text-[13px] font-semibold text-slate-100 first:mt-0">{colorizePct(children)}</h4>,
          h4: ({ children }) => <h4 className="my-1.5 text-[13px] font-semibold text-slate-200 first:mt-0">{colorizePct(children)}</h4>,
          ul: ({ children }) => <ul className="my-1 list-disc pl-5 space-y-0.5">{children}</ul>,
          ol: ({ children }) => <ol className="my-1 list-decimal pl-5 space-y-0.5">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{colorizePct(children)}</li>,
          strong: ({ children }) => <strong className="font-semibold text-slate-100">{colorizePct(children)}</strong>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer" className="text-brand underline underline-offset-2">
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="my-1.5 border-l-2 border-surface-600 pl-2 text-slate-400">{children}</blockquote>
          ),
          hr: () => <hr className="my-2 border-surface-700" />,
          table: ({ children }) => (
            <div className="my-2 overflow-x-auto">
              <table className="w-full border-collapse text-xs">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-surface-600 bg-surface-800 px-2 py-1 text-left font-medium text-slate-300">
              {colorizePct(children)}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-surface-700 px-2 py-1 text-slate-300">{colorizePct(children)}</td>
          ),
          code: ({ children, className }) => (
            <code className={`rounded bg-surface-900 px-1 py-0.5 font-mono text-[11px] ${className ?? ''}`}>
              {children}
            </code>
          ),
          pre: ({ children }) => (
            <pre className="my-1.5 overflow-x-auto rounded-lg bg-surface-900 p-2 text-[11px]">{children}</pre>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}

export default function ChatWidget() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)

  const idRef = useRef(0)
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // 页面感知（改进 2）：当前路由名 + 个股页标的（?code=）
  const location = useLocation()
  const pageName = PAGE_NAMES[location.pathname] ?? ''
  const pageSymbol = new URLSearchParams(location.search).get('code') ?? undefined

  // 打开时聚焦输入框
  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  // ESC 关闭面板
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  // 新消息自动滚到底
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  const nextId = () => ++idRef.current

  /** 用新消息替换指定 id（成功/失败都走这里收尾占位气泡） */
  const replaceMessage = (id: number, patch: Partial<ChatMessage> & { role: Role }) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)))
  }

  /**
   * 发送一条消息（流式，改进 1）。预设 chip 点击时传 textOverride 直接发送
   * （DRY：请求/超时/history 逻辑只有这一处）；回车/发送按钮走默认读 input state。
   */
  async function send(textOverride?: string) {
    const text = (textOverride ?? input).trim()
    if (!text || sending) return
    if (text.length > MAX_CHARS) {
      // 不发请求，直接补一条错误提示（不入 history）
      const errId = nextId()
      setMessages((prev) => [
        ...prev,
        { id: errId, role: 'error', content: `消息过长（${text.length} 字，上限 ${MAX_CHARS} 字），请精简后重试` },
      ])
      return
    }

    // 历史 = 此刻之前的有效消息（user/assistant、非占位）最近 20 条，随请求上送
    const history = messages
      .filter((m) => (m.role === 'user' || m.role === 'assistant') && !m.pending)
      .slice(-HISTORY_MAX)
      .map((m) => ({ role: m.role as 'user' | 'assistant', content: m.content }))

    const userId = nextId()
    const pendingId = nextId()
    if (textOverride === undefined) setInput('') // chip 直发不动输入框草稿
    setSending(true)
    setMessages((prev) => [
      ...prev,
      { id: userId, role: 'user', content: text },
      { id: pendingId, role: 'assistant', content: '', pending: true },
    ])

    const ctrl = new AbortController()
    let timedOut = false
    const timer = window.setTimeout(() => {
      timedOut = true
      ctrl.abort()
    }, TIMEOUT_MS)

    try {
      const res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text, history, page: pageName, symbol: pageSymbol,
        } satisfies ChatRequestBody),
        signal: ctrl.signal,
      })
      if (!res.ok || !res.body) {
        // 400 超长 / 422 空消息：展示后端中文 error
        let msg = `请求失败（HTTP ${res.status}），请稍后再试`
        try {
          const data = (await res.json()) as { error?: string }
          if (data?.error) msg = data.error
        } catch { /* 非 JSON 响应体（代理错误页等） */ }
        replaceMessage(pendingId, { role: 'error', pending: false, content: msg })
        return
      }

      // NDJSON 事件流：delta × N → done | error
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let acc = ''
      let done = false
      while (!done) {
        const { done: rdDone, value } = await reader.read()
        if (rdDone) break
        buf += decoder.decode(value, { stream: true })
        let nl: number
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim()
          buf = buf.slice(nl + 1)
          if (!line) continue
          let ev: { type?: string; text?: string; asof?: string; used_blocks?: string[]; message?: string }
          try {
            ev = JSON.parse(line)
          } catch {
            continue // 半行/脏行跳过
          }
          if (ev.type === 'delta' && ev.text) {
            acc += ev.text
            replaceMessage(pendingId, { role: 'assistant', content: acc, pending: true })
          } else if (ev.type === 'done') {
            replaceMessage(pendingId, {
              role: 'assistant', content: acc || '（未收到有效回答，请重试）',
              asof: ev.asof, used_blocks: ev.used_blocks, pending: false,
            })
            done = true
          } else if (ev.type === 'error') {
            if (acc) {
              // midway：保留已生成部分，追加错误提示行
              replaceMessage(pendingId, {
                role: 'assistant', content: `${acc}\n\n---\n⚠️ ${ev.message ?? '输出中断'}`,
                pending: false,
              })
            } else {
              replaceMessage(pendingId, {
                role: 'error', pending: false,
                content: ev.message ?? '模型暂时不可用，请稍后再试。',
              })
            }
            done = true
          }
        }
      }
      if (!done) {
        // 流被截断（服务端断连且未发 error 事件）
        replaceMessage(pendingId, {
          role: 'assistant',
          content: acc ? `${acc}\n\n---\n⚠️ 连接中断，以上是已生成的部分。` : '连接中断，请重试。',
          pending: false,
        })
      }
    } catch (e) {
      const err = e as Error
      const content = timedOut
        ? '模型暂时繁忙，超过 120 秒未完成，请稍后再试。'
        : err.name === 'AbortError'
          ? '请求已取消，请重试。'
          : '连不上面板服务（网络错误），请确认服务已启动后重试。'
      replaceMessage(pendingId, { role: 'error', pending: false, content })
    } finally {
      window.clearTimeout(timer)
      setSending(false)
    }
  }

  /** 预设 chip 点击：有 fill 只填输入框并聚焦（用户补标的）；否则直接发送 */
  function handlePreset(p: PresetQuestion) {
    if (p.fill !== undefined) {
      setInput(p.fill)
      // 等受控值落进 DOM 再聚焦并把光标放到末尾，方便接着输入
      requestAnimationFrame(() => {
        const el = inputRef.current
        if (el) {
          el.focus()
          el.setSelectionRange(el.value.length, el.value.length)
        }
      })
      return
    }
    if (p.send) void send(p.send)
  }

  if (!open) {
    return (
      <button
        type="button"
        aria-label="打开 AI 问答"
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-50 flex h-12 w-12 items-center justify-center rounded-full border border-surface-600 bg-surface-800 text-brand shadow-xl transition-colors hover:bg-surface-700 hover:text-brand-dim"
      >
        <MessageCircle size={22} />
      </button>
    )
  }

  return (
    <div
      role="dialog"
      aria-label="AI 问答"
      className="fixed bottom-6 right-6 z-50 flex h-[min(560px,70vh)] w-[400px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-xl border border-surface-700 bg-surface-850 shadow-2xl max-md:inset-x-2 max-md:bottom-2 max-md:w-auto max-md:h-[calc(100dvh-1rem)]"
    >
      {/* 头部 */}
      <div className="flex items-center justify-between border-b border-surface-700 bg-surface-800 px-4 py-2.5">
        <span className="text-sm font-semibold text-slate-200">AI 问答</span>
        <button
          type="button"
          aria-label="关闭"
          onClick={() => setOpen(false)}
          className="rounded p-1 text-slate-400 transition-colors hover:bg-surface-700 hover:text-slate-200"
        >
          <X size={16} />
        </button>
      </div>

      {/* 消息流 */}
      <div ref={listRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
            <div className="text-xs text-slate-500">输入你的问题开始对话，答案基于当日看板数据生成，仅供参考</div>
            {/* 分组预设（改进 3）：大盘/个股两组；"分析XX" 只填入输入框 */}
            <div className="w-full space-y-2.5">
              {PRESET_GROUPS.map((g) => (
                <div key={g.group}>
                  <div className="mb-1 text-[10px] uppercase tracking-wider text-slate-600">{g.group}</div>
                  <div className="flex flex-wrap justify-center gap-1.5">
                    {g.items.map((p) => (
                      <button
                        key={p.label}
                        type="button"
                        disabled={sending}
                        onClick={() => handlePreset(p)}
                        className="rounded-full border border-surface-600 bg-surface-800 px-2.5 py-1 text-[11px] leading-none text-slate-300 transition-colors hover:border-brand/50 hover:bg-surface-700 hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => {
          if (m.role === 'error') {
            return (
              <div
                key={m.id}
                className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs leading-relaxed text-red-300"
              >
                {m.content}
              </div>
            )
          }
          if (m.role === 'user') {
            return (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-xl rounded-tr-sm bg-brand/15 px-3 py-2 text-[13px] leading-relaxed text-slate-100">
                  {m.content}
                </div>
              </div>
            )
          }
          // assistant
          if (m.pending) {
            return (
              <div key={m.id} className="flex justify-start">
                <div className="rounded-xl rounded-tl-sm border border-surface-700 bg-surface-800 px-3 py-2 text-[13px] text-slate-400 animate-pulse">
                  思考中…
                </div>
              </div>
            )
          }
          // assistant（正常回答）：底部溯源行 = 数据基准日 asof + used_blocks（T10）
          // 容错：asof / used_blocks 任一为空则只显示另一半，都为空不渲染空行
          const blocks = (Array.isArray(m.used_blocks) ? m.used_blocks : [])
            .map((s) => String(s).trim())
            .filter(Boolean)
          const asof = (m.asof ?? '').trim()
          const showSource = Boolean(asof) || blocks.length > 0
          return (
            <div key={m.id} className="flex justify-start">
              <div className="max-w-[92%] rounded-xl rounded-tl-sm border border-surface-700 bg-surface-800 px-3 py-2 text-slate-300">
                <MarkdownBody text={m.content} />
                {showSource && (
                  <div className="mt-2 flex flex-wrap items-center gap-x-1.5 border-t border-surface-700 pt-1.5 text-[10px] leading-relaxed text-slate-500">
                    {asof && <span>基准日 {asof}</span>}
                    {asof && blocks.length > 0 && <span className="text-slate-600">·</span>}
                    {blocks.length > 0 && <span>依据：{blocks.join(' · ')}</span>}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* 输入区 */}
      <div className="border-t border-surface-700 bg-surface-800 px-3 py-2.5">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              // Enter 发送，Shift+Enter 换行；IME 组字中的 Enter 不发送（中文输入法）
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                void send()
              }
            }}
            rows={2}
            placeholder="输入你的问题，Enter 发送"
            className="min-h-[44px] flex-1 resize-none rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-[13px] leading-relaxed text-slate-200 placeholder:text-slate-500 focus:border-brand/60 focus:outline-none"
          />
          <button
            type="button"
            aria-label="发送"
            disabled={sending || !input.trim()}
            onClick={() => void send()}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand text-surface-900 transition-colors hover:bg-brand-dim disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Send size={16} />
          </button>
        </div>
        <div className="mt-1 flex items-center justify-between text-[10px] text-slate-500">
          <span>Enter 发送 · Shift+Enter 换行</span>
          {input.length > MAX_CHARS - 200 && (
            <span className={input.length > MAX_CHARS ? 'text-red-400' : ''}>
              {input.length}/{MAX_CHARS}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
