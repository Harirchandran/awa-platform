export function parseDateOnlyAsLocal(value?: string | null): Date | null {
  if (!value) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [year, month, day] = value.split('-').map(Number);
    return new Date(year, month - 1, day);
  }
  const normalized = value.replace(' ', 'T');
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function formatLocalDateOnly(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function formatDateOnlyLabel(
  value: string,
  options: Intl.DateTimeFormatOptions,
  locale?: string | string[]
): string {
  const parsed = parseDateOnlyAsLocal(value);
  return parsed ? parsed.toLocaleDateString(locale, options) : value;
}
