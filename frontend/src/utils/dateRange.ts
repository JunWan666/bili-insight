export function endOfLocalDayIso(value: Date): string {
  const end = new Date(value.getTime())
  end.setHours(23, 59, 59, 999)
  return end.toISOString()
}
