/** 文本清洗工具 */

/**
 * 去掉知识星球内容里的博主信息字样（"调研鹅"等标题行/词）。
 * 含关键词的整行剔除（新闻标题/作者行），再兜底全量替换。
 */
export function cleanBlogger(text?: string | null): string {
  if (!text) return ''
  return text
    .split('\n')
    .map((line) => (line.includes('调研鹅') ? '' : line))
    .filter((l) => l.trim())
    .join('\n')
    .replace(/调研鹅/g, '')
    .trim()
}

/**
 * 报告全文显示用：标题里的"调研鹅纪要汇总"统一显示为"调研纪要汇总"，
 * 其余博主字样仍按 cleanBlogger 规则剔除。
 */
export function cleanReport(text?: string | null): string {
  if (!text) return ''
  return cleanBlogger(text.replace(/调研鹅纪要汇总/g, '调研纪要汇总'))
}