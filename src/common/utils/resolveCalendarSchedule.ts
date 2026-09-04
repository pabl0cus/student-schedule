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
 * Explicitly dated lessons are shown only in a week containing one of their
 * dates, but keep the complete date list used by the irregular schedule UI.
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

        return pair.dates.some((date) => selectedDates.has(date)) ? [pair] : [];
      }),
  }));
};

const getPairIdentity = (pair: Pair) => {
  const relatedPair = pair as Pair & {
    lecturer?: { id: string };
    groups?: Array<{ id: string }>;
  };

  return JSON.stringify({
    name: pair.name,
    time: pair.time,
    type: pair.type,
    tag: pair.tag,
    lecturerId: relatedPair.lecturer?.id || '',
    groupIds: relatedPair.groups?.map((group) => group.id).sort() || [],
    locationUri: pair.location?.uri || '',
    locationTitle: pair.location?.title || '',
  });
};

/**
 * Older snapshots contain only the explicit date that fell within their week.
 * Restore the full list from the current API response when the same lesson is
 * still present and the archived dates are an unambiguous subset of it.
 */
export const restoreArchivedScheduleDates = <T extends Pair>(
  archivedDays: WeekSchedule<T>[],
  resolvedDays: WeekSchedule<T>[],
): WeekSchedule<T>[] =>
  archivedDays.map((archivedDay) => {
    const currentPairs = resolvedDays.find((day) => day.day === archivedDay.day)?.pairs || [];
    let changed = false;

    const pairs = archivedDay.pairs.map((archivedPair) => {
      if (archivedPair.dates.length === 0) {
        return archivedPair;
      }

      const identity = getPairIdentity(archivedPair);
      const matches = currentPairs.filter((pair) => getPairIdentity(pair) === identity);

      if (matches.length !== 1) {
        return archivedPair;
      }

      const currentDates = matches[0].dates;
      const isArchivedSubset = archivedPair.dates.every((date) => currentDates.includes(date));

      if (!isArchivedSubset || currentDates.length <= archivedPair.dates.length) {
        return archivedPair;
      }

      changed = true;
      return { ...archivedPair, dates: currentDates };
    });

    return changed ? { ...archivedDay, pairs } : archivedDay;
  });
