import { NavLink, Route, Routes, useSearchParams } from 'react-router-dom';
import GroupSearch from '../../components/GroupSearch';
import LecturerSearch from '../../components/LecturerSearch';
import WeekNavigator from '../../components/WeekNavigator';
import { routes } from '../../common/constants/routes';
import { getLocalStorageItem } from '../../common/utils/parsedLocalStorage';
import { useStore } from '../../store';
import { cn } from '../../common/utils/cn';
import RecordingSearchInput from '../../components/RecordingSearchInput';

const scheduleLinks = [
  { value: routes.INDEX, label: 'Розклад занять' },
  { value: routes.SESSION, label: 'Розклад сесії' },
  { value: routes.LECTURER, label: 'Розклад для викладачів' },
  { value: routes.RECORDINGS, label: 'Пошук записів' },
];

const MainSettings = () => {
  const groupId = useStore((state) => state.group?.id);
  const lecturerId = useStore((state) => state.lecturer?.id);
  const [searchParams] = useSearchParams();

  const getLinkUrl = (url: string) => {
    const nextSearchParams = new URLSearchParams(searchParams);

    if (url !== routes.RECORDINGS) {
      nextSearchParams.delete('q');
      nextSearchParams.delete('scope');
      nextSearchParams.delete('page');
    } else {
      nextSearchParams.delete('week');
      nextSearchParams.delete('recordingId');
    }

    if (url.includes(routes.LECTURER)) {
      const savedLecturerId = lecturerId ?? getLocalStorageItem('lecturerId');
      nextSearchParams.delete('groupId');

      if (savedLecturerId) {
        nextSearchParams.set('lecturerId', String(savedLecturerId));
      }
    } else {
      const savedGroupId = groupId ?? getLocalStorageItem('groupId');
      nextSearchParams.delete('lecturerId');

      if (savedGroupId) {
        nextSearchParams.set('groupId', String(savedGroupId));
      }
    }

    const query = nextSearchParams.toString();
    return query ? `${url}?${query}` : url;
  };

  return (
    <div className="flex grow flex-col items-center gap-[24px] leading-[1.43] max-lg:w-full">
      <nav className="flex max-w-[calc(100vw-3rem)] snap-x snap-mandatory items-center justify-between gap-[37px] overflow-x-scroll whitespace-nowrap [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {scheduleLinks.map(({ value, label }) => (
          <NavLink
            end={value === routes.INDEX}
            key={value}
            to={getLinkUrl(value)}
            className={({ isActive }) =>
              cn(
                "relative cursor-pointer snap-center text-[18px] leading-[1.43] font-bold tracking-[0.01em] text-black no-underline snap-always after:top-[-12px] after:hidden after:h-[2px] after:rounded-[6px] after:bg-black after:content-['']",
                isActive && 'after:block',
              )
            }
            onClick={(event) =>
              event.currentTarget.scrollIntoView({
                inline: 'center',
                block: 'nearest',
                behavior: 'smooth',
              })
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="flex gap-[20px] max-lg:flex-col max-lg:items-center max-sm:w-full">
        <Routes>
          <Route
            index
            element={
              <>
                <WeekNavigator />
                <GroupSearch />
              </>
            }
          />
          <Route path={routes.SESSION} element={<GroupSearch />} />
          <Route
            path={routes.LECTURER}
            element={
              <>
                <WeekNavigator />
                <LecturerSearch />
              </>
            }
          />
          <Route
            path={routes.RECORDINGS}
            element={
              <>
                <GroupSearch />
                <RecordingSearchInput />
              </>
            }
          />
        </Routes>
      </div>
    </div>
  );
};

export default MainSettings;
