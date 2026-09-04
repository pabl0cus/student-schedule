import { RecordingScope } from '../context/useRecordingScope';
import { LecturerPair } from '../../models/LecturerPair';
import { Pair } from '../../models/Pair';
import { StudentPair } from '../../models/StudentPair';

export interface RecordingUploadContext {
  groups: { id: string; label: string }[];
  lecturerName?: string;
  lessonKeys: string[];
}

const isStudentPair = (pair: Pair): pair is StudentPair => 'lecturer' in pair;
const isLecturerPair = (pair: Pair): pair is LecturerPair => 'groups' in pair;

export const createRecordingUploadContext = (
  scope: RecordingScope,
  pair: Pair,
  lessonKeys: string[],
): RecordingUploadContext => {
  const scopeGroupId = scope.scopeKey.startsWith('group:') ? scope.scopeKey.slice('group:'.length) : undefined;
  const groups = isLecturerPair(pair)
    ? pair.groups.map((group) => ({ id: String(group.id), label: group.name }))
    : scopeGroupId
      ? [{ id: scopeGroupId, label: scope.label.replace(/^Група\s+/, '') }]
      : [];

  return {
    groups: Array.from(new Map(groups.map((group) => [group.id, group])).values()),
    lecturerName: isStudentPair(pair)
      ? pair.lecturer.name
      : scope.scopeKey.startsWith('lecturer:')
        ? scope.label
        : undefined,
    lessonKeys: Array.from(new Set(lessonKeys)),
  };
};
