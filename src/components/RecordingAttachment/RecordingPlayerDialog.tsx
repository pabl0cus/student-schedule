import * as DialogPrimitive from '@radix-ui/react-dialog';
import { useEffect, useMemo, useRef, useState } from 'react';
import Link from '../../assets/icons/link.svg?react';
import X from '../../assets/icons/x.svg?react';
import { getRecordingDownloadUrl, getRecordingMediaUrl } from '../../api/recordings';
import { routes } from '../../common/constants/routes';
import { cn } from '../../common/utils/cn';
import {
  formatLessonDate,
  formatTimestamp,
  isRecordingActive,
  isVideoRecording,
  normalizeTranscriptSearch,
} from '../../common/utils/recordingFormat';
import { Recording, TranscriptSegment } from '../../models/Recording';
import { useRecording } from '../../queries/useRecordings';
import { getErrorMessage } from '../../common/utils/getErrorMessage';

interface Props {
  initialRecordingId?: string;
  initialTime?: number;
  open: boolean;
  recordings: Recording[];
  onOpenChange: (open: boolean) => void;
}

type CopyLinkState = { status: 'idle' } | { status: 'success' } | { status: 'manual'; url: string };

const PUBLIC_SCHEDULE_LINK_PARAMS: Record<string, string[]> = {
  [routes.INDEX]: ['groupId', 'week'],
  [routes.SESSION]: ['groupId'],
  [routes.LECTURER]: ['lecturerId', 'week'],
  [routes.RECORDINGS]: ['groupId', 'q', 'scope'],
};

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const createSearchPattern = (value: string) =>
  value
    .split('')
    .map((character) => (/['’ʼ`]/.test(character) ? "['’ʼ`]" : escapeRegExp(character)))
    .join('');

const getPublicRecordingLink = (recordingId: string) => {
  const currentUrl = new URL(window.location.href);
  const normalizedPath = currentUrl.pathname.replace(/\/+$/, '') || '/';
  const allowedParams = PUBLIC_SCHEDULE_LINK_PARAMS[normalizedPath];
  const publicUrl = new URL(allowedParams ? normalizedPath : '/', currentUrl.origin);

  allowedParams?.forEach((param) => {
    const value = currentUrl.searchParams.get(param);
    if (value) {
      publicUrl.searchParams.set(param, value);
    }
  });
  publicUrl.searchParams.set('recordingId', recordingId);

  return publicUrl.toString();
};

const getTranscriptText = (segments: TranscriptSegment[]) =>
  segments
    .map((segment) => segment.text.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\n\n');

const getTranscriptFilename = (recording: Recording) => {
  const extensionIndex = recording.fileName.lastIndexOf('.');
  const originalStem = extensionIndex > 0 ? recording.fileName.slice(0, extensionIndex) : recording.fileName;
  const cleanedStem = Array.from(originalStem)
    .filter((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      const isControlCharacter = codePoint < 32 || (codePoint >= 127 && codePoint <= 159);
      const isInvisibleFormatControl =
        codePoint === 0x061c ||
        (codePoint >= 0x200b && codePoint <= 0x200f) ||
        (codePoint >= 0x202a && codePoint <= 0x202e) ||
        (codePoint >= 0x2060 && codePoint <= 0x206f) ||
        codePoint === 0xfeff;
      return !isControlCharacter && !isInvisibleFormatControl;
    })
    .join('')
    .normalize('NFC')
    .replace(/[<>:"/\\|?*]/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/[. ]+$/g, '')
    .trim();
  const normalizedStem = Array.from(cleanedStem)
    .slice(0, 100)
    .join('')
    .replace(/[. ]+$/g, '');
  const safeStem = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(normalizedStem)
    ? `запис-${recording.id.slice(0, 8)}`
    : normalizedStem || `запис-${recording.id.slice(0, 8)}`;

  return `${safeStem}-транскрипція.txt`;
};

const DownloadIcon = () => (
  <svg aria-hidden="true" className="size-4 shrink-0" viewBox="0 0 20 20" fill="none">
    <path
      d="M10 2.5v10m0 0 3.5-3.5M10 12.5 6.5 9M3 15.5h14"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

const HighlightedText = ({ text, query }: { text: string; query: string }) => {
  const normalizedQuery = query.trim();

  if (!normalizedQuery) {
    return text;
  }

  const parts = text.split(new RegExp(`(${createSearchPattern(normalizedQuery)})`, 'giu'));

  return parts.map((part, index) =>
    normalizeTranscriptSearch(part) === normalizeTranscriptSearch(normalizedQuery) ? (
      <mark className="rounded-sm bg-yellow-200 px-0.5 text-inherit" key={`${part}-${index}`}>
        {part}
      </mark>
    ) : (
      part
    ),
  );
};

const RecordingPlayerDialog = ({ initialRecordingId, initialTime, open, recordings, onOpenChange }: Props) => {
  const [selectedRecordingId, setSelectedRecordingId] = useState(initialRecordingId);
  const [query, setQuery] = useState('');
  const [currentTime, setCurrentTime] = useState(0);
  const [mediaError, setMediaError] = useState<string>();
  const [copyLinkState, setCopyLinkState] = useState<CopyLinkState>({ status: 'idle' });
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const pendingInitialTimeRef = useRef<number>();
  const activeSegmentRef = useRef<HTMLButtonElement | null>(null);
  const manualLinkRef = useRef<HTMLInputElement | null>(null);
  const {
    data: recordingDetails,
    error: recordingDetailsQueryError,
    isError: recordingDetailsQueryFailed,
    isFetching: recordingDetailsFetching,
    isLoading: recordingDetailsLoading,
    refetch: refetchRecordingDetails,
  } = useRecording(selectedRecordingId, open);

  useEffect(() => {
    if (open) {
      setSelectedRecordingId(initialRecordingId);
      setQuery('');
      const startTime = Math.max(0, initialTime || 0);
      pendingInitialTimeRef.current = startTime;
      setCurrentTime(startTime);
      setMediaError(undefined);
      setCopyLinkState({ status: 'idle' });
    }
  }, [initialRecordingId, initialTime, open]);

  useEffect(() => {
    setMediaError(undefined);
    setCopyLinkState({ status: 'idle' });
    setQuery('');
    const startTime = selectedRecordingId === initialRecordingId ? Math.max(0, initialTime || 0) : 0;
    pendingInitialTimeRef.current = startTime;
    setCurrentTime(startTime);
  }, [initialRecordingId, initialTime, selectedRecordingId]);

  const selectedSummary = recordings.find((recording) => recording.id === selectedRecordingId);
  const recording = recordingDetails || selectedSummary;
  const recordingDetailsUnavailable = recordingDetailsQueryFailed && !recordingDetails;
  const transcript = useMemo(() => recording?.transcript || [], [recording?.transcript]);
  const transcriptText = useMemo(() => getTranscriptText(transcript), [transcript]);
  const normalizedQuery = normalizeTranscriptSearch(query);
  const visibleSegments = useMemo(
    () =>
      normalizedQuery
        ? transcript.filter((segment) => normalizeTranscriptSearch(segment.text).includes(normalizedQuery))
        : transcript,
    [normalizedQuery, transcript],
  );
  const activeSegmentId = transcript.find((segment) => currentTime >= segment.start && currentTime < segment.end)?.id;

  useEffect(() => {
    if (!normalizedQuery && activeSegmentRef.current) {
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      activeSegmentRef.current.scrollIntoView({ block: 'nearest', behavior: reduceMotion ? 'auto' : 'smooth' });
    }
  }, [activeSegmentId, normalizedQuery]);

  useEffect(() => {
    if (copyLinkState.status === 'manual') {
      manualLinkRef.current?.focus();
      manualLinkRef.current?.select();
    }
  }, [copyLinkState]);

  const seekToSegment = (segment: TranscriptSegment) => {
    if (!mediaRef.current) {
      return;
    }

    mediaRef.current.currentTime = segment.start;
    void mediaRef.current.play();
  };

  const applyInitialTime = (media: HTMLMediaElement) => {
    const requestedTime = pendingInitialTimeRef.current;
    if (requestedTime === undefined) {
      return;
    }
    const duration = Number.isFinite(media.duration) ? media.duration : requestedTime;
    const nextTime = Math.min(requestedTime, Math.max(0, duration));
    media.currentTime = nextTime;
    setCurrentTime(nextTime);
    pendingInitialTimeRef.current = undefined;
  };

  const copyRecordingLink = async () => {
    if (!recording) {
      return;
    }

    const link = getPublicRecordingLink(recording.id);

    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error('Clipboard API is unavailable');
      }
      await navigator.clipboard.writeText(link);
      setCopyLinkState({ status: 'success' });
    } catch {
      setCopyLinkState({ status: 'manual', url: link });
    }
  };

  const downloadTranscript = () => {
    if (!recording || !transcriptText) {
      return;
    }

    const objectUrl = URL.createObjectURL(new Blob([`${transcriptText}\n`], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = getTranscriptFilename(recording);
    link.hidden = true;
    document.body.append(link);
    try {
      link.click();
    } finally {
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    }
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[1300] bg-slate-950/55 backdrop-blur-[2px] data-[state=closed]:opacity-0 data-[state=open]:opacity-100" />
        <DialogPrimitive.Content className="fixed inset-0 z-[1301] flex flex-col overflow-hidden bg-neutral-50 outline-none sm:inset-4 sm:rounded-2xl sm:border sm:border-neutral-200 sm:shadow-2xl">
          <header className="flex min-h-16 shrink-0 items-center justify-between gap-4 border-b border-neutral-200 bg-white px-4 py-3 sm:px-6">
            <div className="min-w-0">
              <DialogPrimitive.Title className="truncate text-base font-bold text-primary-font sm:text-lg">
                {recording?.lessonTitle || 'Запис пари'}
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="mt-0.5 truncate text-sm text-neutral-700">
                {recording ? `${recording.scopeLabel} · ${formatLessonDate(recording.recordedAt)}` : 'Завантаження…'}
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close
              className="flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-full border border-neutral-200 bg-white text-neutral-700 hover:bg-neutral-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
              aria-label="Закрити плеєр"
            >
              <X className="size-4" />
            </DialogPrimitive.Close>
          </header>

          <div className="shrink-0 border-b border-neutral-200 bg-white px-4 py-2.5 sm:px-6">
            <div className="flex gap-2 overflow-x-auto pb-0.5" role="group" aria-label="Дії із записом">
              <button
                type="button"
                className="inline-flex h-11 shrink-0 cursor-pointer items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3 text-sm font-semibold text-neutral-800 hover:border-basic-blue hover:bg-brand-00 hover:text-basic-blue disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
                disabled={!recording}
                onClick={() => void copyRecordingLink()}
              >
                <Link className="size-4 shrink-0" aria-hidden="true" />
                {copyLinkState.status === 'success' ? 'Посилання скопійовано' : 'Скопіювати посилання'}
              </button>

              {recording ? (
                <a
                  className="inline-flex h-11 shrink-0 items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3 text-sm font-semibold text-neutral-800 hover:border-basic-blue hover:bg-brand-00 hover:text-basic-blue focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
                  href={getRecordingDownloadUrl(recording.id)}
                  download={recording.fileName}
                >
                  <DownloadIcon />
                  {isVideoRecording(recording) ? 'Завантажити відео' : 'Завантажити аудіо'}
                </a>
              ) : (
                <span
                  className="inline-flex h-11 shrink-0 cursor-not-allowed items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3 text-sm font-semibold text-neutral-800 opacity-40"
                  aria-disabled="true"
                >
                  <DownloadIcon />
                  Завантажити запис
                </span>
              )}

              <button
                type="button"
                className="inline-flex h-11 shrink-0 cursor-pointer items-center gap-2 rounded-lg border border-neutral-300 bg-white px-3 text-sm font-semibold text-neutral-800 hover:border-basic-blue hover:bg-brand-00 hover:text-basic-blue disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
                disabled={!recording || !transcriptText}
                onClick={downloadTranscript}
              >
                <DownloadIcon />
                Завантажити текст
              </button>
            </div>

            <div className="sr-only" aria-live="polite">
              {copyLinkState.status === 'success' ? 'Посилання на запис скопійовано' : ''}
            </div>
            {copyLinkState.status === 'manual' && (
              <div
                className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-2.5 text-sm text-neutral-800"
                role="status"
              >
                <label className="block">
                  <span className="mb-1.5 block">Автоматичне копіювання недоступне. Скопіюйте виділене посилання:</span>
                  <input
                    ref={manualLinkRef}
                    className="h-9 w-full rounded-md border border-neutral-300 bg-white px-2.5 text-sm outline-none focus:border-basic-blue focus:ring-2 focus:ring-basic-blue/15"
                    readOnly
                    value={copyLinkState.url}
                    onFocus={(event) => event.currentTarget.select()}
                  />
                </label>
              </div>
            )}
          </div>

          <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[minmax(0,1.45fr)_minmax(360px,0.85fr)] lg:overflow-hidden">
            <section className="flex min-h-[360px] flex-col bg-slate-950 lg:min-h-0" aria-label="Запис пари">
              {recordings.length > 1 && (
                <div className="border-b border-white/10 bg-slate-900 px-4 py-3">
                  <label className="flex items-center gap-3 text-sm text-white">
                    <span className="shrink-0 font-semibold">Запис</span>
                    <select
                      className="min-w-0 flex-1 rounded-lg border border-white/20 bg-slate-800 px-3 py-2 text-white outline-none focus:border-other-purple"
                      value={selectedRecordingId}
                      onChange={(event) => setSelectedRecordingId(event.target.value)}
                    >
                      {recordings.map((item) => (
                        <option value={item.id} key={item.id}>
                          {formatLessonDate(item.recordedAt)} — {item.fileName}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              )}

              <div className="relative flex min-h-[300px] flex-1 items-center justify-center">
                {recording && isVideoRecording(recording) ? (
                  <video
                    className="max-h-full max-w-full"
                    controls
                    playsInline
                    preload="metadata"
                    aria-label={`Відеозапис «${recording.fileName}»`}
                    ref={(node) => {
                      mediaRef.current = node;
                    }}
                    src={getRecordingMediaUrl(recording.id)}
                    onCanPlay={() => setMediaError(undefined)}
                    onLoadedMetadata={(event) => applyInitialTime(event.currentTarget)}
                    onError={() =>
                      setMediaError('Не вдалося відтворити відео в браузері. Спробуйте запис в іншому форматі.')
                    }
                    onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
                  >
                    Ваш браузер не підтримує відтворення цього відео.
                  </video>
                ) : recording ? (
                  <div className="flex w-full max-w-2xl flex-col items-center gap-6 px-6 text-center text-white">
                    <div
                      className="flex size-20 items-center justify-center rounded-full bg-white/10 text-4xl"
                      aria-hidden="true"
                    >
                      ♪
                    </div>
                    <div className="max-w-full truncate text-base font-semibold">{recording.fileName}</div>
                    <audio
                      className="w-full"
                      controls
                      preload="metadata"
                      aria-label={`Аудіозапис «${recording.fileName}»`}
                      ref={(node) => {
                        mediaRef.current = node;
                      }}
                      src={getRecordingMediaUrl(recording.id)}
                      onCanPlay={() => setMediaError(undefined)}
                      onLoadedMetadata={(event) => applyInitialTime(event.currentTarget)}
                      onError={() =>
                        setMediaError('Не вдалося відтворити аудіо в браузері. Спробуйте запис в іншому форматі.')
                      }
                      onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
                    >
                      Ваш браузер не підтримує відтворення цього аудіо.
                    </audio>
                  </div>
                ) : (
                  <span className="text-white/70">Завантаження запису…</span>
                )}
                {mediaError && (
                  <div
                    className="pointer-events-none absolute inset-x-4 top-4 rounded-xl bg-red-800 px-4 py-3 text-center text-sm text-white shadow-lg"
                    role="alert"
                  >
                    {mediaError}
                  </div>
                )}
              </div>
            </section>

            <section
              className="flex min-h-[520px] flex-col border-l border-neutral-200 bg-white lg:min-h-0"
              aria-label="Транскрипція"
            >
              <div className="shrink-0 border-b border-neutral-200 p-4 sm:p-5">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <h2 className="text-base font-bold text-primary-font">Транскрипція</h2>
                  {recording?.durationSeconds !== undefined && (
                    <span className="text-sm text-neutral-700">{formatTimestamp(recording.durationSeconds)}</span>
                  )}
                </div>
                <label className="relative block">
                  <span className="sr-only">Пошук у транскрипції</span>
                  <span
                    className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-neutral-400"
                    aria-hidden="true"
                  >
                    ⌕
                  </span>
                  <input
                    type="search"
                    className="h-11 w-full rounded-xl border border-neutral-300 bg-neutral-50 pr-10 pl-9 text-base text-neutral-900 outline-none placeholder:text-neutral-400 focus:border-basic-blue focus:ring-2 focus:ring-basic-blue/15"
                    placeholder="Знайти слово або фразу"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                </label>
                {normalizedQuery && (
                  <div className="mt-2 text-sm text-neutral-700" aria-live="polite">
                    Знайдено фрагментів: {visibleSegments.length}
                  </div>
                )}
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
                {recordingDetailsUnavailable ? (
                  <div
                    className="flex h-full min-h-48 flex-col items-center justify-center gap-3 px-6 text-center"
                    role="alert"
                  >
                    <div className="font-semibold text-red-700">Не вдалося завантажити транскрипцію</div>
                    <div className="text-sm text-neutral-700">{getErrorMessage(recordingDetailsQueryError)}</div>
                    <button
                      type="button"
                      className="cursor-pointer rounded-lg bg-basic-blue px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
                      disabled={recordingDetailsFetching}
                      onClick={() => void refetchRecordingDetails()}
                    >
                      {recordingDetailsFetching ? 'Завантажуємо…' : 'Спробувати знову'}
                    </button>
                  </div>
                ) : isRecordingActive(recording) ? (
                  <div className="flex h-full min-h-48 flex-col items-center justify-center gap-3 px-6 text-center">
                    <div className="size-8 animate-spin rounded-full border-3 border-neutral-200 border-t-basic-blue" />
                    <div className="font-semibold text-primary-font">Транскрипція готується</div>
                    <div className="text-sm text-neutral-700">
                      Транскрипція ще готується. Запис уже можна переглядати.
                    </div>
                  </div>
                ) : recording?.status === 'failed' ? (
                  <div className="flex h-full min-h-48 items-center justify-center px-6 text-center text-sm text-red-700">
                    {recording.error || 'Не вдалося створити транскрипцію.'}
                  </div>
                ) : recordingDetailsLoading ? (
                  <div className="flex h-full min-h-48 flex-col items-center justify-center gap-3 px-6 text-center">
                    <div className="size-8 animate-spin rounded-full border-3 border-neutral-200 border-t-basic-blue" />
                    <div className="text-sm text-neutral-700">Завантажуємо транскрипцію…</div>
                  </div>
                ) : visibleSegments.length ? (
                  <div className="flex flex-col gap-1" role="list" aria-label="Фрагменти транскрипції">
                    {visibleSegments.map((segment) => {
                      const isActive = !normalizedQuery && segment.id === activeSegmentId;

                      return (
                        <div
                          role="listitem"
                          key={segment.id}
                          className={cn(
                            'grid w-full grid-cols-[58px_1fr] gap-3 rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-brand-00',
                            isActive && 'bg-brand-00 ring-1 ring-basic-blue/20',
                          )}
                        >
                          <button
                            type="button"
                            ref={isActive ? activeSegmentRef : undefined}
                            className="h-fit cursor-pointer rounded-md py-1 font-mono text-xs font-semibold text-basic-blue hover:bg-white focus-visible:outline-2 focus-visible:outline-basic-blue"
                            onClick={() => seekToSegment(segment)}
                            aria-label={`Перейти до ${formatTimestamp(segment.start)}`}
                            aria-current={isActive ? 'true' : undefined}
                          >
                            {formatTimestamp(segment.start)}
                          </button>
                          <p className="m-0 select-text text-base leading-6 font-normal text-neutral-900">
                            <HighlightedText text={segment.text} query={query} />
                          </p>
                        </div>
                      );
                    })}
                  </div>
                ) : normalizedQuery ? (
                  <div className="flex h-full min-h-48 items-center justify-center px-6 text-center text-sm text-neutral-700">
                    Нічого не знайдено. Спробуйте коротший запит.
                  </div>
                ) : (
                  <div className="flex h-full min-h-48 items-center justify-center px-6 text-center text-sm text-neutral-700">
                    Транскрипція поки порожня.
                  </div>
                )}
              </div>
            </section>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};

export default RecordingPlayerDialog;
