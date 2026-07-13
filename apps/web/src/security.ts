export const IMPORT_LIMITS = {
  maxFileBytes: 5 * 1024 * 1024,
  maxDepth: 20,
  maxArrayLength: 10_000,
  maxObjectKeys: 5_000,
  maxStringLength: 20_000,
} as const

const blockedKeys = new Set(['__proto__', 'prototype', 'constructor'])
const secretKey =
  /(?:secret|password|passwd|api[_-]?key|access[_-]?token|private[_-]?key|credential)/i

export class ImportError extends Error {
  constructor(
    message: string,
    public readonly code: 'size' | 'syntax' | 'schema' | 'limits' | 'incompatible',
  ) {
    super(message)
  }
}

export function sanitizeJson(value: unknown, depth = 0): unknown {
  if (depth > IMPORT_LIMITS.maxDepth) throw new ImportError('JSON 对象嵌套过深', 'limits')
  if (typeof value === 'string') {
    if (value.length > IMPORT_LIMITS.maxStringLength)
      throw new ImportError('JSON 包含超长文本字段', 'limits')
    return value
  }
  if (value === null || typeof value === 'boolean' || typeof value === 'number') return value
  if (Array.isArray(value)) {
    if (value.length > IMPORT_LIMITS.maxArrayLength)
      throw new ImportError('JSON 数组超过 10,000 项限制', 'limits')
    return value.map((item) => sanitizeJson(item, depth + 1))
  }
  if (typeof value === 'object') {
    const input = value as Record<string, unknown>
    const keys = Object.keys(input)
    if (keys.length > IMPORT_LIMITS.maxObjectKeys)
      throw new ImportError('JSON 对象字段数量超限', 'limits')
    const output = Object.create(null) as Record<string, unknown>
    for (const key of keys) {
      if (blockedKeys.has(key)) continue
      output[key] = secretKey.test(key) ? '[REDACTED]' : sanitizeJson(input[key], depth + 1)
    }
    return output
  }
  throw new ImportError('JSON 包含不支持的数据类型', 'schema')
}

export function safeExternalUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}
