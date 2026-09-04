import SearchSelect from '../SearchSelect';
import { useStore } from '../../store';
import { useEntitySearch } from '../../common/hooks/useEntitySearch';
import { useGroups } from '../../common/hooks/usePreloadedList';

const GroupSearch = () => {
  const { data: groups = [] } = useGroups();
  const group = useStore((state) => state.group);
  const setGroup = useStore((state) => state.setGroup);

  const { handleChange } = useEntitySearch('groupId', groups, setGroup);

  return <SearchSelect options={groups} value={group} onChange={handleChange} />;
};

export default GroupSearch;
