import { Property } from './Property';
import TeacherIcon from '../../assets/icons/teacher.svg?react';
import { setLocalStorageItem } from '../../common/utils/parsedLocalStorage';
import { EntityWithNameAndId } from '../../models/EntityWithNameAndId';
import { useStore } from '../../store';
import { routes } from '../../common/constants/routes';
import { Link, useSearchParams } from 'react-router-dom';

interface Props {
  lecturer: EntityWithNameAndId;
}

const LecturerProperty = ({ lecturer }: Props) => {
  const setLecturer = useStore((store) => store.setLecturer);
  const [searchParams] = useSearchParams();

  const lecturerLinkParams = new URLSearchParams(searchParams);
  lecturerLinkParams.delete('groupId');
  lecturerLinkParams.delete('recordingId');
  lecturerLinkParams.set('lecturerId', lecturer.id);

  const handleLecturerClick = () => {
    setLocalStorageItem('lecturerId', lecturer.id);
    setLecturer(lecturer);
  };

  return (
    <Property>
      <TeacherIcon />
      <Link className="text-primary-font" onClick={handleLecturerClick} to={`${routes.LECTURER}?${lecturerLinkParams}`}>
        {lecturer.name}
      </Link>
    </Property>
  );
};

export default LecturerProperty;
