const footerSectionClassName =
  'max-w-[360px] max-sm:max-w-none max-sm:text-center [&_p]:m-0 [&_p]:text-[12px] [&_p]:leading-5 [&_p]:font-normal';

export const Footer = () => {
  return (
    <footer className="grid w-full grid-cols-3 items-start gap-10 bg-brand-900 px-9 py-8 text-white max-md:grid-cols-1 max-md:gap-5 max-sm:px-4 max-sm:py-6">
      <section className={footerSectionClassName}>
        <h2 className="mb-2 text-sm font-bold">Розклад із записами</h2>
        <p>Зроблено студентом для студентів — щоб записи пар і важливі моменти з лекцій не губилися.</p>
        <nav className="mt-3 flex gap-4 max-sm:justify-center" aria-label="Додаткова навігація">
          <Link className="text-xs font-semibold underline decoration-white/45 underline-offset-4" to={routes.ABOUT}>
            Про проєкт
          </Link>
          <Link className="text-xs font-semibold underline decoration-white/45 underline-offset-4" to={routes.CONTACTS}>
            Контакти
          </Link>
        </nav>
      </section>
      <section className={footerSectionClassName}>
        <h2 className="mb-2 text-sm font-bold">Неофіційний проєкт</h2>
        <p>Це незалежний студентський сервіс. Він не пов’язаний з адміністрацією КПІ й не представляє університет.</p>
      </section>
      <section className={footerSectionClassName}>
        <h2 className="mb-2 text-sm font-bold">Офіційне джерело</h2>
        <p>
          Дані розкладу отримуємо з офіційного{' '}
          <a
            className="font-semibold text-white underline decoration-white/45 underline-offset-4 hover:decoration-white"
            target="_blank"
            rel="noreferrer"
            href="https://schedule.kpi.ua"
          >
            schedule.kpi.ua
          </a>
          . У разі розбіжностей орієнтуйтеся на першоджерело.
        </p>
      </section>
    </footer>
  );
};
import { Link } from 'react-router-dom';
import { routes } from '../../common/constants/routes';
