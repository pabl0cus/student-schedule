import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useRecordingScope } from '../../common/context/useRecordingScope';
import { createLessonLookupKeys } from '../../common/utils/lessonKey';
import { formatLessonDate, formatTimestamp, isRecordingActive } from '../../common/utils/recordingFormat';
import { Pair } from '../../models/Pair';
import { Recording } from '../../models/Recording';
import { useLessonRecordings, useRetryRecording, useUploadRecording } from '../../queries/useRecordings';
import { getErrorMessage } from '../../common/utils/getErrorMessage';

const RecordingUploadDialog = lazy(() => import('./RecordingUploadDialog'));

interface Props {
  date: string;
  day: string;
  enabled?: boolean;
  pair: Pair;
  onUploadStateChange?: (uploading: boolean) => void;
}

const Waveform = () => {
  const bars = [7, 13, 9, 18, 12, 22, 15, 10, 19, 14, 8, 17, 11, 20, 9, 14];

  return (
    <span className="flex h-6 items-center gap-0.5" aria-hidden="true">
      {bars.map((height, index) => (
        <span className="w-0.5 rounded-full bg-basic-blue/45" style={{ height }} key={`${height}-${index}`} />
      ))}
    </span>
  );
};

const RecordingPreview = ({
  recording,
  count,
  onOpen,
}: {
  recording: Recording;
  count: number;
  onOpen: () => void;
}) => (
  <button
    type="button"
    className="min-w-0 flex-1 cursor-pointer rounded-xl border border-basic-blue/15 bg-brand-00 p-2.5 text-left transition-colors hover:border-basic-blue/35 hover:bg-blue-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
    onClick={onOpen}
    aria-label={`Відкрити запис пари «${recording.lessonTitle}» за ${formatLessonDate(recording.recordedAt)}`}
  >
    <span className="flex items-center gap-2.5">
      <span
        className="flex size-9 shrink-0 items-center justify-center rounded-full bg-basic-blue text-sm text-white shadow-sm"
        aria-hidden="true"
      >
        ▶
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-bold text-primary-font">Запис пари</span>
          {recording.durationSeconds !== undefined && (
            <span className="shrink-0 text-xs font-semibold text-neutral-700">
              {formatTimestamp(recording.durationSeconds)}
            </span>
          )}
        </span>
        <span className="mt-1 flex items-center justify-between gap-2">
          <span className="truncate text-xs text-neutral-700">{formatLessonDate(recording.recordedAt)}</span>
          {count > 1 ? (
            <span className="shrink-0 text-xs font-semibold text-basic-blue">Ще {count - 1}</span>
          ) : (
            <Waveform />
          )}
        </span>
      </span>
    </span>
  </button>
);

const ProcessingPreview = ({ recording }: { recording: Recording }) => {
  return (
    <span
      className="block flex-1 rounded-xl border border-neutral-200 bg-neutral-50 p-3"
      role="status"
      aria-live="polite"
    >
      <span className="flex items-center justify-between gap-3 text-xs">
        <span className="font-semibold text-primary-font">
          {recording.status === 'queued' ? 'У черзі на транскрибацію' : 'Розпізнаємо українську мову'}
        </span>
        <span className="size-3 shrink-0 animate-pulse rounded-full bg-basic-blue" aria-hidden="true" />
      </span>
      <span className="mt-2 block h-1.5 overflow-hidden rounded-full bg-neutral-200" aria-hidden="true">
        <span className="block h-full w-1/2 animate-pulse rounded-full bg-basic-blue" />
      </span>
    </span>
  );
};

