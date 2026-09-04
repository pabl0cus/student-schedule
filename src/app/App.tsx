import { matchPath, Route, Routes, Navigate, useLocation } from 'react-router-dom';
import { lazy, Suspense, useEffect, useRef } from 'react';

import { routes } from '../common/constants/routes';
import { GA_TRACKING_ID } from '../common/constants/config';
import { QueryClient, QueryClientProvider } from 'react-query';
import { ScheduleLayout } from '../layouts/ScheduleLayout';
import GroupSchedule from '../containers/GroupSchedule';

const AboutLayout = lazy(() => import('../layouts/AboutLayout').then((module) => ({ default: module.AboutLayout })));
const Contacts = lazy(() => import('../containers/About/Contacts').then((module) => ({ default: module.Contacts })));
const Project = lazy(() => import('../containers/About/Project').then((module) => ({ default: module.Project })));
const LecturerSchedule = lazy(() => import('../containers/LecturerSchedule'));
const ScheduleExams = lazy(() => import('../containers/ScheduleExams'));
const RecordingSearch = lazy(() => import('../containers/RecordingSearch'));
const Admin = lazy(() => import('../containers/LocalAdmin'));
const RecordingDeepLink = lazy(() => import('../components/RecordingAttachment/RecordingDeepLink'));

const queryClient = new QueryClient();
type AnalyticsClient = typeof import('react-ga4').default;

const getLocalAdminPath = () => {
  const configuredPath = (import.meta.env.VITE_ADMIN_PATH || import.meta.env.VITE_LOCAL_ADMIN_PATH)?.trim();
  if (!configuredPath || /[?#*]/.test(configuredPath)) {
    return undefined;
  }

  const normalizedPath = `/${configuredPath.replace(/^\/+|\/+$/g, '')}`;
  return normalizedPath === '/' ? undefined : normalizedPath;
};

const localAdminPath = getLocalAdminPath();

const RouteFallback = () => (
  <div className="flex min-h-[240px] items-center justify-center text-sm text-neutral-600" role="status">
    Завантаження…
  </div>
);

function App() {
  const location = useLocation();
  const analytics = useRef<AnalyticsClient>();
  const isLocalAdminRoute = Boolean(
    localAdminPath && matchPath({ path: localAdminPath, end: true, caseSensitive: false }, location.pathname),
  );
  const hasRecordingDeepLink = new URLSearchParams(location.search).has('recordingId');
  const page = location.pathname + location.search;
  const currentPage = useRef(page);
  const currentPageIsAdmin = useRef(isLocalAdminRoute);
  currentPage.current = page;
  currentPageIsAdmin.current = isLocalAdminRoute;

  useEffect(() => {
    if (!GA_TRACKING_ID) {
      return;
    }

    let active = true;
    void import('react-ga4')
      .then(({ default: client }) => {
        if (!active) {
          return;
        }
        client.initialize(GA_TRACKING_ID, { gtagOptions: { send_page_view: false } });
        analytics.current = client;
        if (!currentPageIsAdmin.current) {
          client.send({ hitType: 'pageview', page: currentPage.current });
        }
      })
      .catch((error: unknown) => console.error('Failed to initialize Google Analytics:', error));

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (analytics.current && !isLocalAdminRoute) {
      try {
        analytics.current.send({ hitType: 'pageview', page });
      } catch (error) {
        console.error('Failed to send pageview to Google Analytics:', error);
      }
    }
  }, [isLocalAdminRoute, page]);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="flex min-h-screen flex-col bg-white">
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/" element={<ScheduleLayout />}>
              <Route index element={<GroupSchedule />} />
              <Route path={routes.SESSION} element={<ScheduleExams />} />
              <Route path={routes.LECTURER} element={<LecturerSchedule />} />
              <Route path={routes.RECORDINGS} element={<RecordingSearch />} />
            </Route>
            <Route element={<AboutLayout />}>
              <Route path={routes.ABOUT} element={<Project />} />
              <Route path={routes.CONTACTS} element={<Contacts />} />
            </Route>
            {localAdminPath && <Route path={localAdminPath} element={<Admin />} />}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
        {hasRecordingDeepLink && (
          <Suspense fallback={null}>
            <RecordingDeepLink />
          </Suspense>
        )}
      </div>
    </QueryClientProvider>
  );
}

export default App;
