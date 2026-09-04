export interface LessonAttachment {
  id: string;
  lessonKey: string;
  lessonTitle: string;
  scopeLabel: string;
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  recordedAt: string;
  createdAt: string;
  updatedAt?: string;
  contentUrl?: string;
}
