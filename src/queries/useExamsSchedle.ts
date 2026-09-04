import { getExamsByGroup } from '../api/schedule';
import { useQuery } from 'react-query';

const getQueryKey = (groupId?: string) => {
  return ['examsSchedule', groupId];
};

export const useExamsSchedule = (groupId?: string) => {
  return useQuery({
    queryKey: getQueryKey(groupId),
    queryFn: () => (groupId ? getExamsByGroup(groupId) : undefined),
    refetchOnWindowFocus: false,
  });
};
