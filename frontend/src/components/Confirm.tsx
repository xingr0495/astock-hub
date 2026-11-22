import { createRoot } from 'react-dom/client'
import { Modal } from './Modal'

/** 自研确认框（替代 window.confirm）：Promise<boolean>，确认 true / 取消或关闭 false。 */
export function confirmDialog(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    const done = (v: boolean) => {
      root.unmount()
      host.remove()
      resolve(v)
    }
    root.render(
      <Modal onClose={() => done(false)} ariaLabel="确认操作">
        <div className="p-5">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">{message}</p>
          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => done(false)}
              className="rounded-md border border-surface-600 px-3 py-1.5 text-xs text-slate-300 hover:bg-surface-700"
            >
              取消
            </button>
            <button
              type="button"
              onClick={() => done(true)}
              className="rounded-md bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-500"
            >
              确认
            </button>
          </div>
        </div>
      </Modal>,
    )
  })
}
