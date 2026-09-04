import { useMutation, useQuery, useQueryClient } from 'react-query';
import { getLessonAttachments, uploadAttachment } from '../api/attachments';

type LessonKeys = string | string[] | undefined;

const normalizeLessonKeys = (lessonKeys: LessonKeys) =>
  Array.from(
    new Set((Array.isArray(lessonKeys) ? lessonKeys : [lessonKeys]).filter((key): key is string => Boolean(key))),
  );

export const getAttachmentsQueryKey = (lessonKeys?: string | string[]) => [
  'attachments',
  ...normalizeLessonKeys(lessonKeys),
];

export const useLessonAttachments = (lessonKeys?: string | string[], enabled = true) => {
  const normalizedKeys = normalizeLessonKeys(lessonKeys);

  return useQuery({
    queryKey: getAttachmentsQueryKey(normalizedKeys),
    queryFn: () => getLessonAttachments(normalizedKeys),
    enabled: enabled && normalizedKeys.length > 0,
    retry: false,
    staleTime: 5_000,
  });
};

export const useUploadAttachment = () => {
  const queryClient = useQueryClient();

  return useMutation(uploadAttachment, {
    onSuccess: () => {
      queryClient.invalidateQueries(['attachments']);
    },
  });
};
