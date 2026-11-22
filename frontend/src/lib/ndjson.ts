/** 从 NDJSON 缓冲区取出完整记录；剩余半包留给下一次网络读取。 */
export function splitNdjson(buf: string): { lines: string[]; rest: string } {
  const parts = buf.split('\n')
  const rest = parts.pop() ?? ''
  return { lines: parts.filter((line) => line.trim().length > 0), rest }
}
