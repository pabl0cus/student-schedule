import { createContext, useContext } from 'react';
import { Week } from '../../types/Week';

export interface ScheduleWeekContextValue {
  weekStart: string;
  weekEnd: string;
  weekDates: string[];
  cycleWeek?: Week;
  isCurrentWeek: boolean;
  goToPreviousWeek: () => void;
  goToNextWeek: () => void;
  goToCurrentWeek: () => void;
  selectWeek: (date: string) => void;
}

export const ScheduleWeekContext = createContext<ScheduleWeekContextValue | undefined>(undefined);

export const useScheduleWeek = () => {
  const context = useContext(ScheduleWeekContext);

  if (!context) {
    throw new Error('useScheduleWeek must be used inside ScheduleWeekProvider');
  }

  return context;
};
