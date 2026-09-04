import dayjs, { Dayjs } from 'dayjs';
import { Week } from '../../types/Week';

const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const DATE_FORMAT = 'YYYY-MM-DD';

const parseCalendarDate = (value: string): Dayjs | undefined => {
  if (!ISO_DATE_PATTERN.test(value)) {
    return undefined;
  }

  const parsed = dayjs(value);
  return parsed.isValid() && parsed.format(DATE_FORMAT) === value ? parsed : undefined;
};

export const getCalendarWeekStart = (value: string | Dayjs = dayjs()): string => {
  const parsed = typeof value === 'string' ? parseCalendarDate(value) : value;
  const date = parsed || dayjs();

  return date
    .startOf('day')
    .subtract((date.day() + 6) % 7, 'day')
    .format(DATE_FORMAT);
};

export const normalizeCalendarWeek = (value?: string): string | undefined => {
  if (!value) {
    return undefined;
  }

  const parsed = parseCalendarDate(value);
  return parsed ? getCalendarWeekStart(parsed) : undefined;
};

export const shiftCalendarWeek = (weekStart: string, amount: number): string =>
  dayjs(weekStart).add(amount, 'week').format(DATE_FORMAT);

export const getCalendarWeekEnd = (weekStart: string): string => dayjs(weekStart).add(6, 'day').format(DATE_FORMAT);

export const getCalendarWeekDates = (weekStart: string): string[] =>
  Array.from({ length: 6 }, (_, index) => dayjs(weekStart).add(index, 'day').format(DATE_FORMAT));

export const getCycleWeek = (
  weekStart: string,
  currentServerWeek?: number,
  currentWeekStart = getCalendarWeekStart(),
): Week | undefined => {
  if (currentServerWeek !== 1 && currentServerWeek !== 2) {
    return undefined;
  }

  const distanceInWeeks = dayjs(weekStart).diff(dayjs(currentWeekStart), 'week');
  const isSameCycleWeek = Math.abs(distanceInWeeks) % 2 === 0;
  const selectedServerWeek = isSameCycleWeek ? currentServerWeek : currentServerWeek === 1 ? 2 : 1;

  return selectedServerWeek === 1 ? 'firstWeek' : 'secondWeek';
};
