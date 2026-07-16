export const settingsSections = [
  { value: 'account', label: '管理员', description: '应用账号与密码' },
  { value: 'auth', label: '登录态', description: 'Cookie 上传与校验' },
  { value: 'download', label: '下载', description: '预设、并发与命名' },
  { value: 'storage', label: '存储', description: '目录、配额与清理' },
  { value: 'analysis', label: '分析', description: '模型、设备与采样' },
  { value: 'network', label: '网络', description: '超时、限速与间隔' },
  { value: 'privacy', label: '隐私', description: '历史和诊断策略' },
] as const

export type SettingsSection = typeof settingsSections[number]['value']

export function isSettingsSection(value: unknown): value is SettingsSection {
  return typeof value === 'string' && settingsSections.some((section) => section.value === value)
}
