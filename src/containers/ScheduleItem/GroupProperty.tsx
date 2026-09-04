import { Property } from './Property';
import { setLocalStorageItem } from '../../common/utils/parsedLocalStorage';
import { useStore } from '../../store';
import { routes } from '../../common/constants/routes';
import { Group } from '../../models/Group';
import React from 'react';
import ThreeUsersIcon from '../../assets/icons/users-three.svg?react';
import { Link, useSearchParams } from 'react-router-dom';

interface Props {
  groups: Group[];
}

const GroupProperty = ({ groups }: Props) => {
  const setGroup = useStore((store) => store.setGroup);
  const [searchParams] = useSearchParams();

  const handleGroupClick = (group: Group) => {
    return () => {
      setLocalStorageItem('groupId', group.id);
      setGroup(group);
    };
  };

  const getGroupLink = (groupId: string) => {
    if (!groupId) {
      return '#';
    }

    const groupLinkParams = new URLSearchParams(searchParams);
    groupLinkParams.delete('lecturerId');
    groupLinkParams.delete('recordingId');
    groupLinkParams.set('groupId', groupId);

    return `${routes.INDEX}?${groupLinkParams}`;
  };

  return (
    <Property>
      <ThreeUsersIcon />
      <div>
        {groups.map((group, index) => (
          <React.Fragment key={group.id}>
            <Link
              className="text-primary-font"
              onClick={handleGroupClick(group)}
              key={group.id}
              to={getGroupLink(group.id)}
            >
              {group.name}
            </Link>
            {index < groups.length - 1 && ', '}
          </React.Fragment>
        ))}
      </div>
    </Property>
  );
};

export default GroupProperty;
