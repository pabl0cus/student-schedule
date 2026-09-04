import { Recording } from '../models/Recording';
import {
  getRecordingApiUrl,
  LOCAL_REQUEST_HEADERS,
  normalizeRecording,
  parseApiError,
  RawRecording,
} from './recordingsShared';

export type AdminSession = { authenticated: false; username?: never } | { authenticated: true; username: string };

export interface AdminCredentials {
  username: string;
  password: string;
}

export interface CommunityWorker {
  id: string;
  label: string;
  createdAt: string;
  lastSeenAt: string | null;
  revokedAt: string | null;
}

export interface IssuedCommunityWorker extends CommunityWorker {
  token: string;
}

interface RawCommunityWorker {
  id: string;
  label: string;
  created_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
}

interface RawIssuedCommunityWorker extends RawCommunityWorker {
  token: string;
}

export class AdminApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'AdminApiError';
    this.status = status;
  }
}

const request = async <T>(path: string, options: RequestInit = {}): Promise<T> => {
  const response = await fetch(getRecordingApiUrl(path), {
    ...options,
    credentials: 'include',
  });

  if (!response.ok) {
    throw new AdminApiError(await parseApiError(response), response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
};

export const getAdminSession = () => request<AdminSession>('/admin/session');

export const createAdminSession = (credentials: AdminCredentials) =>
  request<AdminSession>('/admin/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...LOCAL_REQUEST_HEADERS },
    body: JSON.stringify(credentials),
  });

export const deleteAdminSession = () =>
  request<void>('/admin/session', {
    method: 'DELETE',
    headers: LOCAL_REQUEST_HEADERS,
  });

export const getAdminRecordings = async (): Promise<Recording[]> => {
  const recordings = await request<RawRecording[]>('/admin/recordings');
  return recordings.map(normalizeRecording);
};

export const deleteAdminRecording = (recordingId: string) =>
  request<void>(`/admin/recordings/${encodeURIComponent(recordingId)}`, {
    method: 'DELETE',
    headers: LOCAL_REQUEST_HEADERS,
  });

const normalizeCommunityWorker = (worker: RawCommunityWorker): CommunityWorker => ({
  id: worker.id,
  label: worker.label,
  createdAt: worker.created_at,
  lastSeenAt: worker.last_seen_at,
  revokedAt: worker.revoked_at,
});

export const getCommunityWorkers = async (): Promise<CommunityWorker[]> => {
  const workers = await request<RawCommunityWorker[]>('/admin/community-workers');
  return workers.map(normalizeCommunityWorker);
};

export const createCommunityWorker = async (label: string): Promise<IssuedCommunityWorker> => {
  const worker = await request<RawIssuedCommunityWorker>('/admin/community-workers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...LOCAL_REQUEST_HEADERS },
    body: JSON.stringify({ label }),
  });
  return { ...normalizeCommunityWorker(worker), token: worker.token };
};

export const revokeCommunityWorker = (workerId: string) =>
  request<void>(`/admin/community-workers/${encodeURIComponent(workerId)}`, {
    method: 'DELETE',
    headers: LOCAL_REQUEST_HEADERS,
  });
