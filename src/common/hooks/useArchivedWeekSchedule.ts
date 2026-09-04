import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Pair } from '../../models/Pair';
import { Schedule } from '../../models/Schedule';
import { WeekSchedule } from '../../models/WeekSchedule';
import { usePutScheduleSnapshot, useScheduleSnapshot } from '../../queries/useScheduleSnapshot';
import { Week } from '../../types/Week';
import { useScheduleWeek } from '../context/useScheduleWeek';
import { getCalendarWeekStart } from '../utils/calendarWeek';
import { resolveCalendarSchedule } from '../utils/resolveCalendarSchedule';

interface ArchivedWeekScheduleOptions<T extends Pair> {
  schedule?: Schedule<T>;
  scopeKey?: string;
  scopeLabel?: string;
}

export interface ArchivedWeekScheduleState<T extends Pair> {
  scheduleWeek?: Week;
  weekStart: string;
  weekSchedule: WeekSchedule<T>[];
  isScheduleSnapshotReady: boolean;
  isResolvingScheduleSnapshot: boolean;
  isScheduleArchived: boolean;
  isArchivingSchedule: boolean;
  archiveError?: Error;
  ensureScheduleArchived: () => Promise<void>;
}

const toError = (error: unknown) =>
  error instanceof Error ? error : new Error('Не вдалося закріпити копію розкладу.');

const isSnapshotConflict = (error: Error) => /already archived|409/i.test(error.message);

export const useArchivedWeekSchedule = <T extends Pair>({
  schedule,
  scopeKey,
  scopeLabel,
}: ArchivedWeekScheduleOptions<T>): ArchivedWeekScheduleState<T> => {
  const { cycleWeek, weekDates, weekStart } = useScheduleWeek();
  const snapshotQuery = useScheduleSnapshot<T>(scopeKey, weekStart);
  const { mutateAsync: putSnapshot, reset: resetPutSnapshot } = usePutScheduleSnapshot<T>();
  const [archiveError, setArchiveError] = useState<Error>();
  const [isArchivingSchedule, setIsArchivingSchedule] = useState(false);
  const activeEnsure = useRef<{ key: string; promise: Promise<void> }>();
  const autoArchiveAttempt = useRef<string>();

  const resolvedWeekSchedule = useMemo(
    () => resolveCalendarSchedule(schedule, cycleWeek, weekDates),
    [cycleWeek, schedule, weekDates],
  );

  const archivedSnapshot = snapshotQuery.data;
  const effectiveWeekSchedule = archivedSnapshot?.schedule.days ?? resolvedWeekSchedule;
  const effectiveScheduleWeek = archivedSnapshot?.schedule.scheduleWeek ?? cycleWeek;
  const ensureKey = `${scopeKey || ''}|${weekStart}`;
  const currentEnsureKey = useRef(ensureKey);
  currentEnsureKey.current = ensureKey;

  useEffect(() => {
    setArchiveError(undefined);
    setIsArchivingSchedule(false);
    autoArchiveAttempt.current = undefined;
  }, [ensureKey]);

  const ensureScheduleArchived = useCallback(() => {
    if (archivedSnapshot) {
      return Promise.resolve();
    }

    if (activeEnsure.current?.key === ensureKey) {
      return activeEnsure.current.promise;
    }

    const promise = (async () => {
      setArchiveError(undefined);
      setIsArchivingSchedule(true);

      try {
        if (!scopeKey || !scopeLabel || !cycleWeek || !schedule) {
          throw new Error('Розклад ще не завантажено — зачекайте й повторіть.');
        }

        if (!snapshotQuery.isSuccess || snapshotQuery.isFetching) {
          const exactSnapshot = await snapshotQuery.refetch();

          if (exactSnapshot.data) {
            return;
          }

          if (exactSnapshot.isError) {
            throw toError(exactSnapshot.error);
          }
        }

        try {
          await putSnapshot({
            scopeKey,
            weekStart,
            snapshot: {
              scopeLabel,
              schedule: {
                scheduleWeek: cycleWeek,
                days: resolvedWeekSchedule,
              },
            },
          });
        } catch (error) {
          const normalizedError = toError(error);

          if (isSnapshotConflict(normalizedError)) {
            const exactSnapshot = await snapshotQuery.refetch();
            if (exactSnapshot.data) {
              resetPutSnapshot();
              throw new Error(
                'Цей тиждень щойно закріпили з іншим розкладом. Відкрийте матеріали потрібної пари ще раз.',
              );
            }
          }

          throw normalizedError;
        }
      } catch (error) {
        const normalizedError = toError(error);
        if (currentEnsureKey.current === ensureKey) {
          setArchiveError(normalizedError);
        }
        throw normalizedError;
      } finally {
        if (currentEnsureKey.current === ensureKey) {
          setIsArchivingSchedule(false);
        }
      }
    })();

    activeEnsure.current = { key: ensureKey, promise };
    const clearActiveEnsure = () => {
      if (activeEnsure.current?.promise === promise) {
        activeEnsure.current = undefined;
      }
    };
    void promise.then(clearActiveEnsure, clearActiveEnsure);

    return promise;
  }, [
    archivedSnapshot,
    cycleWeek,
    ensureKey,
    putSnapshot,
    resetPutSnapshot,
    resolvedWeekSchedule,
    schedule,
    scopeKey,
    scopeLabel,
    snapshotQuery,
    weekStart,
  ]);

  useEffect(() => {
    const isPastWeek = weekStart < getCalendarWeekStart();

    if (
      !isPastWeek ||
      archivedSnapshot ||
      !scopeKey ||
      !scopeLabel ||
      !cycleWeek ||
      !schedule ||
      !snapshotQuery.isSuccess ||
      snapshotQuery.isFetching ||
      autoArchiveAttempt.current === ensureKey
    ) {
      return;
    }

    autoArchiveAttempt.current = ensureKey;
    void ensureScheduleArchived().catch(() => undefined);
  }, [
    archivedSnapshot,
    cycleWeek,
    ensureKey,
    ensureScheduleArchived,
    schedule,
    scopeKey,
    scopeLabel,
    snapshotQuery.isFetching,
    snapshotQuery.isSuccess,
    weekStart,
  ]);

  return {
    scheduleWeek: effectiveScheduleWeek,
    weekStart,
    weekSchedule: effectiveWeekSchedule,
    isScheduleSnapshotReady: snapshotQuery.isSuccess,
    isResolvingScheduleSnapshot: snapshotQuery.isLoading || snapshotQuery.isFetching,
    isScheduleArchived: Boolean(archivedSnapshot),
    isArchivingSchedule,
    archiveError: archiveError || (snapshotQuery.isError ? toError(snapshotQuery.error) : undefined),
    ensureScheduleArchived,
  };
};
