import { Pair } from '../../models/Pair';
import { WeekSchedule } from '../../models/WeekSchedule';
import ScheduleDayToggler from '../ScheduleDayToggler';
import ScheduleTable from '../ScheduleTable/ScheduleTable';
import { SliceContextProvider } from '../../common/context/SliceOptionsContext';
import { ScheduleComponentsProps } from '../../types/ScheduleComponentsProps';
import { cn } from '../../common/utils/cn';

export const ScheduleGrid = ({ className, ...props }: React.ComponentPropsWithoutRef<'div'>) => (
  <div
    className={cn(
      'relative flex grow flex-col gap-4 overflow-hidden rounded-[20px] border-2 border-neutral-100 bg-bg-table',
      className,
    )}
    {...props}
  />
);

interface ScheduleWrapperProps<T extends Pair> extends ScheduleComponentsProps<T> {
  weekSchedule?: WeekSchedule<T>[];
}

const ScheduleWrapper = <T extends Pair>({
  weekSchedule,
  baseComponent: BaseComponent,
  baseComponentExtended: BaseComponentExtended,
}: ScheduleWrapperProps<T>) => {
  return (
    <SliceContextProvider>
      <ScheduleDayToggler />
      <ScheduleTable
        weekSchedule={weekSchedule}
        baseComponent={BaseComponent}
        baseComponentExtended={BaseComponentExtended}
      />
    </SliceContextProvider>
  );
};

export default ScheduleWrapper;
