import { createContext, useContext } from 'react';
import { Week } from '../../types/Week';

export interface RecordingScope {
  scopeKey: string;
  label: string;
  scheduleWeek?: Week;
  weekStart: string;
  isScheduleSnapshotReady: boolean;
  isResolvingScheduleSnapshot: boolean;
  isScheduleArchived: boolean;
  isArchivingSchedule: boolean;
  archiveError?: Error;
  ensureScheduleArchived: () => Promise<void>;
}

export const RecordingScopeContext = createContext<RecordingScope | undefined>(undefined);

export const useRecordingScope = () => useContext(RecordingScopeContext);
