export type RecordingStatus = 'queued' | 'processing' | 'ready' | 'failed';

export interface TranscriptWord {
  start: number;
  end: number;
  word: string;
  probability?: number;
}

export interface TranscriptSegment {
  id: number;
  start: number;
  end: number;
  text: string;
  words: TranscriptWord[];
}

export interface Recording {
  id: string;
  lessonKey: string;
  lessonTitle: string;
  scopeLabel: string;
  fileName: string;
  mimeType: string;
  status: RecordingStatus;
  progress: number;
  createdAt: string;
  recordedAt: string;
  durationSeconds?: number;
  language?: string;
  error?: string;
  transcript?: TranscriptSegment[];
}

export type RecordingSearchScope = 'all' | 'subject' | 'lecturer' | 'transcript';
export type RecordingSearchField = 'lesson_title' | 'lecturer_name' | 'transcript';

export interface RecordingSearchResult extends Recording {
  groupId: string;
  groupLabel: string;
  lecturerName?: string;
  matchedFields: RecordingSearchField[];
  matchSegment?: TranscriptSegment;
}

export interface RecordingSearchPage {
  items: RecordingSearchResult[];
  total: number;
  limit: number;
  offset: number;
}
