import { lazy, Suspense, useEffect, useId, useMemo, useRef, useState } from 'react';
import CaretDown from '../../assets/icons/caret-down.svg?react';
import { getAttachmentContentUrl } from '../../api/attachments';
import { useRecordingScope } from '../../common/context/useRecordingScope';
import { cn } from '../../common/utils/cn';
import { createLessonLookupKeys } from '../../common/utils/lessonKey';
import { Pair } from '../../models/Pair';
import { useLessonAttachments, useUploadAttachment } from '../../queries/useAttachments';
import { useLessonRecordings } from '../../queries/useRecordings';
import RecordingAttachment from './RecordingAttachment';
import { getErrorMessage } from '../../common/utils/getErrorMessage';
import { isRecordingActive } from '../../common/utils/recordingFormat';
import { createRecordingUploadContext } from '../../common/utils/recordingContext';

const AttachmentUploadDialog = lazy(() => import('./AttachmentUploadDialog'));

interface Props {
  date: string;
  day: string;
  pair: Pair;
}

const formatFileSize = (bytes: number) => {
  if (bytes < 1024) {
    return `${bytes} Б`;
  }

  if (bytes < 1024 * 1024) {
    return `${Math.max(0.1, bytes / 1024).toFixed(1)} КБ`;
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
};

const formatMaterialCount = (count: number) => {
  const lastTwoDigits = count % 100;
  const lastDigit = count % 10;

  if (lastTwoDigits >= 11 && lastTwoDigits <= 14) {
    return `${count} матеріалів`;
  }

  if (lastDigit === 1) {
    return `${count} матеріал`;
  }

  if (lastDigit >= 2 && lastDigit <= 4) {
    return `${count} матеріали`;
  }

  return `${count} матеріалів`;
};

const getFileTypeLabel = (fileName: string, mimeType: string) => {
  const extension = fileName.split('.').pop();
  if (extension && extension !== fileName && extension.length <= 6) {
    return extension.toLocaleUpperCase('uk-UA');
  }

  return mimeType.split('/').pop()?.toLocaleUpperCase('uk-UA') || 'ФАЙЛ';
};

const LessonMaterials = ({ pair, day, date }: Props) => {
  const scope = useRecordingScope();
  const panelId = `lesson-materials-${useId().replace(/:/g, '')}`;
  const [expanded, setExpanded] = useState(false);
  const [attachmentDialogOpen, setAttachmentDialogOpen] = useState(false);
  const [attachmentUploadProgress, setAttachmentUploadProgress] = useState(0);
  const [recordingUploading, setRecordingUploading] = useState(false);
  const requestedArchiveRef = useRef<string>();
  const uploadAttachmentMutation = useUploadAttachment();
  const scopeKey = scope?.scopeKey;
  const scheduleWeek = scope?.scheduleWeek;
  const lessonKeys = useMemo(() => {
    if (!scopeKey) {
      return [];
    }

    return createLessonLookupKeys({ scopeKey, occurrenceDate: date, week: scheduleWeek, day, pair });
  }, [date, day, pair, scheduleWeek, scopeKey]);
  const lessonKey = lessonKeys[0];
  const recordingsQuery = useLessonRecordings(lessonKeys, expanded && Boolean(scope?.isScheduleSnapshotReady));
  const attachmentsQuery = useLessonAttachments(lessonKeys, expanded && Boolean(scope?.isScheduleSnapshotReady));
  const recordings = useMemo(
    () => (recordingsQuery.data || []).filter((recording) => recording.recordedAt === date),
    [date, recordingsQuery.data],
  );
  const attachments = useMemo(
    () =>
      (attachmentsQuery.data || [])
        .filter((attachment) => attachment.recordedAt === date)
        .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt)),
    [attachmentsQuery.data, date],
  );
  const materialCount = recordings.length + attachments.length;
  const queriesStarted = recordingsQuery.data !== undefined || attachmentsQuery.data !== undefined;
  const queriesLoading = recordingsQuery.isLoading || attachmentsQuery.isLoading;
  const queriesFailed = recordingsQuery.isError || attachmentsQuery.isError;
  const processingRecording = recordings.some(isRecordingActive);
  const isBusy = recordingUploading || uploadAttachmentMutation.isLoading || Boolean(scope?.isArchivingSchedule);

  useEffect(() => {
    const archiveRequestKey = scopeKey && `${scopeKey}:${scope?.weekStart || date}`;
    if (
      !expanded ||
      !archiveRequestKey ||
      !scope?.ensureScheduleArchived ||
      scope.isScheduleArchived ||
      requestedArchiveRef.current === archiveRequestKey
    ) {
      return;
    }

    requestedArchiveRef.current = archiveRequestKey;
    void scope
      .ensureScheduleArchived()
      .then(() => Promise.all([recordingsQuery.refetch(), attachmentsQuery.refetch()]))
      .catch(() => undefined);
  }, [attachmentsQuery, date, expanded, recordingsQuery, scope, scopeKey]);

  if (!lessonKey || !scope) {
    return null;
  }

  if (!scope.isScheduleSnapshotReady) {
    return (
      <div
        className="mt-3 flex min-h-9 items-center justify-between gap-3 border-t border-neutral-200/80 px-1.5 pt-1.5 text-xs text-neutral-600"
        role={scope.archiveError ? 'alert' : 'status'}
        aria-live="polite"
      >
        <span className="font-semibold">Матеріали</span>
        <span>{scope.isResolvingScheduleSnapshot ? 'Перевіряємо архів…' : 'Архів тимчасово недоступний'}</span>
      </div>
    );
  }

  const openAttachmentDialog = () => {
    uploadAttachmentMutation.reset();
    setExpanded(true);
    setAttachmentDialogOpen(true);
  };

  const toggleExpanded = () => {
    if (!expanded) {
      requestedArchiveRef.current = undefined;
    }
    setExpanded((current) => !current);
  };

  const handleAttachmentUpload = async (file: File) => {
    setExpanded(true);
    setAttachmentUploadProgress(0);
    await scope.ensureScheduleArchived();
    uploadAttachmentMutation.mutate({
      file,
      lessonKey,
      lessonTitle: pair.name,
      scopeLabel: scope.label,
      recordedAt: date,
      context: createRecordingUploadContext(scope, pair, lessonKeys),
      onProgress: setAttachmentUploadProgress,
    });
  };

  const status = uploadAttachmentMutation.isLoading
    ? `Додаємо файл · ${attachmentUploadProgress}%`
    : recordingUploading
      ? 'Додаємо запис'
      : scope.isArchivingSchedule
        ? 'Закріплюємо розклад…'
        : processingRecording
          ? `${formatMaterialCount(materialCount)} · обробка запису`
          : queriesLoading && !queriesStarted
            ? 'Завантаження…'
            : queriesFailed && !materialCount
              ? 'Недоступно'
              : queriesStarted
                ? `${formatMaterialCount(materialCount)}${queriesFailed ? ' · не оновлено' : ''}`
                : undefined;

  return (
    <div className="mt-3 border-t border-neutral-200/80 pt-1.5">
      <button
        type="button"
        className="flex min-h-9 w-full cursor-pointer items-center justify-between gap-3 rounded-lg px-1.5 text-left text-xs text-neutral-700 transition-colors hover:bg-neutral-50 hover:text-primary-font disabled:cursor-wait disabled:opacity-70 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
        aria-controls={panelId}
        aria-expanded={expanded}
        disabled={expanded && isBusy}
        onClick={toggleExpanded}
      >
        <span className="font-semibold">Матеріали</span>
        <span className="flex min-w-0 items-center gap-2">
          {status && (
            <span className="truncate text-[11px] text-neutral-600" aria-live="polite">
              {status}
            </span>
          )}
          <CaretDown
            className={cn('size-3.5 shrink-0 transition-transform duration-200', expanded && 'rotate-180')}
            aria-hidden="true"
          />
        </span>
      </button>

      <div id={panelId} hidden={!expanded}>
        <div className="pb-1">
          <div className="mt-2 flex items-center justify-between gap-3 px-0.5">
            <span className="text-xs font-semibold text-neutral-700">Записи заняття</span>
            <span className="text-[11px] text-neutral-600">аудіо чи відео · до 500 МБ</span>
          </div>
          <RecordingAttachment
            pair={pair}
            day={day}
            date={date}
            enabled={expanded}
            onUploadStateChange={setRecordingUploading}
          />

          <section className="mt-4 border-t border-neutral-200 pt-3" aria-labelledby={`${panelId}-files-title`}>
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <h3 id={`${panelId}-files-title`} className="text-xs font-semibold text-neutral-700">
                  Файли
                </h3>
                <div className="mt-0.5 text-[11px] text-neutral-600">будь-який формат · до 100 МБ</div>
              </div>
              <button
                type="button"
                className="shrink-0 cursor-pointer rounded-lg border border-neutral-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-neutral-700 hover:border-basic-blue hover:bg-brand-00 hover:text-basic-blue disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
                disabled={uploadAttachmentMutation.isLoading || scope.isArchivingSchedule}
                onClick={openAttachmentDialog}
              >
                + Додати файл
              </button>
            </div>

            {uploadAttachmentMutation.isLoading && (
              <div className="mt-3 rounded-xl border border-neutral-200 bg-neutral-50 p-3" aria-live="polite">
                <div className="flex items-center justify-between gap-3 text-xs">
                  <span className="min-w-0 truncate font-semibold text-primary-font">Завантажуємо файл</span>
                  <span className="shrink-0 text-neutral-700">{attachmentUploadProgress}%</span>
                </div>
                <div
                  className="mt-2 h-1.5 overflow-hidden rounded-full bg-neutral-200"
                  role="progressbar"
                  aria-label="Прогрес завантаження файлу"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={attachmentUploadProgress}
                >
                  <div
                    className="h-full rounded-full bg-basic-blue transition-[width]"
                    style={{ width: `${attachmentUploadProgress}%` }}
                  />
                </div>
              </div>
            )}

            {attachmentsQuery.isLoading && !attachmentsQuery.data ? (
              <div
                className="mt-3 h-12 animate-pulse rounded-xl border border-neutral-200 bg-neutral-100"
                aria-label="Завантажуємо файли"
                aria-busy="true"
              />
            ) : attachments.length ? (
              <ul className="mt-3 space-y-2">
                {attachments.map((attachment) => (
                  <li
                    className="flex min-w-0 items-center gap-2.5 rounded-xl border border-neutral-200 bg-white p-2.5"
                    key={attachment.id}
                  >
                    <span
                      className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-00 px-1 text-[10px] font-bold text-basic-blue"
                      aria-hidden="true"
                    >
                      {getFileTypeLabel(attachment.fileName, attachment.mimeType)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span
                        className="block truncate text-xs font-semibold text-primary-font"
                        title={attachment.fileName}
                      >
                        {attachment.fileName}
                      </span>
                      <span className="mt-0.5 block truncate text-[11px] text-neutral-600">
                        {formatFileSize(attachment.sizeBytes)} · {attachment.mimeType}
                      </span>
                    </span>
                    <a
                      className="shrink-0 rounded-lg px-2 py-1.5 text-xs font-semibold text-basic-blue hover:bg-brand-00 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
                      download={attachment.fileName}
                      href={getAttachmentContentUrl(attachment.id)}
                      aria-label={`Завантажити файл «${attachment.fileName}»`}
                    >
                      Завантажити
                    </a>
                  </li>
                ))}
              </ul>
            ) : !attachmentsQuery.isError ? (
              <div className="mt-3 rounded-xl border border-dashed border-neutral-300 px-3 py-2.5 text-xs text-neutral-600">
                Файлів поки немає.
              </div>
            ) : null}

            {attachmentsQuery.isError && (
              <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3" role="alert">
                <div className="text-xs font-semibold text-red-700">
                  {attachments.length ? 'Не вдалося оновити список файлів.' : 'Сервіс файлів недоступний'}
                </div>
                {!attachments.length && (
                  <div className="mt-1 text-xs leading-4 text-neutral-700">
                    {getErrorMessage(attachmentsQuery.error)}
                  </div>
                )}
                <button
                  type="button"
                  className="mt-2 cursor-pointer text-xs font-semibold text-red-700 underline underline-offset-2 disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={attachmentsQuery.isFetching}
                  onClick={() => void attachmentsQuery.refetch()}
                >
                  {attachmentsQuery.isFetching ? 'Оновлюємо…' : 'Спробувати знову'}
                </button>
              </div>
            )}

            {(scope.archiveError || uploadAttachmentMutation.isError) && (
              <div className="mt-2 text-xs leading-4 text-red-700" role="alert">
                {getErrorMessage(scope.archiveError || uploadAttachmentMutation.error)}
              </div>
            )}
          </section>
        </div>
      </div>

      {attachmentDialogOpen && (
        <Suspense fallback={null}>
          <AttachmentUploadDialog
            open
            onOpenChange={setAttachmentDialogOpen}
            onSubmit={handleAttachmentUpload}
            lessonTitle={pair.name}
            recordedAt={date}
            scopeLabel={scope.label}
            day={day}
            time={pair.time}
          />
        </Suspense>
      )}
    </div>
  );
};

export default LessonMaterials;
