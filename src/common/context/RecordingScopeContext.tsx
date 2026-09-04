import { RecordingScope, RecordingScopeContext } from './useRecordingScope';

interface Props extends RecordingScope {
  children: React.ReactNode;
}

export const RecordingScopeProvider = ({ children, ...scope }: Props) => (
  <RecordingScopeContext.Provider value={scope}>{children}</RecordingScopeContext.Provider>
);
