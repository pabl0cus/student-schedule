import React from 'react';
import dayjs from 'dayjs';
import { useSliceOptionsContext } from '../../common/context/useSliceOptions';
import { useScheduleWeek } from '../../common/context/useScheduleWeek';

const DAYS = ['Понеділок', 'Вівторок', 'Середа', 'Четвер', "П'ятниця", 'Субота'];

export const ScheduleHeader = () => {
  const { slice } = useSliceOptionsContext();
  const { weekDates } = useScheduleWeek();
  const [start, end] = slice;
  const slicedDays = DAYS.slice(start - 1, end);
  const slicedDates = weekDates.slice(start - 1, end);

  return (
    <React.Fragment>
      {slicedDays.map((day, index) => (
        <div
          className="z-2 hidden py-6 text-center text-[18px] font-semibold text-primary-font sm:block"
          key={slicedDates[index] || day}
        >
          <div>{day}</div>
          {slicedDates[index] && (
            <div className="mt-1 text-xs font-medium tracking-normal text-neutral-600">
              {dayjs(slicedDates[index]).format('DD.MM')}
            </div>
          )}
        </div>
      ))}
    </React.Fragment>
  );
};
