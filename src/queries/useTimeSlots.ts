import { getTimeSlots } from '../api/time';
import { useQuery } from 'react-query';

const getQueryKey = () => {
  return 'timeSlots';
};

export const useTimeSlots = () => {
  return useQuery({
    staleTime: 12 * 60 * 60 * 1000,
    queryKey: getQueryKey(),
    placeholderData: [],
    queryFn: async () => {
      const timeSlots = await getTimeSlots();

      return Object.entries(timeSlots)
        .sort(([left], [right]) => Number(left) - Number(right))
        .map(([, timeSlot]) => timeSlot);
    },
    refetchOnWindowFocus: false,
  });
};
