import { MAX_ATTACHMENT_UPLOAD_BYTES } from '../common/constants/uploadLimits';
import { AttachmentSearchPage, AttachmentSearchResult, LessonAttachment } from '../models/LessonAttachment';
import { getRecordingApiUrl, LOCAL_REQUEST_HEADER, LOCAL_REQUEST_VALUE, parseApiError } from './recordingsShared';
import { RecordingUploadContext } from '../common/utils/recordingContext';
const ATTACHMENTS_PAGE_SIZE = 200;
const MAX_ATTACHMENTS_PER_LESSON = 10_000;

class AttachmentPaginationContractError extends Error {}

interface RawAttachment {
  id: string;
  lesson_key: string;
  lesson_title: string;
  scope_label: string;
  file_name?: string | null;
  mime_type?: string | null;
  original_filename?: string | null;
  content_type?: string | null;
  size_bytes: number;
  recorded_at?: string | null;
  created_at: string;
  updated_at?: string | null;
  content_url?: string | null;
}

interface RawAttachmentSearchResult extends RawAttachment {
  group_id: string;
  group_label: string;
  matched_fields: AttachmentSearchResult['matchedFields'];
}

interface RawAttachmentSearchPage {
  items: RawAttachmentSearchResult[];
  total: number;
  limit: number;
  offset: number;
}

interface UploadAttachmentOptions {
  file: File;
  lessonKey: string;
  lessonTitle: string;
  scopeLabel: string;
  recordedAt: string;
  context: RecordingUploadContext;
  onProgress?: (progress: number) => void;
}

const normalizeAttachment = (attachment: RawAttachment): LessonAttachment => ({
  id: attachment.id,
  lessonKey: attachment.lesson_key,
  lessonTitle: attachment.lesson_title,
  scopeLabel: attachment.scope_label,
  fileName: attachment.original_filename || attachment.file_name || 'Файл',
  mimeType: attachment.content_type || attachment.mime_type || 'application/octet-stream',
  sizeBytes: attachment.size_bytes,
  recordedAt: attachment.recorded_at || attachment.created_at,
  createdAt: attachment.created_at,
  updatedAt: attachment.updated_at || undefined,
  contentUrl: attachment.content_url || undefined,
});

const request = async <T>(path: string): Promise<T> => {
  const response = await fetch(getRecordingApiUrl(path));

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return response.json() as Promise<T>;
};

export const getLessonAttachments = async (lessonKeys: string | string[]): Promise<LessonAttachment[]> => {
  const uniqueKeys = Array.from(new Set(Array.isArray(lessonKeys) ? lessonKeys : [lessonKeys]));
  if (!uniqueKeys.length) {
    return [];
  }

  const attachmentGroups = await Promise.allSettled(
    uniqueKeys.map(async (lessonKey): Promise<LessonAttachment[]> => {
      const lessonAttachments: LessonAttachment[] = [];
      let offset = 0;

      while (true) {
        const query = new URLSearchParams({
          lesson_key: lessonKey,
          limit: String(ATTACHMENTS_PAGE_SIZE),
          offset: String(offset),
        });
        const page = await request<RawAttachment[]>(`/attachments?${query}`);

        if (page.length > ATTACHMENTS_PAGE_SIZE) {
          throw new AttachmentPaginationContractError('Сервіс файлів повернув некоректну сторінку результатів.');
        }

        if (lessonAttachments.length + page.length > MAX_ATTACHMENTS_PER_LESSON) {
          throw new AttachmentPaginationContractError(
            `Для однієї пари доступно понад ${MAX_ATTACHMENTS_PER_LESSON} файлів.`,
          );
        }

        lessonAttachments.push(...page.map(normalizeAttachment));

        if (page.length < ATTACHMENTS_PAGE_SIZE) {
          return lessonAttachments;
        }

        offset += page.length;
      }
    }),
  );
  const successfulGroups = attachmentGroups.flatMap((result) => (result.status === 'fulfilled' ? [result.value] : []));
  const contractFailure = attachmentGroups.find(
    (result) => result.status === 'rejected' && result.reason instanceof AttachmentPaginationContractError,
  );

  if (contractFailure?.status === 'rejected') {
    throw contractFailure.reason;
  }

  if (!successfulGroups.length) {
    const firstFailure = attachmentGroups.find((result) => result.status === 'rejected');
    throw firstFailure?.reason || new Error('Не вдалося завантажити файли.');
  }

  const attachmentsById = new Map<string, LessonAttachment>();

  successfulGroups.flat().forEach((attachment) => attachmentsById.set(attachment.id, attachment));
  return Array.from(attachmentsById.values());
};

export const searchAttachments = async ({
  groupId,
  query,
  limit,
  offset,
}: {
  groupId: string;
  query: string;
  limit: number;
  offset: number;
}): Promise<AttachmentSearchPage> => {
  const search = new URLSearchParams({
    group_id: groupId,
    q: query,
    limit: String(limit),
    offset: String(offset),
  });
  const page = await request<RawAttachmentSearchPage>(`/attachments/search?${search}`);

  return {
    ...page,
    items: page.items.map((item) => ({
      ...normalizeAttachment(item),
      groupId: item.group_id,
      groupLabel: item.group_label,
      matchedFields: item.matched_fields,
    })),
  };
};

export const uploadAttachment = ({
  file,
  lessonKey,
  lessonTitle,
  scopeLabel,
  recordedAt,
  context,
  onProgress,
}: UploadAttachmentOptions): Promise<LessonAttachment> => {
  if (file.size > MAX_ATTACHMENT_UPLOAD_BYTES) {
    return Promise.reject(new Error('Файл завеликий. Максимальний розмір — 100 МБ.'));
  }

  const formData = new FormData();
  formData.append('file', file);
  formData.append('lesson_key', lessonKey);
  formData.append('lesson_title', lessonTitle);
  formData.append('scope_label', scopeLabel);
  formData.append('recorded_at', recordedAt);
  formData.append(
    'context_json',
    JSON.stringify({
      groups: context.groups,
      lecturer_name: context.lecturerName,
      lesson_keys: context.lessonKeys,
    }),
  );

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', getRecordingApiUrl('/attachments'));
    xhr.setRequestHeader(LOCAL_REQUEST_HEADER, LOCAL_REQUEST_VALUE);

    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) {
        onProgress?.(Math.round((event.loaded / event.total) * 100));
      }
    });

    xhr.addEventListener('load', () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        try {
          const payload = JSON.parse(xhr.responseText) as { detail?: string };
          reject(new Error(payload.detail || `HTTP ${xhr.status}`));
        } catch {
          reject(new Error(`HTTP ${xhr.status}`));
        }
        return;
      }

      try {
        resolve(normalizeAttachment(JSON.parse(xhr.responseText) as RawAttachment));
      } catch {
        reject(new Error('Сервіс повернув некоректну відповідь'));
      }
    });

    xhr.addEventListener('error', () => reject(new Error('Сервіс файлів недоступний')));
    xhr.addEventListener('abort', () => reject(new Error('Завантаження скасовано')));
    xhr.send(formData);
  });
};

export const getAttachmentContentUrl = (attachmentId: string) =>
  getRecordingApiUrl(`/attachments/${encodeURIComponent(attachmentId)}/content`);
