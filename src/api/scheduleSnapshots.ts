import { Pair } from '../models/Pair';
import { ScheduleSnapshot, ScheduleSnapshotWrite, ScheduleScopeType } from '../models/ScheduleSnapshot';
import { getRecordingApiUrl, LOCAL_REQUEST_HEADERS, parseApiError } from './recordingsShared';

interface RawScheduleSnapshot<T extends Pair> {
  scope_type: ScheduleScopeType;
  scope_id: string;
  scope_key: string;
  scope_label: string;
  week_start: string;
  week_end: string;
  content_hash: string;
  created_at: string;
  schedule: ScheduleSnapshot<T>['schedule'];
}

const parseScopeKey = (scopeKey: string) => {
  const separator = scopeKey.indexOf(':');
  const scopeType = scopeKey.slice(0, separator);
  const scopeId = scopeKey.slice(separator + 1);

  if ((scopeType !== 'group' && scopeType !== 'lecturer') || !scopeId) {
    throw new Error('Некоректний ключ розкладу');
  }

  return { scopeType, scopeId } as const;
};

const normalizeSnapshot = <T extends Pair>(snapshot: RawScheduleSnapshot<T>): ScheduleSnapshot<T> => ({
  scopeType: snapshot.scope_type,
  scopeId: snapshot.scope_id,
  scopeKey: snapshot.scope_key,
  scopeLabel: snapshot.scope_label,
  weekStart: snapshot.week_start,
  weekEnd: snapshot.week_end,
  contentHash: snapshot.content_hash,
  createdAt: snapshot.created_at,
  schedule: snapshot.schedule,
});

const getSnapshotPath = (scopeKey: string, weekStart: string) => {
  const { scopeType, scopeId } = parseScopeKey(scopeKey);
  return `/schedule-snapshots/${scopeType}/${encodeURIComponent(scopeId)}/${encodeURIComponent(weekStart)}`;
};

export const getScheduleSnapshot = async <T extends Pair>(
  scopeKey: string,
  weekStart: string,
): Promise<ScheduleSnapshot<T> | undefined> => {
  const response = await fetch(getRecordingApiUrl(getSnapshotPath(scopeKey, weekStart)));

  if (response.status === 404) {
    return undefined;
  }

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return normalizeSnapshot((await response.json()) as RawScheduleSnapshot<T>);
};

export const putScheduleSnapshot = async <T extends Pair>(
  scopeKey: string,
  weekStart: string,
  snapshot: ScheduleSnapshotWrite<T>,
): Promise<ScheduleSnapshot<T>> => {
  const response = await fetch(getRecordingApiUrl(getSnapshotPath(scopeKey, weekStart)), {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...LOCAL_REQUEST_HEADERS,
    },
    body: JSON.stringify({
      scope_label: snapshot.scopeLabel,
      schedule: snapshot.schedule,
    }),
  });

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return normalizeSnapshot((await response.json()) as RawScheduleSnapshot<T>);
};
