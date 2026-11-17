import { useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'

export interface VColumn<Row> {
  key: string
  header: string
  width?: number
  align?: 'left' | 'right' | 'center'
  className?: string
  cell: (row: Row, index: number) => React.ReactNode
}

interface Props<Row> {
  rows: Row[]
  columns: VColumn<Row>[]
  rowHeight?: number
  height?: number
  rowKey: (row: Row, index: number) => string
}

/**
 * 虚拟滚动表格：只渲染可视区行，上万行也流畅。
 * 表头在滚动容器外（独立一行），容器内仅虚拟化数据行。
 */
export default function VirtualTable<Row>({
  rows,
  columns,
  rowHeight = 36,
  height = 460,
  rowKey,
}: Props<Row>) {
  const parentRef = useRef<HTMLDivElement>(null)

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  })

  const gridTemplate = columns
    .map((c) => (c.width ? `${c.width}px` : '1fr'))
    .join(' ')

  const alignJustify = (a?: string) =>
    a === 'right' ? 'justify-end text-right' : a === 'center' ? 'justify-center text-center' : ''

  return (
    <div className="overflow-hidden rounded-lg border border-surface-700" style={{ height }}>
      {/* 表头 */}
      <div
        className="grid bg-surface-800 text-[11px] text-slate-400"
        style={{ gridTemplateColumns: gridTemplate }}
      >
        {columns.map((c) => (
          <div key={c.key} className={`px-3 py-2 font-medium ${alignJustify(c.align)}`}>
            {c.header}
          </div>
        ))}
      </div>

      {/* 虚拟化数据行 */}
      <div ref={parentRef} className="h-[calc(100%-33px)] overflow-auto">
        <div style={{ height: virtualizer.getTotalSize(), position: 'relative', width: '100%' }}>
          {virtualizer.getVirtualItems().map((vi) => {
            const row = rows[vi.index]
            return (
              <div
                key={rowKey(row, vi.index)}
                className="grid border-t border-surface-700/60 text-[12px]"
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  transform: `translateY(${vi.start}px)`,
                  height: rowHeight,
                  width: '100%',
                  gridTemplateColumns: gridTemplate,
                }}
              >
                {columns.map((c) => (
                  <div
                    key={c.key}
                    className={`flex items-center px-3 ${alignJustify(c.align)} ${c.className ?? ''}`}
                  >
                    {c.cell(row, vi.index)}
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
