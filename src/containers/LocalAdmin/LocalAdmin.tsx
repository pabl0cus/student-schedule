import dayjs from 'dayjs';
import { FormEvent, useEffect, useState } from 'react';
import { AdminApiError } from '../../api/admin';
import LogoIcon from '../../assets/logo.svg?react';
import { Button } from '../../components/ui/button';
import { Recording, RecordingStatus } from '../../models/Recording';
import {
  useAdminRecordings,
  useAdminSession,
  useCommunityWorkers,
  useCreateAdminSession,
  useCreateCommunityWorker,
  useDeleteAdminRecording,
  useDeleteAdminSession,
  useRevokeCommunityWorker,
} from '../../queries/useAdmin';
import { isRecordingActive } from '../../common/utils/recordingFormat';

const STATUS_LABELS: Record<RecordingStatus, string> = {
  queued: 'У черзі',
  processing: 'Обробляється',
  ready: 'Готово',
  failed: 'Помилка',
};

const STATUS_STYLES: Record<RecordingStatus, string> = {
  queued: 'bg-neutral-100 text-neutral-700',
  processing: 'bg-brand-00 text-basic-blue',
  ready: 'bg-emerald-50 text-emerald-700',
  failed: 'bg-red-50 text-red-700',
};

const getErrorMessage = (error: unknown) => {
  if (error instanceof AdminApiError && error.status === 401) {
    return 'Невірний логін або пароль.';
  }

  return error instanceof Error ? error.message : 'Сервіс недоступний. Спробуйте ще раз.';
};

const LoginPanel = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const login = useCreateAdminSession();
  const canSubmit = username.trim().length > 0 && password.length > 0 && !login.isLoading;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }

    login.mutate(
      { username: username.trim(), password },
      {
        onSuccess: () => setPassword(''),
      },
    );
  };

  return (
    <section className="w-full max-w-md rounded-[20px] border-2 border-neutral-100 bg-white p-6 shadow-schedule-item sm:p-8">
      <div className="mb-2 text-2xl leading-tight font-bold text-primary-font" role="heading" aria-level={1}>
        Керування записами
      </div>
      <p className="mt-0 mb-6 text-sm font-normal text-neutral-700">
        Увійдіть, щоб переглянути або видалити записи пар.
      </p>

      <form className="space-y-4" onSubmit={handleSubmit}>
        <div>
          <label className="mb-1.5 block text-sm font-semibold text-neutral-900" htmlFor="admin-username">
            Користувач
          </label>
          <input
            id="admin-username"
            className="h-11 w-full rounded-lg border border-neutral-300 bg-white px-3 text-base text-neutral-900 outline-none placeholder:text-neutral-400 focus:border-basic-blue focus:ring-2 focus:ring-basic-blue/15"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
          />
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-semibold text-neutral-900" htmlFor="admin-password">
            Пароль
          </label>
          <input
            id="admin-password"
            className="h-11 w-full rounded-lg border border-neutral-300 bg-white px-3 text-base text-neutral-900 outline-none placeholder:text-neutral-400 focus:border-basic-blue focus:ring-2 focus:ring-basic-blue/15"
            autoComplete="current-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </div>

        {login.isError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {getErrorMessage(login.error)}
          </div>
        )}

        <Button className="w-full justify-center" disabled={!canSubmit} size="lg" type="submit">
          {login.isLoading ? 'Входимо…' : 'Увійти'}
        </Button>
      </form>
    </section>
  );
};

interface RecordingRowProps {
  recording: Recording;
  confirming: boolean;
  deleting: boolean;
  deleteError?: Error;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}

const RecordingRow = ({
  recording,
  confirming,
  deleting,
  deleteError,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
}: RecordingRowProps) => {
  const isRunning = isRecordingActive(recording);

  return (
    <li className="rounded-xl border border-neutral-200 bg-white p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="truncate text-sm font-bold text-primary-font" title={recording.lessonTitle}>
            {recording.lessonTitle}
          </div>
          <div className="mt-1 truncate text-xs text-neutral-700" title={recording.fileName}>
            {recording.fileName}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-neutral-600">
            <time dateTime={recording.recordedAt}>{dayjs(recording.recordedAt).format('D MMM YYYY')}</time>
            <span aria-hidden="true">·</span>
            <span className={`rounded-full px-2 py-0.5 font-semibold ${STATUS_STYLES[recording.status]}`}>
              {STATUS_LABELS[recording.status]}
            </span>
          </div>
        </div>

        {!confirming ? (
          <Button
            className="shrink-0 justify-center text-red-700 enabled:hover:border-red-300 enabled:hover:text-red-800"
            disabled={isRunning || deleting}
            onClick={onAskDelete}
            size="sm"
            title={isRunning ? 'Дочекайтеся завершення транскрибації' : 'Видалити запис'}
            type="button"
            variant="secondary"
          >
            Видалити
          </Button>
        ) : (
          <div
            className="shrink-0 rounded-lg border border-red-200 bg-red-50 p-2"
            role="group"
            aria-label="Підтвердження видалення"
          >
            <div className="mb-2 text-xs font-semibold text-red-800">Видалити цей запис?</div>
            <div className="flex gap-2">
              <Button disabled={deleting} onClick={onCancelDelete} size="sm" type="button" variant="secondary">
                Скасувати
              </Button>
              <Button
                className="justify-center bg-red-700 enabled:hover:bg-red-800"
                disabled={deleting || isRunning}
                onClick={onConfirmDelete}
                size="sm"
                type="button"
              >
                {deleting ? 'Видаляємо…' : 'Так, видалити'}
              </Button>
            </div>
          </div>
        )}
      </div>

      {deleteError && (
        <div className="mt-3 text-xs text-red-700" role="alert">
          {getErrorMessage(deleteError)}
        </div>
      )}
    </li>
  );
};

