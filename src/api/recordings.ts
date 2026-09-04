import { Recording } from '../models/Recording';
import { MAX_RECORDING_UPLOAD_BYTES } from '../common/constants/uploadLimits';
import {
  getRecordingApiUrl,
  LOCAL_REQUEST_HEADERS,
  LOCAL_REQUEST_HEADER,
  LOCAL_REQUEST_VALUE,
  normalizeRecording,
  parseApiError,
  RawRecording,
} from './recordingsShared';

interface UploadRecordingOptions {
  file: File;
  lessonKey: string;
  lessonTitle: string;
  scopeLabel: string;
  recordedAt: string;
  onProgress?: (progress: number) => void;
}

const request = async <T>(path: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(getRecordingApiUrl(path), options);

  if (!response.ok) {
    throw new Error(await parseApiError(response));
  }

  return response.json() as Promise<T>;
};

export const getLessonRecordings = async (lessonKeys: string | string[]): Promise<Recording[]> => {
  const uniqueKeys = Array.from(new Set(Array.isArray(lessonKeys) ? lessonKeys : [lessonKeys]));
  if (!uniqueKeys.length) {
    return [];
  }

  const recordingGroups = await Promise.allSettled(
    uniqueKeys.map(async (lessonKey) => {
      const query = new URLSearchParams({ lesson_key: lessonKey });
      const recordings = await request<RawRecording[]>(`/recordings?${query}`);
      return recordings.map(normalizeRecording);
    }),
  );
  const successfulGroups = recordingGroups.flatMap((result) => (result.status === 'fulfilled' ? [result.value] : []));

  if (!successfulGroups.length) {
    const firstFailure = recordingGroups.find((result) => result.status === 'rejected');
    throw firstFailure?.reason || new Error('Не вдалося завантажити записи.');
  }

  const recordingsById = new Map<string, Recording>();

  successfulGroups.flat().forEach((recording) => recordingsById.set(recording.id, recording));
  return Array.from(recordingsById.values());
};

export const getRecording = async (recordingId: string): Promise<Recording> => {
  const recording = await request<RawRecording>(`/recordings/${encodeURIComponent(recordingId)}`);
  return normalizeRecording(recording);
};

export const uploadRecording = ({
  file,
  lessonKey,
  lessonTitle,
  scopeLabel,
  recordedAt,
  onProgress,
}: UploadRecordingOptions): Promise<Recording> => {
  if (file.size > MAX_RECORDING_UPLOAD_BYTES) {
    return Promise.reject(new Error('Файл запису завеликий. Максимальний розмір — 500 МБ.'));
  }

  const formData = new FormData();
  formData.append('file', file);
  formData.append('lesson_key', lessonKey);
  formData.append('lesson_title', lessonTitle);
  formData.append('scope_label', scopeLabel);
  formData.append('recorded_at', recordedAt);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', getRecordingApiUrl('/recordings'));
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
        resolve(normalizeRecording(JSON.parse(xhr.responseText) as RawRecording));
      } catch {
        reject(new Error('Сервіс повернув некоректну відповідь'));
      }
    });

    xhr.addEventListener('error', () => reject(new Error('Сервіс транскрибації недоступний')));
    xhr.addEventListener('abort', () => reject(new Error('Завантаження скасовано')));
    xhr.send(formData);
  });
};

export const retryRecording = async (recordingId: string): Promise<Recording> => {
  const recording = await request<RawRecording>(`/recordings/${encodeURIComponent(recordingId)}/retry`, {
    headers: LOCAL_REQUEST_HEADERS,
    method: 'POST',
  });
  return normalizeRecording(recording);
};

export const getRecordingMediaUrl = (recordingId: string) =>
  getRecordingApiUrl(`/recordings/${encodeURIComponent(recordingId)}/media`);

export const getRecordingDownloadUrl = (recordingId: string) =>
  getRecordingApiUrl(`/recordings/${encodeURIComponent(recordingId)}/download`);
