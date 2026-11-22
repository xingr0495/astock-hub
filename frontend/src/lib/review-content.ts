/** 历史复盘隐藏旧版底部数据来源、模型名称等元信息，行业正文保持不变。 */
export function stripReviewSource(content: string): string {
  return content
    .split('\n')
    .filter((line) => !/^\s*>\s*(?:数据来源|.*(?:模型|model|LLM))/i.test(line))
    .join('\n')
    .trimEnd()
}
