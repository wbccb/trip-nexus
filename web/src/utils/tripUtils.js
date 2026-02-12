export function normalizeTripDays(tripData) {
  if (!tripData || !tripData.daily_plan) {
    return []
  }
  const entries = Object.entries(tripData.daily_plan)
  entries.sort((a, b) => Number(a[0]) - Number(b[0]))
  return entries.map(([day, items]) => ({
    day,
    items: Array.isArray(items) ? items : [],
  }))
}
