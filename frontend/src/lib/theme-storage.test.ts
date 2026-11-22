import test from 'node:test'
import assert from 'node:assert/strict'

const listeners = []
globalThis.document = { documentElement: { classList: { toggle() {} } } }
globalThis.window = {
  dispatchEvent() {},
  addEventListener() {},
  removeEventListener() {},
}
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  get() { throw new Error('storage denied') },
})

const { getTheme, setTheme, toggleTheme } = await import('./theme.ts')

test('本地存储不可用时仍能来回切换主题', () => {
  assert.equal(getTheme(), 'dark')
  setTheme('light')
  assert.equal(getTheme(), 'light')
  assert.equal(toggleTheme(), 'dark')
  assert.equal(getTheme(), 'dark')
})

test('能读旧值但写入失败时，不被旧值立即覆盖', () => {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem() { return 'dark' },
      setItem() { throw new Error('write denied') },
    },
  })
  setTheme('light')
  assert.equal(getTheme(), 'light')
  assert.equal(toggleTheme(), 'dark')
})
