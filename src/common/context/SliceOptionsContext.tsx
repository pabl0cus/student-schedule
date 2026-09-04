import { useEffect, useState } from 'react';

import { ScreenSize } from '../../types/ScreenSize';
import { useCurrentTime } from '../../queries/useCurrentTime';
import { useScreenSize } from '../hooks/useScreenSize';
import { useScheduleWeek } from './useScheduleWeek';
import { Slice, SliceContextValue, SliceOptionsContext } from './useSliceOptions';

const defaultValue: Slice = [0, 0];

interface SliceContextProviderProps {
  children: React.ReactNode | React.ReactNode[];
}

const DAYS_COUNT = 6;

const ScreenSizeSlicesCount: Record<ScreenSize, number> = {
  [ScreenSize.Big]: 1,
  [ScreenSize.Medium]: 2,
  [ScreenSize.Small]: 3,
  [ScreenSize.ExtraSmall]: 6,
};

const generateSlices = (screenSize: ScreenSize): Slice[] => {
  const numberOfSlices = ScreenSizeSlicesCount[screenSize];
  const sliceRange = DAYS_COUNT / numberOfSlices;

  return Array.from(
    { length: numberOfSlices },
    (_, index): Slice => [sliceRange * index + 1, sliceRange * index + sliceRange],
  );
};

const getCurrentSlice = (screenSize: ScreenSize, currentDay: number): Slice => {
  const slices = generateSlices(screenSize);
  const normalizedDay = Math.min(Math.max(currentDay, 1), DAYS_COUNT);

  return slices.find(([start, end]) => normalizedDay >= start && normalizedDay <= end) || defaultValue;
};

export const SliceContextProvider = ({ children }: SliceContextProviderProps) => {
  const { data } = useCurrentTime();
  const { screenSize } = useScreenSize();
  const { isCurrentWeek } = useScheduleWeek();
  const [slice, setSlice] = useState<Slice>(defaultValue);

  useEffect(() => {
    if (isCurrentWeek && data?.currentDay != null) {
      setSlice(getCurrentSlice(screenSize, data?.currentDay || 0));
      return;
    }

    setSlice(generateSlices(screenSize)[0] || defaultValue);
  }, [data?.currentDay, isCurrentWeek, screenSize]);

  const value: SliceContextValue = {
    slice,
    setSlice,
  };

  return <SliceOptionsContext.Provider value={value}>{children}</SliceOptionsContext.Provider>;
};
