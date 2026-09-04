import { useMutation, useQuery, useQueryClient } from 'react-query';
import {
  AdminApiError,
  AdminCredentials,
  AdminSession,
  CommunityWorker,
  IssuedCommunityWorker,
  createAdminSession,
  createCommunityWorker,
  deleteAdminRecording,
  deleteAdminSession,
  getCommunityWorkers,
  getAdminRecordings,
  getAdminSession,
  revokeCommunityWorker,
} from '../api/admin';
import { Recording } from '../models/Recording';
import { isRecordingActive } from '../common/utils/recordingFormat';

export const ADMIN_SESSION_QUERY_KEY = ['localAdmin', 'session'] as const;
export const ADMIN_RECORDINGS_QUERY_KEY = ['localAdmin', 'recordings'] as const;
export const ADMIN_COMMUNITY_WORKERS_QUERY_KEY = ['localAdmin', 'communityWorkers'] as const;

const isUnauthorized = (error: unknown) => error instanceof AdminApiError && error.status === 401;

export const useAdminSession = () =>
  useQuery<AdminSession, Error>({
    queryKey: ADMIN_SESSION_QUERY_KEY,
    queryFn: getAdminSession,
    retry: false,
    staleTime: 30_000,
  });

export const useCreateAdminSession = () => {
  const queryClient = useQueryClient();

  return useMutation<AdminSession, Error, AdminCredentials>(createAdminSession, {
    onSuccess: (session) => {
      queryClient.setQueryData(ADMIN_SESSION_QUERY_KEY, session);
      queryClient.invalidateQueries(ADMIN_RECORDINGS_QUERY_KEY);
      queryClient.invalidateQueries(ADMIN_COMMUNITY_WORKERS_QUERY_KEY);
    },
  });
};

export const useDeleteAdminSession = () => {
  const queryClient = useQueryClient();

  return useMutation<void, Error>(deleteAdminSession, {
    onSuccess: () => {
      queryClient.setQueryData<AdminSession>(ADMIN_SESSION_QUERY_KEY, { authenticated: false });
      queryClient.removeQueries(ADMIN_RECORDINGS_QUERY_KEY);
      queryClient.removeQueries(ADMIN_COMMUNITY_WORKERS_QUERY_KEY);
    },
    onError: (error) => {
      if (isUnauthorized(error)) {
        queryClient.setQueryData<AdminSession>(ADMIN_SESSION_QUERY_KEY, { authenticated: false });
        queryClient.removeQueries(ADMIN_RECORDINGS_QUERY_KEY);
        queryClient.removeQueries(ADMIN_COMMUNITY_WORKERS_QUERY_KEY);
      }
    },
  });
};

export const useAdminRecordings = (enabled: boolean) => {
  const queryClient = useQueryClient();

  return useQuery<Recording[], Error>({
    queryKey: ADMIN_RECORDINGS_QUERY_KEY,
    queryFn: getAdminRecordings,
    enabled,
    retry: false,
    refetchInterval: (recordings) => (recordings?.some(isRecordingActive) ? 5_000 : false),
    onError: (error) => {
      if (isUnauthorized(error)) {
        queryClient.setQueryData<AdminSession>(ADMIN_SESSION_QUERY_KEY, { authenticated: false });
      }
    },
  });
};

export const useDeleteAdminRecording = () => {
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>(deleteAdminRecording, {
    onSuccess: (_result, recordingId) => {
      queryClient.invalidateQueries(['localAdmin']);
      queryClient.invalidateQueries(['recordings']);
      queryClient.invalidateQueries(['recording', recordingId]);
    },
    onError: (error) => {
      if (isUnauthorized(error)) {
        queryClient.setQueryData<AdminSession>(ADMIN_SESSION_QUERY_KEY, { authenticated: false });
      }
    },
  });
};

export const useCommunityWorkers = (enabled: boolean) => {
  const queryClient = useQueryClient();

  return useQuery<CommunityWorker[], Error>({
    queryKey: ADMIN_COMMUNITY_WORKERS_QUERY_KEY,
    queryFn: getCommunityWorkers,
    enabled,
    retry: false,
    refetchInterval: 30_000,
    onError: (error) => {
      if (isUnauthorized(error)) {
        queryClient.setQueryData<AdminSession>(ADMIN_SESSION_QUERY_KEY, { authenticated: false });
      }
    },
  });
};

export const useCreateCommunityWorker = () => {
  const queryClient = useQueryClient();

  return useMutation<IssuedCommunityWorker, Error, string>(createCommunityWorker, {
    onSuccess: () => queryClient.invalidateQueries(ADMIN_COMMUNITY_WORKERS_QUERY_KEY),
    onError: (error) => {
      if (isUnauthorized(error)) {
        queryClient.setQueryData<AdminSession>(ADMIN_SESSION_QUERY_KEY, { authenticated: false });
      }
    },
  });
};

export const useRevokeCommunityWorker = () => {
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>(revokeCommunityWorker, {
    onSuccess: () => queryClient.invalidateQueries(ADMIN_COMMUNITY_WORKERS_QUERY_KEY),
    onError: (error) => {
      if (isUnauthorized(error)) {
        queryClient.setQueryData<AdminSession>(ADMIN_SESSION_QUERY_KEY, { authenticated: false });
      }
    },
  });
};
