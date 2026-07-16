export const settingsSections = [
  { value: 'account', label: '管理员', pageTitle: '管理员设置', description: '应用账号与密码' },
  { value: 'auth', label: '登录态', pageTitle: '登录态设置', description: 'Cookie 上传与校验' },
  { value: 'download', label: '下载', pageTitle: '下载设置', description: '预设、并发与命名' },
  { value: 'storage', label: '存储', pageTitle: '存储设置', description: '目录、配额与清理' },
  { value: 'analysis', label: '分析', pageTitle: '分析设置', description: '模型、设备与采样' },
  { value: 'network', label: '网络', pageTitle: '网络设置', description: '超时、限速与间隔' },
  { value: 'privacy', label: '隐私', pageTitle: '隐私设置', description: '历史和诊断策略' },
] as const

export type SettingsSection = typeof settingsSections[number]['value']

export function isSettingsSection(value: unknown): value is SettingsSection {
  return typeof value === 'string' && settingsSections.some((section) => section.value === value)
}

export function settingsSectionPath(section: SettingsSection): string {
  return `/settings/${section}`
}
