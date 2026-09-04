import { Exam } from '../models/Exam';
import { GroupSyncDate } from '../models/GroupSyncDate';
import Http from './index';
import { LecturerSchedule } from '../models/LecturerSchedule';
import { StudentSchedule } from '../models/StudentSchedule';

const withQuery = (path: string, key: string, value?: string) => {
  if (!value) {
    return path;
  }
  return `${path}?${new URLSearchParams({ [key]: value })}`;
};

export const getScheduleByLecturer = (lecturerId: string): Promise<LecturerSchedule> => {
  return Http.get(withQuery('/schedule/lecturer', 'lecturerId', lecturerId));
};

export const getScheduleByGroup = (groupId: string): Promise<StudentSchedule> => {
  return Http.get(withQuery('/schedule/lessons', 'groupId', groupId));
};

export const getExamsByGroup = (groupId: string): Promise<Exam[]> => {
  return Http.get(withQuery('/schedule/exams/group', 'groupId', groupId));
};

export const getLastSyncDate = (groupId?: string): Promise<GroupSyncDate[]> => {
  return Http.get(withQuery('/schedule/status', 'groupId', groupId));
};
