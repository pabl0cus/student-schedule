import ScheduleWrapper, { ScheduleGrid } from '../ScheduleWrapper/ScheduleWrapper';
import { useStudentSchedule } from '../../queries/useStudentSchedule';
import { useStore } from '../../store';
import StudentScheduleItem from '../ScheduleItem/StudentScheduleItem';
import StudentScheduleItemExtended from '../ScheduleItemExtended/StudentScheduleItemExtended';
import { RecordingScopeProvider } from '../../common/context/RecordingScopeContext';
import { useArchivedWeekSchedule } from '../../common/hooks/useArchivedWeekSchedule';

export const GroupSchedule = () => {
  const group = useStore((state) => state.group);
  const { data } = useStudentSchedule(group?.id);
  const scopeKey = group ? `group:${group.id}` : undefined;
  const scopeLabel = group ? `Група ${group.name}` : undefined;
  const archivedWeek = useArchivedWeekSchedule({ schedule: data, scopeKey, scopeLabel });

  return group ? (
    <RecordingScopeProvider
      scopeKey={`group:${group.id}`}
      label={`Група ${group.name}`}
      scheduleWeek={archivedWeek.scheduleWeek}
      weekStart={archivedWeek.weekStart}
      isScheduleSnapshotReady={archivedWeek.isScheduleSnapshotReady}
      isResolvingScheduleSnapshot={archivedWeek.isResolvingScheduleSnapshot}
      isScheduleArchived={archivedWeek.isScheduleArchived}
      isArchivingSchedule={archivedWeek.isArchivingSchedule}
      archiveError={archivedWeek.archiveError}
      ensureScheduleArchived={archivedWeek.ensureScheduleArchived}
    >
      <ScheduleGrid>
        <ScheduleWrapper
          weekSchedule={archivedWeek.weekSchedule}
          baseComponent={StudentScheduleItem}
          baseComponentExtended={StudentScheduleItemExtended}
        />
      </ScheduleGrid>
    </RecordingScopeProvider>
  ) : (
    <ScheduleGrid>
      <ScheduleWrapper
        weekSchedule={archivedWeek.weekSchedule}
        baseComponent={StudentScheduleItem}
        baseComponentExtended={StudentScheduleItemExtended}
      />
    </ScheduleGrid>
  );
};
