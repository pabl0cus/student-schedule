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
