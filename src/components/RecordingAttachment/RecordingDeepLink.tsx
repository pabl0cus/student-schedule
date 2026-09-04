import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useLessonRecordings, useRecording } from '../../queries/useRecordings';
import RecordingPlayerDialog from './RecordingPlayerDialog';

const RecordingDeepLink = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const recordingId = searchParams.get('recordingId')?.trim() || undefined;
  const { data: recording } = useRecording(recordingId, Boolean(recordingId));
  const { data: lessonRecordings = [] } = useLessonRecordings(recording?.lessonKey);
  const recordings = useMemo(
    () => (lessonRecordings.length ? lessonRecordings : recording ? [recording] : []),
    [lessonRecordings, recording],
  );

  const handleOpenChange = (open: boolean) => {
    if (open) {
      return;
    }

    const nextSearchParams = new URLSearchParams(searchParams);
    nextSearchParams.delete('recordingId');
    setSearchParams(nextSearchParams, { replace: true });
  };

  if (!recordingId) {
    return null;
  }

  return (
    <RecordingPlayerDialog
      initialRecordingId={recordingId}
      open
      recordings={recordings}
      onOpenChange={handleOpenChange}
    />
  );
};

export default RecordingDeepLink;