const RecordingAttachment = ({ pair, day, date, enabled = true, onUploadStateChange }: Props) => {
  const scope = useRecordingScope();
  const [searchParams, setSearchParams] = useSearchParams();
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const requestedArchiveRef = useRef<string>();
  const uploadMutation = useUploadRecording();
  const retryMutation = useRetryRecording();
  const scopeKey = scope?.scopeKey;
  const scheduleWeek = scope?.scheduleWeek;
  const lessonKeys = useMemo(() => {
    if (!scopeKey) {
      return [];
    }

    return createLessonLookupKeys({ scopeKey, occurrenceDate: date, week: scheduleWeek, day, pair });
  }, [date, day, pair, scheduleWeek, scopeKey]);
  const lessonKey = lessonKeys[0];
  const {
    data: recordings = [],
    error: recordingsQueryError,
    isError: recordingsQueryFailed,
    isFetching: recordingsFetching,
    isLoading: recordingsLoading,
    refetch: refetchRecordings,
  } = useLessonRecordings(lessonKeys, enabled);
  const occurrenceRecordings = useMemo(
    () => recordings.filter((recording) => recording.recordedAt === date),
    [date, recordings],
  );
  const orderedRecordings = useMemo(
    () => [...occurrenceRecordings].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt)),
    [occurrenceRecordings],
  );
  const latestRecording = orderedRecordings[0];

  useEffect(() => {
    onUploadStateChange?.(uploadMutation.isLoading);
  }, [onUploadStateChange, uploadMutation.isLoading]);

  useEffect(() => {
    const archiveRequestKey = scopeKey && `${scopeKey}:${scope?.weekStart || date}`;
    if (
      !latestRecording ||
      !archiveRequestKey ||
      !scope?.ensureScheduleArchived ||
      scope.isScheduleArchived ||
      requestedArchiveRef.current === archiveRequestKey
    ) {
      return;
    }

    requestedArchiveRef.current = archiveRequestKey;
    void scope.ensureScheduleArchived().catch(() => undefined);
  }, [date, latestRecording, scope, scopeKey]);

  if (!lessonKey || !scope) {
    return null;
  }

  const openUploadDialog = () => {
    uploadMutation.reset();
    setUploadDialogOpen(true);
  };

  const openPlayer = (recordingId: string) => {
    const nextSearchParams = new URLSearchParams(searchParams);
    nextSearchParams.set('recordingId', recordingId);
    setSearchParams(nextSearchParams, { replace: true });
  };

  const handleUpload = async (file: File) => {
    setUploadProgress(0);
    await scope.ensureScheduleArchived?.();
    uploadMutation.mutate({
      file,
      lessonKey,
      lessonTitle: pair.name,
      scopeLabel: scope.label,
      recordedAt: date,
      onProgress: setUploadProgress,
    });
  };

  const mutationError = scope.archiveError
    ? getErrorMessage(scope.archiveError)
    : uploadMutation.isError
      ? getErrorMessage(uploadMutation.error)
      : retryMutation.isError
        ? getErrorMessage(retryMutation.error)
        : undefined;

  return (
    <div className="mt-4">
      {recordingsQueryFailed && !orderedRecordings.length ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-3" role="alert">
          <div className="text-xs font-semibold text-red-700">Сервіс записів недоступний</div>
          <div className="mt-1 text-xs leading-4 text-neutral-700">{getErrorMessage(recordingsQueryError)}</div>
          <button
            type="button"
            className="mt-2 cursor-pointer rounded-lg bg-basic-blue px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
            disabled={recordingsFetching}
            onClick={() => void refetchRecordings()}
          >
            {recordingsFetching ? 'Повторюємо…' : 'Спробувати знову'}
          </button>
        </div>
      ) : recordingsLoading ? (
        <div
          className="h-14 animate-pulse rounded-xl border border-neutral-200 bg-neutral-100"
          aria-label="Завантажуємо записи"
          aria-busy="true"
        />
      ) : uploadMutation.isLoading ? (
        <div className="rounded-xl border border-neutral-200 bg-neutral-50 p-3" aria-live="polite">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="font-semibold text-primary-font">Додаємо запис</span>
            <span className="text-neutral-700">{uploadProgress}%</span>
          </div>
          <div
            className="mt-2 h-1.5 overflow-hidden rounded-full bg-neutral-200"
            role="progressbar"
            aria-label="Прогрес завантаження"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={uploadProgress}
          >
            <div
              className="h-full rounded-full bg-basic-blue transition-[width]"
              style={{ width: `${uploadProgress}%` }}
            />
          </div>
        </div>
      ) : latestRecording?.status === 'ready' ? (
        <div className="flex gap-2">
          <RecordingPreview
            recording={latestRecording}
            count={orderedRecordings.length}
            onOpen={() => openPlayer(latestRecording.id)}
          />
          <button
            type="button"
            className="flex w-11 shrink-0 cursor-pointer items-center justify-center rounded-xl border border-neutral-300 bg-white text-xl text-neutral-700 hover:border-basic-blue hover:bg-brand-00 hover:text-basic-blue focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
            onClick={openUploadDialog}
            aria-label="Додати ще один запис"
            title="Додати ще один запис"
          >
            +
          </button>
        </div>
      ) : isRecordingActive(latestRecording) ? (
        <div className="flex gap-2">
          <button
            type="button"
            className="min-w-0 flex-1 cursor-pointer text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
            onClick={() => openPlayer(latestRecording.id)}
          >
            <ProcessingPreview recording={latestRecording} />
          </button>
          <button
            type="button"
            className="flex w-11 shrink-0 cursor-pointer items-center justify-center rounded-xl border border-neutral-300 bg-white text-xl text-neutral-700 hover:border-basic-blue hover:text-basic-blue focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
            onClick={openUploadDialog}
            aria-label="Додати ще один запис"
            title="Додати ще один запис"
          >
            +
          </button>
        </div>
      ) : latestRecording?.status === 'failed' ? (
        <div className="space-y-2">
          <div className="flex gap-2">
            <RecordingPreview
              recording={latestRecording}
              count={orderedRecordings.length}
              onOpen={() => openPlayer(latestRecording.id)}
            />
            <button
              type="button"
              className="flex w-11 shrink-0 cursor-pointer items-center justify-center rounded-xl border border-neutral-300 bg-white text-xl text-neutral-700 hover:border-basic-blue hover:text-basic-blue focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
              onClick={openUploadDialog}
              aria-label="Додати ще один запис"
              title="Додати ще один запис"
            >
              +
            </button>
          </div>
          <div className="rounded-xl border border-red-200 bg-red-50 p-3">
            <div className="text-xs font-semibold text-red-700">Запис доступний, але транскрипція не вдалася</div>
            <button
              type="button"
              className="mt-2 cursor-pointer rounded-lg bg-basic-blue px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
              disabled={retryMutation.isLoading}
              onClick={() => retryMutation.mutate(latestRecording.id)}
            >
              {retryMutation.isLoading ? 'Повторюємо…' : 'Повторити транскрибацію'}
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          className="flex min-h-14 w-full cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-neutral-300 bg-neutral-50 px-3 py-2.5 text-sm font-semibold text-neutral-700 transition-colors hover:border-basic-blue hover:bg-brand-00 hover:text-basic-blue focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
          onClick={openUploadDialog}
          aria-label={`Додати запис пари «${pair.name}»`}
        >
          <span
            className="flex size-7 items-center justify-center rounded-full bg-white text-xl leading-none shadow-sm"
            aria-hidden="true"
          >
            +
          </span>
          Додати запис
        </button>
      )}

      {recordingsQueryFailed && orderedRecordings.length > 0 && (
        <div className="mt-2 flex items-center justify-between gap-2 text-xs leading-4 text-red-700" role="alert">
          <span>Не вдалося оновити список записів.</span>
          <button
            type="button"
            className="cursor-pointer font-semibold underline underline-offset-2 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={recordingsFetching}
            onClick={() => void refetchRecordings()}
          >
            {recordingsFetching ? 'Оновлюємо…' : 'Повторити'}
          </button>
        </div>
      )}

      {mutationError && (
        <div className="mt-2 text-xs leading-4 text-red-700" role="alert">
          {mutationError}
        </div>
      )}

      {uploadDialogOpen && (
        <Suspense fallback={null}>
          <RecordingUploadDialog
            open
            onOpenChange={setUploadDialogOpen}
            onSubmit={handleUpload}
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

export default RecordingAttachment;
