import { useMutation, useQuery, useQueryClient } from 'react-query';
import {
  getLessonRecordings,
  getRecording,
  retryRecording,
  searchRecordings,
  uploadRecording,
} from '../api/recordings';
import { Recording, RecordingSearchScope } from '../models/Recording';

const isRunning = (recording?: Recording) => recording?.status === 'queued' || recording?.status === 'processing';

type LessonKeys = string | string[] | undefined;

const normalizeLessonKeys = (lessonKeys: LessonKeys) =>
  Array.from(
    new Set((Array.isArray(lessonKeys) ? lessonKeys : [lessonKeys]).filter((key): key is string => Boolean(key))),
  );

export const getRecordingsQueryKey = (lessonKeys?: string | string[]) => [
  'recordings',
  ...normalizeLessonKeys(lessonKeys),
];

export const useLessonRecordings = (lessonKeys?: string | string[], enabled = true) => {
  const normalizedKeys = normalizeLessonKeys(lessonKeys);

  return useQuery({
    queryKey: getRecordingsQueryKey(normalizedKeys),
    queryFn: () => getLessonRecordings(normalizedKeys),
    enabled: enabled && normalizedKeys.length > 0,
    retry: false,
    staleTime: 5_000,
    refetchInterval: (recordings: Recording[] | undefined) => (recordings?.some(isRunning) ? 2_500 : false),
  });
};

export const useRecording = (recordingId?: string, enabled = true) =>
  useQuery({
    queryKey: ['recording', recordingId],
    queryFn: () => (recordingId ? getRecording(recordingId) : undefined),
    enabled: Boolean(recordingId) && enabled,
    retry: false,
    refetchInterval: (recording: Recording | undefined) => (isRunning(recording) ? 2_500 : false),
  });

export const useRecordingSearch = ({
  groupId,
  query,
  scope,
  limit,
  offset,
  enabled = true,
}: {
  groupId?: string;
  query: string;
  scope: RecordingSearchScope;
  limit: number;
  offset: number;
  enabled?: boolean;
}) =>
  useQuery({
    queryKey: ['recording-search', groupId, query, scope, limit, offset],
    queryFn: () => searchRecordings({ groupId: groupId || '', query, scope, limit, offset }),
    enabled: enabled && Boolean(groupId),
    keepPreviousData: true,
    retry: false,
    staleTime: 30_000,
  });

export const useUploadRecording = () => {
  const queryClient = useQueryClient();

  return useMutation(uploadRecording, {
    onSuccess: (recording) => {
      queryClient.setQueryData(['recording', recording.id], recording);
      queryClient.invalidateQueries(['recordings']);
      queryClient.invalidateQueries(['recording-search']);
    },
  });
};

export const useRetryRecording = () => {
  const queryClient = useQueryClient();

  return useMutation(retryRecording, {
    onSuccess: (recording) => {
      queryClient.setQueryData(['recording', recording.id], recording);
      queryClient.invalidateQueries(['recordings']);
      queryClient.invalidateQueries(['recording-search']);
    },
  });
};
