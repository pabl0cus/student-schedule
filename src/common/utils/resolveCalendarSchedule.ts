import { Pair } from '../../models/Pair';
import { Schedule } from '../../models/Schedule';
import { WeekSchedule } from '../../models/WeekSchedule';
import { Week } from '../../types/Week';
import { DAYS } from '../constants/scheduleParams';

const scheduleProperty: Record<Week, keyof Schedule<Pair>> = {
  firstWeek: 'scheduleFirstWeek',
  secondWeek: 'scheduleSecondWeek',
};

/**
 * Resolves the repeating two-week API schedule into one concrete calendar week.
 * Explicitly dated lessons are retained only for dates within the selected
 * Monday-Saturday range; recurring lessons are copied unchanged.
 */
export const resolveCalendarSchedule = <T extends Pair>(
  schedule: Schedule<T> | undefined,
  scheduleWeek: Week | undefined,
  weekDates: string[],
): WeekSchedule<T>[] => {
  if (!schedule || !scheduleWeek) {
    return [];
  }

  const selectedDates = new Set(weekDates.slice(0, DAYS.length));
  const sourceDays = schedule[scheduleProperty[scheduleWeek] as keyof Schedule<T>];

  return DAYS.map((day) => ({
    day,
    pairs: sourceDays
      .filter((sourceDay) => sourceDay.day === day)
      .flatMap((sourceDay) => sourceDay.pairs)
      .flatMap((pair) => {
        if (pair.dates.length === 0) {
          return [pair];
        }

        const dates = pair.dates.filter((date) => selectedDates.has(date));
        return dates.length > 0 ? [{ ...pair, dates }] : [];
      }),
  }));
};
