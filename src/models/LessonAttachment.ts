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

export type AttachmentSearchField = 'lesson_title' | 'file_name';

export interface AttachmentSearchResult extends LessonAttachment {
  groupId: string;
  groupLabel: string;
  matchedFields: AttachmentSearchField[];
}

export interface AttachmentSearchPage {
  items: AttachmentSearchResult[];
  total: number;
  limit: number;
  offset: number;
}
