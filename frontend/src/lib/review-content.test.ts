import test from 'node:test'
import assert from 'node:assert/strict'
import { stripReviewSource } from './review-content.ts'

test('历史复盘隐藏底部数据来源，保留风险提示和正文', () => {
  const input = '### 八、风险提示\n\n- 注意仓位\n\n> 数据来源：a-stock-data + hithink | 2026-11-19\n'
  assert.equal(stripReviewSource(input), '### 八、风险提示\n\n- 注意仓位')
})

test('正文中的数据说明不误删', () => {
  const input = '数据来源口径需交叉验证，但不是底部来源行'
  assert.equal(stripReviewSource(input), input)
})

test('模型名称等元信息也隐藏，行业正文不受影响', () => {
  const input = 'AI 产业链继续走强\n\n> 模型：mimo-v2.6-flash\n> 数据来源：内部框架'
  assert.equal(stripReviewSource(input), 'AI 产业链继续走强')
})
