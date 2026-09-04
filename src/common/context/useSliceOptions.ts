import { createContext, useContext } from 'react';

export type Slice = [number, number];

export interface SliceContextValue {
  slice: Slice;
  setSlice: (slice: Slice) => void;
}

export const SliceOptionsContext = createContext<SliceContextValue | undefined>(undefined);

export const useSliceOptionsContext = () => {
  const context = useContext(SliceOptionsContext);
  if (!context) {
    throw new Error('useSliceOptionsContext must be used inside SliceContextProvider');
  }
  return context;
};
