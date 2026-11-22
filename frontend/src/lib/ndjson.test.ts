import test from 'node:test'
import assert from 'node:assert/strict'
import { splitNdjson } from './ndjson.ts'

test('NDJSON 按真实换行拆记录，不误切 JSON 字符串里的转义换行', () => {
  const input = '{"type":"delta","text":"第一段\\n第二段"}\n{"type":"done"}\n'
  const { lines, rest } = splitNdjson(input)
  assert.equal(lines.length, 2)
  assert.deepEqual(JSON.parse(lines[0]), { type: 'delta', text: '第一段\n第二段' })
  assert.deepEqual(JSON.parse(lines[1]), { type: 'done' })
  assert.equal(rest, '')
})

test('网络包从多字节字符中间切开时保留未完整部分', () => {
  const whole = '{"type":"delta","text":"中文"}\n'
  const first = whole.slice(0, 12)
  const a = splitNdjson(first)
  assert.equal(a.lines.length, 0)
  const b = splitNdjson(a.rest + whole.slice(12))
  assert.equal(b.lines.length, 1)
})
