import { Pair } from './Pair';
import { WeekSchedule } from './WeekSchedule';
import { Week } from '../types/Week';

export type ScheduleScopeType = 'group' | 'lecturer';

export interface ArchivedWeekSchedule<T extends Pair = Pair> {
  scheduleWeek: Week;
  days: WeekSchedule<T>[];
}

export interface ScheduleSnapshot<T extends Pair = Pair> {
  scopeType: ScheduleScopeType;
  scopeId: string;
  scopeKey: string;
  scopeLabel: string;
  weekStart: string;
  weekEnd: string;
  contentHash: string;
  createdAt: string;
  schedule: ArchivedWeekSchedule<T>;
}

export interface ScheduleSnapshotWrite<T extends Pair = Pair> {
  scopeLabel: string;
  schedule: ArchivedWeekSchedule<T>;
}
