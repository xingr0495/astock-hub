import { useEffect, useRef, type ReactNode } from 'react'

/**
 * 无障碍模态对话框原语（移植自 tickflow，主题 class 适配本项目）。
 *
 * - role="dialog" + aria-modal；ESC 关闭；点击遮罩关闭（防拖选误关）
 * - 打开时焦点移入，Tab 焦点陷阱，关闭后焦点还原
 *
 * 视觉：居中遮罩 + 面板容器；尺寸/样式用 panelClassName 定制。
 */
export interface ModalProps {
  onClose: () => void
  children: ReactNode
  labelledBy?: string
  ariaLabel?: string
  panelClassName?: string
  overlayClassName?: string
  initialFocusRef?: React.RefObject<HTMLElement>
  closeOnBackdrop?: boolean
}

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function Modal({
  onClose,
  children,
  labelledBy,
  ariaLabel,
  panelClassName = 'w-[92vw] max-w-lg rounded-xl border border-surface-700 bg-surface-850 shadow-2xl',
  overlayClassName = 'fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm',
  initialFocusRef,
  closeOnBackdrop = true,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const mouseDownOnBackdrop = useRef(false)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const prevActive = document.activeElement as HTMLElement | null

    const focusFirst = () => {
      if (initialFocusRef?.current) {
        initialFocusRef.current.focus()
        return
      }
      const panel = panelRef.current
      if (!panel) return
      const first = panel.querySelector<HTMLElement>(FOCUSABLE)
      ;(first ?? panel).focus()
    }
    const raf = requestAnimationFrame(focusFirst)

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCloseRef.current()
        return
      }
      if (e.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
        .filter((el) => el.offsetParent !== null || el === document.activeElement)
      if (nodes.length === 0) {
        e.preventDefault()
        panel.focus()
        return
      }
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      const active = document.activeElement as HTMLElement | null
      if (e.shiftKey) {
        if (active === first || !panel.contains(active)) {
          e.preventDefault()
          last.focus()
        }
      } else if (active === last || !panel.contains(active)) {
        e.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('keydown', onKeyDown, true)
      prevActive?.focus?.()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div
      className={overlayClassName}
      onMouseDown={(e) => {
        mouseDownOnBackdrop.current = e.target === e.currentTarget
      }}
      onClick={
        closeOnBackdrop
          ? (e) => {
              if (mouseDownOnBackdrop.current && e.target === e.currentTarget) onClose()
            }
          : undefined
      }
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : ariaLabel}
        tabIndex={-1}
        className={`max-h-[90vh] overflow-auto outline-none ${panelClassName}`}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  )
}
