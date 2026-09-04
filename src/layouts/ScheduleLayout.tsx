import Footer from '../components/Footer';
import { Navbar } from '../containers/Navbar/Navbar';
import ScrollToTop from '../components/ScrollToTop';
import { Outlet, useLocation } from 'react-router-dom';
import Legend from '../components/Legend';
import { ScheduleWeekProvider } from '../common/context/ScheduleWeekContext';
import { routes } from '../common/constants/routes';

export const ScheduleLayout = () => {
  const location = useLocation();

  return (
    <ScheduleWeekProvider>
      <ScrollToTop>
        <Navbar />
        <div className="m-9 flex grow flex-col max-sm:m-4">
          <Outlet />
          {location.pathname !== routes.RECORDINGS && <Legend />}
        </div>
        <Footer />
      </ScrollToTop>
    </ScheduleWeekProvider>
  );
};