const CommunityWorkersPanel = () => {
  const workers = useCommunityWorkers(true);
  const createWorker = useCreateCommunityWorker();
  const revokeWorker = useRevokeCommunityWorker();
  const [label, setLabel] = useState('');
  const [issuedToken, setIssuedToken] = useState<string>();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) {
      return;
    }
    const timeout = window.setTimeout(() => setCopied(false), 2_000);
    return () => window.clearTimeout(timeout);
  }, [copied]);

  const submitWorker = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedLabel = label.trim();
    if (!normalizedLabel || createWorker.isLoading) {
      return;
    }
    createWorker.mutate(normalizedLabel, {
      onSuccess: (worker) => {
        setIssuedToken(worker.token);
        setLabel('');
        setCopied(false);
      },
    });
  };

  const copyToken = async () => {
    if (!issuedToken || !navigator.clipboard?.writeText) {
      return;
    }
    await navigator.clipboard.writeText(issuedToken);
    setCopied(true);
  };

  return (
    <section className="mb-6 rounded-xl border border-neutral-200 bg-white p-4 sm:p-5">
      <div className="mb-1 text-lg font-bold text-primary-font" role="heading" aria-level={2}>
        Учасники транскрибації
      </div>
      <p className="mt-0 mb-4 text-sm text-neutral-700">
        Створіть окремий токен для кожного довіреного комп’ютера. Токен буде показано лише один раз.
      </p>

      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={submitWorker}>
        <label className="sr-only" htmlFor="community-worker-label">
          Назва комп’ютера або учасника
        </label>
        <input
          id="community-worker-label"
          className="h-10 min-w-0 grow rounded-lg border border-neutral-300 bg-white px-3 text-sm text-neutral-900 outline-none placeholder:text-neutral-400 focus:border-basic-blue focus:ring-2 focus:ring-basic-blue/15"
          maxLength={100}
          placeholder="Наприклад, RTX 3090 — Андрій"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          required
        />
        <Button disabled={!label.trim() || createWorker.isLoading} size="sm" type="submit">
          {createWorker.isLoading ? 'Створюємо…' : 'Створити токен'}
        </Button>
      </form>

      {createWorker.isError && (
        <div className="mt-3 text-sm text-red-700" role="alert">
          {getErrorMessage(createWorker.error)}
        </div>
      )}

      {issuedToken && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3">
          <div className="text-sm font-semibold text-amber-950">Збережіть токен зараз</div>
          <p className="my-1 text-xs text-amber-900">
            Передайте його власнику клієнта безпечним каналом. Відновити цей токен із сервера неможливо.
          </p>
          <div className="mt-2 flex flex-col gap-2 sm:flex-row">
            <input
              className="h-10 min-w-0 grow rounded-lg border border-amber-300 bg-white px-3 font-mono text-xs text-neutral-900"
              aria-label="Новий токен учасника"
              readOnly
              value={issuedToken}
              onFocus={(event) => event.currentTarget.select()}
            />
            <Button onClick={() => void copyToken()} size="sm" type="button" variant="secondary">
              {copied ? 'Скопійовано' : 'Копіювати'}
            </Button>
            <Button onClick={() => setIssuedToken(undefined)} size="sm" type="button" variant="secondary">
              Я зберіг
            </Button>
          </div>
        </div>
      )}

      {workers.isLoading ? (
        <div className="mt-4 text-sm text-neutral-600">Завантажуємо учасників…</div>
      ) : workers.isError ? (
        <div className="mt-4 text-sm text-red-700" role="alert">
          {getErrorMessage(workers.error)}
        </div>
      ) : workers.data?.length ? (
        <ul className="mt-4 divide-y divide-neutral-100 border-t border-neutral-100">
          {workers.data.map((worker) => {
            const isRevoked = Boolean(worker.revokedAt);
            return (
              <li className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between" key={worker.id}>
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-neutral-900">{worker.label}</div>
                  <div className="mt-0.5 text-xs text-neutral-600">
                    {isRevoked
                      ? `Відкликано ${dayjs(worker.revokedAt).format('D MMM YYYY, HH:mm')}`
                      : worker.lastSeenAt
                        ? `Був онлайн ${dayjs(worker.lastSeenAt).format('D MMM YYYY, HH:mm')}`
                        : 'Ще не підключався'}
                  </div>
                </div>
                <Button
                  className="shrink-0 text-red-700 enabled:hover:border-red-300 enabled:hover:text-red-800"
                  disabled={isRevoked || revokeWorker.isLoading}
                  onClick={() => revokeWorker.mutate(worker.id)}
                  size="sm"
                  type="button"
                  variant="secondary"
                >
                  {isRevoked ? 'Відкликано' : 'Відкликати'}
                </Button>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="mt-4 rounded-lg border border-dashed border-neutral-300 px-3 py-5 text-center text-sm text-neutral-600">
          Підключених комп’ютерів поки немає.
        </div>
      )}

      {revokeWorker.isError && (
        <div className="mt-3 text-sm text-red-700" role="alert">
          {getErrorMessage(revokeWorker.error)}
        </div>
      )}
    </section>
  );
};

const RecordingsPanel = ({ username }: { username: string }) => {
  const recordings = useAdminRecordings(true);
  const logout = useDeleteAdminSession();
  const removeRecording = useDeleteAdminRecording();
  const [confirmingId, setConfirmingId] = useState<string>();

  const confirmDelete = (recordingId: string) => {
    removeRecording.mutate(recordingId, {
      onSuccess: () => setConfirmingId(undefined),
    });
  };

  return (
    <section className="w-full max-w-4xl rounded-[20px] border-2 border-neutral-100 bg-neutral-50 p-4 sm:p-6">
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="mb-1 text-2xl leading-tight font-bold text-primary-font" role="heading" aria-level={1}>
            Записи пар
          </div>
          <div className="text-sm text-neutral-700">Адміністратор: {username}</div>
        </div>
        <Button disabled={logout.isLoading} onClick={() => logout.mutate()} type="button" variant="secondary">
          {logout.isLoading ? 'Виходимо…' : 'Вийти'}
        </Button>
      </div>

      {logout.isError && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {getErrorMessage(logout.error)}
        </div>
      )}

      <CommunityWorkersPanel />

      {recordings.isLoading ? (
        <div className="py-10 text-center text-sm text-neutral-700" aria-live="polite">
          Завантажуємо записи…
        </div>
      ) : recordings.isError ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4" role="alert">
          <div className="text-sm text-red-700">{getErrorMessage(recordings.error)}</div>
          <Button className="mt-3" onClick={() => void recordings.refetch()} size="sm" type="button">
            Спробувати знову
          </Button>
        </div>
      ) : recordings.data?.length ? (
        <ul className="space-y-2">
          {recordings.data.map((recording) => (
            <RecordingRow
              key={recording.id}
              recording={recording}
              confirming={confirmingId === recording.id}
              deleting={removeRecording.isLoading}
              deleteError={
                removeRecording.isError && removeRecording.variables === recording.id
                  ? removeRecording.error
                  : undefined
              }
              onAskDelete={() => {
                removeRecording.reset();
                setConfirmingId(recording.id);
              }}
              onCancelDelete={() => {
                removeRecording.reset();
                setConfirmingId(undefined);
              }}
              onConfirmDelete={() => confirmDelete(recording.id)}
            />
          ))}
        </ul>
      ) : (
        <div className="rounded-xl border border-dashed border-neutral-300 bg-white py-10 text-center text-sm text-neutral-700">
          Записів поки немає.
        </div>
      )}
    </section>
  );
};

