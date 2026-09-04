import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import Teacher from '../../assets/icons/teacher.svg?react';
import Users from '../../assets/icons/users-three.svg?react';
import { getAttachmentContentUrl } from '../../api/attachments';
import RecordingPlayerDialog from '../../components/RecordingAttachment/RecordingPlayerDialog';
import { Tabs, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { formatLessonDate, formatTimestamp } from '../../common/utils/recordingFormat';
import { AttachmentSearchResult } from '../../models/LessonAttachment';
import { RecordingSearchResult, RecordingSearchScope } from '../../models/Recording';
import { useAttachmentSearch } from '../../queries/useAttachments';
import { useRecordingSearch } from '../../queries/useRecordings';
import { useStore } from '../../store';
import { ScheduleGrid } from '../ScheduleWrapper/ScheduleWrapper';
import { getErrorMessage } from '../../common/utils/getErrorMessage';

type SearchMode = RecordingSearchScope | 'materials';

const PAGE_SIZE = 18;
const SEARCH_MODES: SearchMode[] = ['all', 'subject', 'lecturer', 'transcript', 'materials'];
const SEARCH_TABS: { value: SearchMode; label: string }[] = [
  { value: 'all', label: 'Усі' },
  { value: 'subject', label: 'Предмет' },
  { value: 'lecturer', label: 'Викладач' },
  { value: 'transcript', label: 'Транскрипція' },
  { value: 'materials', label: 'Матеріали' },
];

const isSearchCharacter = (character: string) => {
  const lower = character.toLocaleLowerCase('uk-UA');
  const upper = character.toLocaleUpperCase('uk-UA');
  return (character >= '0' && character <= '9') || lower !== upper;
};

const splitSearchText = (value: string) => {
  const parts: string[] = [];
  let current = '';
  let currentIsWord: boolean | undefined;

  Array.from(value).forEach((character) => {
    const isWord = isSearchCharacter(character);
    if (current && currentIsWord !== isWord) {
      parts.push(current);
      current = '';
    }
    current += character;
    currentIsWord = isWord;
  });
  if (current) {
    parts.push(current);
  }
  return parts;
};

const getSearchTokens = (query: string) =>
  Array.from(
    new Set(
      splitSearchText(query.normalize('NFKC').toLocaleLowerCase('uk-UA'))
        .filter((part) => isSearchCharacter(part[0]) && part.length >= 2)
        .slice(0, 8),
    ),
  );

const HighlightedText = ({ text, query }: { text: string; query: string }) => {
  const tokens = getSearchTokens(query);
  if (!tokens.length) {
    return text;
  }

  return splitSearchText(text).map((part, index) =>
    tokens.some((token) => part.normalize('NFKC').toLocaleLowerCase('uk-UA').startsWith(token)) ? (
      <mark className="rounded-sm bg-yellow-200 px-0.5 text-inherit" key={`${part}-${index}`}>
        {part}
      </mark>
    ) : (
      part
    ),
  );
};

const formatFileSize = (bytes: number) => {
  if (bytes < 1024) {
    return `${bytes} Б`;
  }
  if (bytes < 1024 * 1024) {
    return `${Math.max(0.1, bytes / 1024).toFixed(1)} КБ`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
};

const resultLabel = (count: number) => {
  const lastTwoDigits = count % 100;
  const lastDigit = count % 10;
  if (lastTwoDigits >= 11 && lastTwoDigits <= 14) {
    return `${count} результатів`;
  }
  if (lastDigit === 1) {
    return `${count} результат`;
  }
  if (lastDigit >= 2 && lastDigit <= 4) {
    return `${count} результати`;
  }
  return `${count} результатів`;
};

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

const ResultBadge = ({ children }: { children: React.ReactNode }) => (
  <span className="rounded-lg bg-other-purple px-2.5 py-0.75 text-center font-medium text-white">{children}</span>
);

const RecordingResultCard = ({
  recording,
  query,
  onOpen,
}: {
  recording: RecordingSearchResult;
  query: string;
  onOpen: () => void;
}) => (
  <article className="z-5 flex min-w-0 flex-col rounded-2xl border border-neutral-200 bg-bg-card p-4 shadow-schedule-item">
    <div className="flex items-center justify-between gap-6">
      <ResultBadge>Запис</ResultBadge>
      <span className="shrink-0 text-xs font-semibold text-neutral-600">{formatLessonDate(recording.recordedAt)}</span>
    </div>
    <h2 className="mt-3 text-sm leading-[17px] font-bold text-primary-font">
      <HighlightedText text={recording.lessonTitle} query={query} />
    </h2>
    <div className="mt-3 flex flex-col gap-3 text-[13px] leading-[18px] text-primary-font">
      {recording.lecturerName && (
        <div className="flex items-start gap-2">
          <Teacher className="size-[18px] shrink-0 text-neutral-600" aria-hidden="true" />
          <span>
            <HighlightedText text={recording.lecturerName} query={query} />
          </span>
        </div>
      )}
      <div className="flex items-start gap-2">
        <Users className="size-[18px] shrink-0 text-neutral-600" aria-hidden="true" />
        <span>{recording.groupLabel}</span>
      </div>
    </div>
    <button
      type="button"
      className="mt-4 min-w-0 cursor-pointer rounded-xl border border-basic-blue/15 bg-brand-00 p-2.5 text-left transition-colors hover:border-basic-blue/35 hover:bg-blue-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
      onClick={onOpen}
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
            <span className="truncate text-sm font-bold text-primary-font">
              {recording.matchSegment ? `Збіг о ${formatTimestamp(recording.matchSegment.start)}` : 'Відкрити запис'}
            </span>
            {recording.durationSeconds !== undefined && (
              <span className="shrink-0 text-xs font-semibold text-neutral-700">
                {formatTimestamp(recording.durationSeconds)}
              </span>
            )}
          </span>
          <span className="mt-1 flex items-center justify-between gap-2">
            <span className="truncate text-xs text-neutral-700">{recording.fileName}</span>
            <Waveform />
          </span>
        </span>
      </span>
    </button>
    {recording.matchSegment && (
      <button
        type="button"
        className="mt-3 cursor-pointer rounded-lg p-0 text-left text-xs leading-[17px] text-neutral-700 hover:text-primary-font focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
        onClick={onOpen}
      >
        <span className="mr-1.5 font-bold text-basic-blue tabular-nums">
          {formatTimestamp(recording.matchSegment.start)}
        </span>
        <HighlightedText text={recording.matchSegment.text} query={query} />
      </button>
    )}
  </article>
);

const MaterialResultCard = ({ material, query }: { material: AttachmentSearchResult; query: string }) => (
  <article className="z-5 flex min-w-0 flex-col rounded-2xl border border-neutral-200 bg-bg-card p-4 shadow-schedule-item">
    <div className="flex items-center justify-between gap-6">
      <ResultBadge>Матеріал</ResultBadge>
      <span className="shrink-0 text-xs font-semibold text-neutral-600">{formatLessonDate(material.recordedAt)}</span>
    </div>
    <h2 className="mt-3 text-sm leading-[17px] font-bold text-primary-font">
      <HighlightedText text={material.lessonTitle} query={query} />
    </h2>
    <div className="mt-3 flex items-start gap-2 text-[13px] leading-[18px] text-primary-font">
      <Users className="size-[18px] shrink-0 text-neutral-600" aria-hidden="true" />
      <span>{material.groupLabel}</span>
    </div>
    <a
      className="mt-4 flex min-w-0 items-center gap-3 rounded-xl border border-neutral-200 bg-neutral-50 p-3 text-left no-underline transition-colors hover:border-basic-blue/35 hover:bg-brand-00 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
      href={getAttachmentContentUrl(material.id)}
      download={material.fileName}
    >
      <span
        className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-white text-lg text-basic-blue shadow-sm"
        aria-hidden="true"
      >
        ↓
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-bold text-primary-font">
          <HighlightedText text={material.fileName} query={query} />
        </span>
        <span className="mt-1 block text-xs text-neutral-700">{formatFileSize(material.sizeBytes)}</span>
      </span>
    </a>
  </article>
);

const RecordingSearch = () => {
  const group = useStore((state) => state.group);
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get('q') || '';
  const modeParam = searchParams.get('scope');
  const mode: SearchMode = SEARCH_MODES.includes(modeParam as SearchMode) ? (modeParam as SearchMode) : 'all';
  const requestedPage = Number.parseInt(searchParams.get('page') || '1', 10);
  const page = Number.isFinite(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const [debouncedQuery, setDebouncedQuery] = useState(query);
  const [selectedRecording, setSelectedRecording] = useState<RecordingSearchResult>();
  const [playerOpen, setPlayerOpen] = useState(false);

  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => window.clearTimeout(timeout);
  }, [query]);

  const queryReady = !debouncedQuery || getSearchTokens(debouncedQuery).length > 0;
  const offset = (page - 1) * PAGE_SIZE;
  const recordingsQuery = useRecordingSearch({
    groupId: group?.id,
    query: debouncedQuery,
    scope: mode === 'materials' ? 'all' : mode,
    limit: PAGE_SIZE,
    offset,
    enabled: mode !== 'materials' && queryReady,
  });
  const materialsQuery = useAttachmentSearch({
    groupId: group?.id,
    query: debouncedQuery,
    limit: PAGE_SIZE,
    offset,
    enabled: mode === 'materials' && queryReady,
  });
  const activeQuery = mode === 'materials' ? materialsQuery : recordingsQuery;
  const total = activeQuery.data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const resultItems = useMemo(
    () => (mode === 'materials' ? materialsQuery.data?.items || [] : recordingsQuery.data?.items || []),
    [materialsQuery.data?.items, mode, recordingsQuery.data?.items],
  );

  useEffect(() => {
    if (page <= totalPages || activeQuery.isLoading) {
      return;
    }
    const nextSearchParams = new URLSearchParams(searchParams);
    nextSearchParams.delete('page');
    setSearchParams(nextSearchParams, { replace: true });
  }, [activeQuery.isLoading, page, searchParams, setSearchParams, totalPages]);

  const setPage = (nextPage: number) => {
    const nextSearchParams = new URLSearchParams(searchParams);
    if (nextPage <= 1) {
      nextSearchParams.delete('page');
    } else {
      nextSearchParams.set('page', String(nextPage));
    }
    setSearchParams(nextSearchParams, { replace: true });
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <>
      <ScheduleGrid className="min-h-[500px]">
        <div className="m-4 mb-0 flex items-center justify-between gap-5 max-lg:flex-col max-lg:items-stretch">
          <Tabs
            value={mode}
            onValueChange={(value) => {
              const nextMode = value as SearchMode;
              const nextSearchParams = new URLSearchParams(searchParams);
              if (nextMode === 'all') {
                nextSearchParams.delete('scope');
              } else {
                nextSearchParams.set('scope', nextMode);
              }
              nextSearchParams.delete('page');
              setSearchParams(nextSearchParams, { replace: true });
            }}
          >
            <TabsList
              segmented
              rounded
              className="max-w-full overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
              aria-label="Де шукати"
            >
              {SEARCH_TABS.map((tab) => (
                <TabsTrigger segmented rounded value={tab.value} data-text={tab.label} key={tab.value}>
                  {tab.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          {group && queryReady && (
            <div className="shrink-0 self-center text-[13px] font-semibold text-neutral-700" aria-live="polite">
              {activeQuery.isFetching ? 'Шукаємо…' : resultLabel(total)}
            </div>
          )}
        </div>

        {!group ? (
          <div className="m-auto px-5 py-14 text-center">
            <div className="text-base font-bold text-primary-font">Оберіть групу</div>
            <div className="mt-1.5 text-[13px] text-neutral-700">
              Після цього тут з’являться записи та матеріали її занять.
            </div>
          </div>
        ) : !queryReady ? (
          <div className="m-auto px-5 py-14 text-center">
            <div className="text-base font-bold text-primary-font">Запит надто короткий</div>
            <div className="mt-1.5 text-[13px] text-neutral-700">Введіть хоча б дві літери або цифри.</div>
          </div>
        ) : activeQuery.isLoading ? (
          <div className="grid grid-cols-3 gap-4 p-4 max-xl:grid-cols-2 max-md:grid-cols-1" aria-busy="true">
            {Array.from({ length: 6 }, (_, index) => (
              <div className="h-64 animate-pulse rounded-2xl border border-neutral-200 bg-neutral-100" key={index} />
            ))}
          </div>
        ) : activeQuery.isError ? (
          <div className="m-auto flex max-w-md flex-col items-center px-5 py-14 text-center" role="alert">
            <div className="text-base font-bold text-primary-font">Пошук тимчасово недоступний</div>
            <div className="mt-1.5 text-[13px] text-neutral-700">{getErrorMessage(activeQuery.error)}</div>
            <button
              type="button"
              className="mt-4 cursor-pointer rounded-lg bg-basic-blue px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
              onClick={() => void activeQuery.refetch()}
            >
              Спробувати знову
            </button>
          </div>
        ) : resultItems.length ? (
          <>
            <div className="grid grid-cols-3 gap-4 p-4 pt-0 max-xl:grid-cols-2 max-md:grid-cols-1">
              {mode === 'materials'
                ? (resultItems as AttachmentSearchResult[]).map((material) => (
                    <MaterialResultCard material={material} query={debouncedQuery} key={material.id} />
                  ))
                : (resultItems as RecordingSearchResult[]).map((recording) => (
                    <RecordingResultCard
                      recording={recording}
                      query={debouncedQuery}
                      onOpen={() => {
                        setSelectedRecording(recording);
                        setPlayerOpen(true);
                      }}
                      key={recording.id}
                    />
                  ))}
            </div>
            {totalPages > 1 && (
              <nav
                className="mt-auto flex items-center justify-center gap-3 px-4 pb-4"
                aria-label="Сторінки результатів"
              >
                <button
                  type="button"
                  className="cursor-pointer rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-semibold text-neutral-700 hover:border-basic-blue hover:text-basic-blue disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={page <= 1 || activeQuery.isFetching}
                  onClick={() => setPage(page - 1)}
                >
                  Назад
                </button>
                <span className="text-sm font-semibold text-neutral-700">
                  {page} / {totalPages}
                </span>
                <button
                  type="button"
                  className="cursor-pointer rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-semibold text-neutral-700 hover:border-basic-blue hover:text-basic-blue disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={page >= totalPages || activeQuery.isFetching}
                  onClick={() => setPage(page + 1)}
                >
                  Далі
                </button>
              </nav>
            )}
          </>
        ) : (
          <div className="m-auto px-5 py-14 text-center">
            <div className="text-base font-bold text-primary-font">
              {debouncedQuery
                ? 'Нічого не знайдено'
                : mode === 'materials'
                  ? 'Матеріалів ще немає'
                  : 'Записів ще немає'}
            </div>
            <div className="mt-1.5 text-[13px] text-neutral-700">
              {debouncedQuery
                ? 'Спробуйте коротший запит або інший розділ пошуку.'
                : 'Нові файли з’являться тут після додавання до заняття.'}
            </div>
          </div>
        )}
      </ScheduleGrid>

      {selectedRecording && (
        <RecordingPlayerDialog
          open={playerOpen}
          recordings={[selectedRecording]}
          initialRecordingId={selectedRecording.id}
          initialTime={selectedRecording.matchSegment?.start}
          onOpenChange={setPlayerOpen}
        />
      )}
    </>
  );
};

export default RecordingSearch;
