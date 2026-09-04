import * as DialogPrimitive from '@radix-ui/react-dialog';
import { ChangeEvent, DragEvent, useEffect, useRef, useState } from 'react';
import X from '../../assets/icons/x.svg?react';
import { MAX_ATTACHMENT_UPLOAD_BYTES } from '../../common/constants/uploadLimits';
import { formatLessonDate } from '../../common/utils/recordingFormat';

interface Props {
  day: string;
  lessonTitle: string;
  open: boolean;
  recordedAt: string;
  scopeLabel: string;
  time: string;
  onOpenChange: (open: boolean) => void;
  onSubmit: (file: File) => Promise<void> | void;
}

const formatFileSize = (bytes: number) => `${Math.max(0.1, bytes / (1024 * 1024)).toFixed(1)} МБ`;

const AttachmentUploadDialog = ({
  day,
  lessonTitle,
  open,
  recordedAt,
  scopeLabel,
  time,
  onOpenChange,
  onSubmit,
}: Props) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File>();
  const [fileError, setFileError] = useState<string>();
  const [submitError, setSubmitError] = useState<string>();
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setFile(undefined);
      setFileError(undefined);
      setSubmitError(undefined);
      setIsSubmitting(false);
    }
  }, [open]);

  const selectFile = (selectedFile?: File) => {
    if (!selectedFile) {
      return;
    }

    if (!selectedFile.size) {
      setFile(undefined);
      setFileError('Порожній файл завантажити не можна.');
      return;
    }

    if (selectedFile.size > MAX_ATTACHMENT_UPLOAD_BYTES) {
      setFile(undefined);
      setFileError('Файл завеликий. Максимальний розмір — 100 МБ.');
      return;
    }

    setFileError(undefined);
    setFile(selectedFile);
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    selectFile(event.target.files?.[0]);
    event.target.value = '';
  };

  const handleDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    selectFile(event.dataTransfer.files?.[0]);
  };

  const handleSubmit = async () => {
    if (!file) {
      setFileError('Оберіть файл.');
      return;
    }

    setSubmitError(undefined);
    setIsSubmitting(true);
    try {
      await onSubmit(file);
      onOpenChange(false);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : 'Не вдалося закріпити розклад перед завантаженням.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen && isSubmitting) {
      return;
    }
    onOpenChange(nextOpen);
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={handleOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[1300] bg-slate-950/45 backdrop-blur-[2px]" />
        <DialogPrimitive.Content
          className="fixed inset-x-0 bottom-0 z-[1301] max-h-[calc(100dvh-16px)] overflow-y-auto rounded-t-2xl bg-white p-5 shadow-2xl outline-none sm:top-1/2 sm:bottom-auto sm:left-1/2 sm:w-[min(520px,calc(100vw-32px))] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-2xl sm:border sm:border-neutral-200 sm:p-6"
          onEscapeKeyDown={(event) => isSubmitting && event.preventDefault()}
          onPointerDownOutside={(event) => isSubmitting && event.preventDefault()}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <DialogPrimitive.Title className="text-lg font-bold text-primary-font">Додати файл</DialogPrimitive.Title>
              <DialogPrimitive.Description className="mt-1 text-sm leading-5 text-neutral-700">
                <span className="font-semibold text-neutral-900">{lessonTitle}</span>
                <br />
                {scopeLabel} · {day} · {time}
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close
              className="flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-full border border-neutral-200 bg-white hover:bg-neutral-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
              aria-label="Закрити"
              disabled={isSubmitting}
            >
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>

          <div className="mt-5">
            <div className="text-sm font-semibold text-neutral-900">Дата пари</div>
            <div className="mt-2 rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2.5">
              <time className="block text-base font-semibold text-primary-font" dateTime={recordedAt}>
                {formatLessonDate(recordedAt)}
              </time>
              <span className="mt-0.5 block text-xs text-neutral-600">Визначено вибраним тижнем розкладу</span>
            </div>
          </div>

          <input className="sr-only" ref={inputRef} type="file" tabIndex={-1} onChange={handleFileChange} />
          <button
            type="button"
            className="mt-4 flex min-h-32 w-full cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed border-neutral-300 bg-neutral-50 px-5 py-6 text-center hover:border-basic-blue hover:bg-brand-00 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => event.preventDefault()}
            onDrop={handleDrop}
            disabled={isSubmitting}
          >
            <span
              className="flex size-10 items-center justify-center rounded-full bg-white text-2xl text-basic-blue shadow-sm"
              aria-hidden="true"
            >
              +
            </span>
            {file ? (
              <>
                <span className="mt-3 max-w-full truncate text-sm font-bold text-primary-font">{file.name}</span>
                <span className="mt-1 text-xs text-neutral-700">
                  {formatFileSize(file.size)} · натисніть, щоб замінити
                </span>
              </>
            ) : (
              <>
                <span className="mt-3 text-sm font-bold text-primary-font">Оберіть або перетягніть файл</span>
                <span className="mt-1 text-xs leading-4 text-neutral-700">Будь-який тип файлу · до 100 МБ</span>
              </>
            )}
          </button>

          {fileError && (
            <div className="mt-3 text-sm text-red-700" role="alert">
              {fileError}
            </div>
          )}

          {submitError && (
            <div className="mt-3 text-sm text-red-700" role="alert">
              {submitError}
            </div>
          )}

          <div className="mt-4 rounded-xl bg-brand-00 px-3 py-2.5 text-xs leading-4 text-primary-font">
            Після завантаження файл буде доступний всім, хто відкриє матеріали цієї пари.
          </div>

          <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <DialogPrimitive.Close
              className="min-h-11 cursor-pointer rounded-xl border border-neutral-300 bg-white px-4 text-sm font-semibold text-neutral-700 hover:bg-neutral-50 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
              disabled={isSubmitting}
            >
              Скасувати
            </DialogPrimitive.Close>
            <button
              type="button"
              className="min-h-11 cursor-pointer rounded-xl bg-basic-blue px-5 text-sm font-semibold text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-basic-blue"
              disabled={!file || isSubmitting}
              onClick={() => void handleSubmit()}
            >
              {isSubmitting ? 'Закріплюємо розклад…' : 'Додати файл'}
            </button>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};

export default AttachmentUploadDialog;