const LocalAdmin = () => {
  const session = useAdminSession();

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <header className="flex min-h-24 items-center bg-white px-6 py-5 shadow-header sm:px-9">
        <LogoIcon viewBox="0 0 185 64" className="h-auto w-full max-w-[145px]" aria-label="Розклад КПІ" />
      </header>
      <main className="grow bg-neutral-50">
        <div className="flex items-start justify-center px-4 py-8 sm:px-9 sm:py-12">
          {session.isLoading ? (
            <div className="py-16 text-sm text-neutral-700" aria-live="polite">
              Перевіряємо сесію…
            </div>
          ) : session.isError ? (
            <section className="w-full max-w-md rounded-[20px] border border-red-200 bg-red-50 p-6" role="alert">
              <div className="mb-2 text-xl leading-tight font-bold text-red-800" role="heading" aria-level={1}>
                Сервіс недоступний
              </div>
              <div className="text-sm text-red-700">{getErrorMessage(session.error)}</div>
              <Button className="mt-4" onClick={() => void session.refetch()} type="button">
                Спробувати знову
              </Button>
            </section>
          ) : session.data?.authenticated ? (
            <RecordingsPanel username={session.data.username} />
          ) : (
            <LoginPanel />
          )}
        </div>
      </main>
    </div>
  );
};

export default LocalAdmin;
