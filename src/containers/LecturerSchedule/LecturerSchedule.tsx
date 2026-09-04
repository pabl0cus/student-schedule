import ScheduleWrapper, { ScheduleGrid } from '../ScheduleWrapper/ScheduleWrapper';
import { useLecturerSchedule } from '../../queries/useLecturerSchedule';
import { useStore } from '../../store';
import LecturerScheduleItem from '../ScheduleItem/LecturerScheduleItem';
import LecturerScheduleItemExtended from '../ScheduleItemExtended/LecturerScheduleItemExtended';
import { RecordingScopeProvider } from '../../common/context/RecordingScopeContext';
import { useArchivedWeekSchedule } from '../../common/hooks/useArchivedWeekSchedule';

export const LecturerSchedule = () => {
  const lecturer = useStore((state) => state.lecturer);
  const { data } = useLecturerSchedule(lecturer?.id);
  const scopeKey = lecturer ? `lecturer:${lecturer.id}` : undefined;
  const scopeLabel = lecturer?.name;
  const archivedWeek = useArchivedWeekSchedule({ schedule: data, scopeKey, scopeLabel });

  return lecturer ? (
    <RecordingScopeProvider
      scopeKey={`lecturer:${lecturer.id}`}
      label={lecturer.name}
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
          baseComponent={LecturerScheduleItem}
          baseComponentExtended={LecturerScheduleItemExtended}
        />
      </ScheduleGrid>
    </RecordingScopeProvider>
  ) : (
    <ScheduleGrid>
      <ScheduleWrapper
        weekSchedule={archivedWeek.weekSchedule}
        baseComponent={LecturerScheduleItem}
        baseComponentExtended={LecturerScheduleItemExtended}
      />
    </ScheduleGrid>
  );
};
