import dayjs from 'dayjs';
import type { Recording } from '../../models/Recording';

export const formatTimestamp = (seconds: number) => {
  const totalSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainingSeconds = totalSeconds % 60;

  return hours > 0
    ? [hours, minutes, remainingSeconds].map((value) => String(value).padStart(2, '0')).join(':')
    : [minutes, remainingSeconds].map((value) => String(value).padStart(2, '0')).join(':');
};

export const formatLessonDate = (value: string) => dayjs(value).format('D MMMM YYYY');

export const normalizeTranscriptSearch = (value: string) =>
  value
    .normalize('NFC')
    .toLocaleLowerCase('uk-UA')
    .replace(/['’ʼ`]/g, "'")
    .trim();

export const isVideoRecording = (recording: Recording) =>
  recording.mimeType.startsWith('video/') || /\.(mp4|m4v|mov|webm|mkv)$/i.test(recording.fileName);

export const isRecordingActive = (recording?: Pick<Recording, 'status'>) =>
  recording?.status === 'queued' || recording?.status === 'processing';
