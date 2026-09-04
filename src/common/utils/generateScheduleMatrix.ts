import { DAYS } from '../constants/scheduleParams';
import { Pair } from '../../models/Pair';
import { WeekSchedule } from '../../models/WeekSchedule';
import dayjs from 'dayjs';
import { ScheduleMatrix, ScheduleMatrixCell } from '../../types/ScheduleMatrix';

export const generateScheduleMatrix = <T extends Pair>(
  weekSchedule: WeekSchedule<T>[],
  timeSlots: string[],
  weekDates: string[],
  currentLesson = 0,
): ScheduleMatrix<T> => {
  const scheduleMatrix: ScheduleMatrix<T> = Array.from({ length: timeSlots.length }, () =>
    Array.from({ length: DAYS.length }, () => null),
  );

  const activePair = currentLesson - 1;
  const today = dayjs().format('YYYY-MM-DD');

  weekSchedule.forEach((schedule) => {
    const yIndex = DAYS.findIndex((item) => item === schedule.day);
    const date = weekDates[yIndex];

    if (yIndex < 0 || !date) {
      return;
    }

    schedule.pairs.forEach((pair) => {
      if (pair.dates.length > 0 && !pair.dates.includes(date)) {
        return;
      }

      const xIndex = timeSlots.indexOf(pair.time);
      if (xIndex < 0) {
        return;
      }

      const cell = scheduleMatrix[xIndex][yIndex];
      let newCell: ScheduleMatrixCell<T> | ScheduleMatrixCell<T>[] = {
        pair,
        day: schedule.day,
        date,
        currentPair: activePair !== -1 && date === today && activePair === xIndex,
      };

      if (cell) {
        let extendedCell: ScheduleMatrixCell<T>[] = [];

        if (Array.isArray(cell)) {
          extendedCell = [...cell];
        } else {
          extendedCell = [cell];
        }

        extendedCell.push({ pair, day: schedule.day, date, currentPair: newCell.currentPair });
        newCell = extendedCell;
      }

      scheduleMatrix[xIndex][yIndex] = newCell;
    });
  });

  return scheduleMatrix;
};
