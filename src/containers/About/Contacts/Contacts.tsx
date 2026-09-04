import { CampusPhoto } from '../common/CampusPhoto';
import { PhotoWrapper } from '../common/PhotoWrapper';
import { TwoColumnsLayout } from '../../../layouts/TwoColumnsLayout';

export const Contacts = () => (
  <TwoColumnsLayout>
    <article>
      <h1>Контакти</h1>
      <p>
        Цей студентський сервіс не приймає офіційних звернень до КПІ. Окремий канал зв’язку з автором проєкту буде
        додано згодом.
      </p>
      <p>
        Актуальний розклад і офіційну інформацію перевіряйте на{' '}
        <a href="https://schedule.kpi.ua" target="_blank" rel="noreferrer">
          schedule.kpi.ua
        </a>
        .
      </p>
    </article>
    <PhotoWrapper>
      <CampusPhoto src="/contacts.jpg" alt="" />
    </PhotoWrapper>
  </TwoColumnsLayout>
);
