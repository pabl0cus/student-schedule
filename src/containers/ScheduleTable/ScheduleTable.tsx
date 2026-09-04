import React from 'react';
import { generateScheduleMatrix } from '../../common/utils/generateScheduleMatrix';
import { Pair } from '../../models/Pair';
import { WeekSchedule } from '../../models/WeekSchedule';
import { ScheduleHeader } from '../ScheduleHeader';
import ScheduleRow from '../ScheduleRow';
import TimeDivider from '../../components/TimeDivider';
import { useCurrentTime } from '../../queries/useCurrentTime';
import { useSliceOptionsContext } from '../../common/context/useSliceOptions';
import { useScheduleWeek } from '../../common/context/useScheduleWeek';
import { ScheduleMatrix, ScheduleMatrixRow } from '../../types/ScheduleMatrix';
import { ScheduleComponentsProps } from '../../types/ScheduleComponentsProps';
import { useTimeSlots } from '../../queries/useTimeSlots';

interface ScheduleWrapperProps<T extends Pair> extends ScheduleComponentsProps<T> {
  weekSchedule?: WeekSchedule<T>[];
}

const ScheduleTable = <T extends Pair>({
  weekSchedule,
  baseComponent: BaseComponent,
  baseComponentExtended: BaseComponentExtended,
}: ScheduleWrapperProps<T>) => {
  const { slice } = useSliceOptionsContext();
  const { isCurrentWeek, weekDates } = useScheduleWeek();
  const { data: currentTime } = useCurrentTime();
  const { data: timeSlots } = useTimeSlots();
  const [start, end] = slice;

  const currentDayIndex =
    currentTime && currentTime.currentDay >= start && currentTime.currentDay <= end
      ? currentTime.currentDay - start
      : -1;
  const currentDayColumn = isCurrentWeek && currentDayIndex >= 0 ? currentDayIndex + 1 : undefined;

  const generateScheduleRows = (scheduleMatrix: ScheduleMatrix<T>, timeSlots: string[]) => {
    return scheduleMatrix.map((item: ScheduleMatrixRow<T>, i: number) => {
      const [start, end] = slice;
      const slicedDataset = item.slice(start - 1, end);

      if (i + 1 > timeSlots?.length) {
        return null;
      }

      return (
        <React.Fragment key={i}>
          <TimeDivider value={timeSlots[i]} />
          <ScheduleRow
            key={i}
            scheduleMatrixCell={slicedDataset}
            baseComponent={BaseComponent}
            baseComponentExtended={BaseComponentExtended}
          />
        </React.Fragment>
      );
    });
  };

  if (!timeSlots?.length) {
    return null;
  }

  const scheduleMatrix = generateScheduleMatrix<T>(
    weekSchedule || [],
    timeSlots,
    weekDates,
    isCurrentWeek ? currentTime?.currentLesson : undefined,
  );

  return (
    <div className="relative m-3 grid grid-cols-1 gap-x-6 gap-y-2.5 pl-[60px] sm:grid-cols-2 sm:pl-[100px] lg:grid-cols-3 2xl:grid-cols-6">
      {currentDayColumn ? (
        <div
          className="absolute top-0 -bottom-3 -left-3 z-0 w-[calc(100%+1.5rem)] bg-current-day sm:-top-3"
          style={{ gridColumn: `${currentDayColumn} / span 1` }}
        />
      ) : null}
      <ScheduleHeader />
      {generateScheduleRows(scheduleMatrix, timeSlots)}
    </div>
  );
};

export default ScheduleTable;
