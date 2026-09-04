import Footer from '../components/Footer';
import { Navbar } from '../containers/Navbar/Navbar';
import ScrollToTop from '../components/ScrollToTop';
import { Outlet } from 'react-router-dom';
import Legend from '../components/Legend';
import { ScheduleWeekProvider } from '../common/context/ScheduleWeekContext';

export const ScheduleLayout = () => {
  return (
    <ScheduleWeekProvider>
      <ScrollToTop>
        <Navbar />
        <div className="m-9 flex grow flex-col max-sm:m-4">
          <Outlet />
          <Legend />
        </div>
        <Footer />
      </ScrollToTop>
    </ScheduleWeekProvider>
  );
};
