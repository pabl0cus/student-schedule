import { useQuery } from 'react-query';
import { getAllGroups, getAllLecturers } from '../../api/fullList';
import { Group } from '../../models/Group';
import { EntityWithNameAndId } from '../../models/EntityWithNameAndId';

export const useGroups = () =>
  useQuery<Group[]>({
    queryKey: 'groups',
    queryFn: getAllGroups,
    refetchOnWindowFocus: false,
  });

export const useLecturers = () =>
  useQuery<EntityWithNameAndId[]>({
    queryKey: 'lecturers',
    queryFn: getAllLecturers,
    refetchOnWindowFocus: false,
  });
