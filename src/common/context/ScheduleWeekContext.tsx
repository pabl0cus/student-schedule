import { useCallback, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useCurrentTime } from '../../queries/useCurrentTime';
import {
  getCalendarWeekDates,
  getCalendarWeekEnd,
  getCalendarWeekStart,
  getCycleWeek,
  normalizeCalendarWeek,
  shiftCalendarWeek,
} from '../utils/calendarWeek';
import { ScheduleWeekContext, ScheduleWeekContextValue } from './useScheduleWeek';

interface Props {
  children: React.ReactNode;
}

export const ScheduleWeekProvider = ({ children }: Props) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const { data: currentTime } = useCurrentTime();
  const currentWeekStart = getCalendarWeekStart();
  const requestedWeek = searchParams.get('week') || undefined;
  const weekStart = normalizeCalendarWeek(requestedWeek) || currentWeekStart;

  useEffect(() => {
    if (requestedWeek === weekStart) {
      return;
    }

    const nextSearchParams = new URLSearchParams(searchParams);
    nextSearchParams.set('week', weekStart);
    setSearchParams(nextSearchParams, { replace: true });
  }, [requestedWeek, searchParams, setSearchParams, weekStart]);

  const updateWeek = useCallback(
    (date: string) => {
      const normalizedWeek = normalizeCalendarWeek(date);
      if (!normalizedWeek || normalizedWeek === weekStart) {
        return;
      }

      const nextSearchParams = new URLSearchParams(searchParams);
      nextSearchParams.set('week', normalizedWeek);
      nextSearchParams.delete('recordingId');
      setSearchParams(nextSearchParams);
    },
    [searchParams, setSearchParams, weekStart],
  );

  const value = useMemo<ScheduleWeekContextValue>(
    () => ({
      weekStart,
      weekEnd: getCalendarWeekEnd(weekStart),
      weekDates: getCalendarWeekDates(weekStart),
      cycleWeek: getCycleWeek(weekStart, currentTime?.currentWeek, currentWeekStart),
      isCurrentWeek: weekStart === currentWeekStart,
      goToPreviousWeek: () => updateWeek(shiftCalendarWeek(weekStart, -1)),
      goToNextWeek: () => updateWeek(shiftCalendarWeek(weekStart, 1)),
      goToCurrentWeek: () => updateWeek(currentWeekStart),
      selectWeek: updateWeek,
    }),
    [currentTime?.currentWeek, currentWeekStart, updateWeek, weekStart],
  );

  return <ScheduleWeekContext.Provider value={value}>{children}</ScheduleWeekContext.Provider>;
};
