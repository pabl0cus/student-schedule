import { Recording, TranscriptSegment, TranscriptWord } from '../models/Recording';

export const RECORDINGS_API_BASE_URL = (import.meta.env.VITE_RECORDINGS_API_URL || '/recordings-api').replace(
  /\/$/,
  '',
);
export const LOCAL_REQUEST_HEADER = 'X-KPI-Local-Request';
export const LOCAL_REQUEST_VALUE = '1';
export const LOCAL_REQUEST_HEADERS = { [LOCAL_REQUEST_HEADER]: LOCAL_REQUEST_VALUE } as const;

export interface RawTranscriptWord {
  start: number;
  end: number;
  word: string;
  probability?: number;
}

export interface RawTranscriptSegment {
  id: number;
  start: number;
  end: number;
  text: string;
  words?: RawTranscriptWord[];
}

export interface RawRecording {
  id: string;
  lesson_key: string;
  lesson_title: string;
  scope_label: string;
  file_name: string;
  mime_type: string;
  status: Recording['status'];
  progress: number;
  created_at: string;
  recorded_at?: string | null;
  duration_seconds?: number | null;
  language?: string | null;
  error?: string | null;
  transcript?: RawTranscriptSegment[] | null;
}

const normalizeWord = (word: RawTranscriptWord): TranscriptWord => ({
  start: word.start,
  end: word.end,
  word: word.word,
  probability: word.probability,
});

const normalizeSegment = (segment: RawTranscriptSegment): TranscriptSegment => ({
  id: segment.id,
  start: segment.start,
  end: segment.end,
  text: segment.text,
  words: segment.words?.map(normalizeWord) || [],
});

export const normalizeRecording = (recording: RawRecording): Recording => ({
  id: recording.id,
  lessonKey: recording.lesson_key,
  lessonTitle: recording.lesson_title,
  scopeLabel: recording.scope_label,
  fileName: recording.file_name,
  mimeType: recording.mime_type,
  status: recording.status,
  progress: recording.progress,
  createdAt: recording.created_at,
  recordedAt: recording.recorded_at || recording.created_at,
  durationSeconds: recording.duration_seconds ?? undefined,
  language: recording.language ?? undefined,
  error: recording.error ?? undefined,
  transcript: recording.transcript?.map(normalizeSegment),
});

export const parseApiError = async (response: Response) => {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail || `HTTP ${response.status}`;
  } catch {
    return `HTTP ${response.status}`;
  }
};

export const getRecordingApiUrl = (path: string) => `${RECORDINGS_API_BASE_URL}${path}`;
