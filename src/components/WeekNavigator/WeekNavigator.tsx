import dayjs from 'dayjs';
import { useLocation } from 'react-router-dom';
import Calendar from '../../assets/icons/calendar-blank.svg?react';
import CaretDown from '../../assets/icons/caret-down.svg?react';
import { routes } from '../../common/constants/routes';
import { useScheduleWeek } from '../../common/context/useScheduleWeek';
import { Pair } from '../../models/Pair';
import { useScheduleSnapshot } from '../../queries/useScheduleSnapshot';
import { useStore } from '../../store';

const WeekNavigator = () => {
  const location = useLocation();
  const groupId = useStore((state) => state.group?.id);
  const lecturerId = useStore((state) => state.lecturer?.id);
  const { weekStart, weekEnd, isCurrentWeek, goToPreviousWeek, goToNextWeek, goToCurrentWeek, selectWeek } =
    useScheduleWeek();
  const scopeKey = location.pathname.startsWith(routes.LECTURER)
    ? lecturerId
      ? `lecturer:${lecturerId}`
      : undefined
    : groupId
      ? `group:${groupId}`
      : undefined;
  const { data: snapshot } = useScheduleSnapshot<Pair>(scopeKey, weekStart);
  const visibleRange = `${dayjs(weekStart).format('DD.MM')}–${dayjs(weekEnd).format('DD.MM')}`;

  return (
    <div className="flex h-10 w-[278px] shrink-0 items-center gap-1 rounded-xl bg-bg-options p-1 shadow-radio-group max-sm:w-full">
      <button
        type="button"
        className="flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-lg text-neutral-700 transition-colors hover:bg-white hover:text-basic-blue focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-basic-blue"
        onClick={goToPreviousWeek}
        aria-label="Попередній тиждень"
        title="Попередній тиждень"
      >
        <CaretDown className="rotate-90" />
      </button>

      <label className="relative flex h-8 min-w-0 flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-lg bg-white px-2 text-center text-[13px] font-semibold text-primary-font shadow-radio-option transition-colors hover:bg-brand-00 focus-within:outline-2 focus-within:outline-offset-1 focus-within:outline-basic-blue">
        <Calendar className="size-4 shrink-0 text-basic-blue" aria-hidden="true" />
        <span className="min-w-0 truncate">{visibleRange}</span>
        {snapshot && (
          <span
            className="shrink-0 rounded-md bg-brand-00 px-1 py-0.5 text-[9px] leading-3 font-bold tracking-wide text-basic-blue uppercase"
            title="Збережений знімок розкладу"
          >
            Архів
          </span>
        )}
        <input
          type="date"
          className="absolute inset-0 cursor-pointer opacity-0"
          value={weekStart}
          onChange={(event) => selectWeek(event.target.value)}
          onClick={(event) => event.currentTarget.showPicker()}
          aria-label={`Обрати тиждень за датою. Зараз обрано ${visibleRange}`}
          title="Обрати тиждень"
        />
      </label>

      <button
        type="button"
        className="flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-lg text-neutral-700 transition-colors hover:bg-white hover:text-basic-blue focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-basic-blue"
        onClick={goToNextWeek}
        aria-label="Наступний тиждень"
        title="Наступний тиждень"
      >
        <CaretDown className="-rotate-90" />
      </button>

      {!isCurrentWeek && (
        <button
          type="button"
          className="h-8 shrink-0 cursor-pointer rounded-lg px-1.5 text-[11px] font-semibold text-basic-blue transition-colors hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-basic-blue"
          onClick={goToCurrentWeek}
          aria-label="Повернутися до поточного тижня"
          title="Повернутися до поточного тижня"
        >
          Зараз
        </button>
      )}
    </div>
  );
};

export default WeekNavigator;
