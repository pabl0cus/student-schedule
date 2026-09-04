import { useMutation, useQuery, useQueryClient } from 'react-query';
import { getScheduleSnapshot, putScheduleSnapshot } from '../api/scheduleSnapshots';
import { Pair } from '../models/Pair';
import { ScheduleSnapshot, ScheduleSnapshotWrite } from '../models/ScheduleSnapshot';

export const getScheduleSnapshotQueryKey = (scopeKey?: string, weekStart?: string) => [
  'scheduleSnapshot',
  scopeKey,
  weekStart,
];

export const useScheduleSnapshot = <T extends Pair>(scopeKey?: string, weekStart?: string) =>
  useQuery({
    queryKey: getScheduleSnapshotQueryKey(scopeKey, weekStart),
    queryFn: () => (scopeKey && weekStart ? getScheduleSnapshot<T>(scopeKey, weekStart) : undefined),
    enabled: Boolean(scopeKey && weekStart),
    retry: false,
    staleTime: Infinity,
  });

interface PutScheduleSnapshotOptions<T extends Pair> {
  scopeKey: string;
  weekStart: string;
  snapshot: ScheduleSnapshotWrite<T>;
}

export const usePutScheduleSnapshot = <T extends Pair>() => {
  const queryClient = useQueryClient();

  return useMutation<ScheduleSnapshot<T>, Error, PutScheduleSnapshotOptions<T>>(
    ({ scopeKey, weekStart, snapshot }) => putScheduleSnapshot(scopeKey, weekStart, snapshot),
    {
      onSuccess: (archived) => {
        queryClient.setQueryData(getScheduleSnapshotQueryKey(archived.scopeKey, archived.weekStart), archived);
      },
    },
  );
};
