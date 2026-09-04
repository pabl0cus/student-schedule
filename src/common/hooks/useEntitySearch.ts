import { getLocalStorageItem, setLocalStorageItem } from '../utils/parsedLocalStorage';
import { useEffect } from 'react';

import { EntityWithNameAndId } from '../../models/EntityWithNameAndId';
import { useSearchParams } from 'react-router-dom';

export const useEntitySearch = <T extends EntityWithNameAndId>(
  storageKey: string,
  items: T[],
  setValue: (value?: T) => void,
) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const itemId = searchParams.get(storageKey) || getLocalStorageItem<string>(storageKey);

  useEffect(() => {
    if (!itemId || searchParams.get(storageKey) === itemId) {
      return;
    }

    const nextSearchParams = new URLSearchParams(searchParams);
    nextSearchParams.set(storageKey, itemId);
    setSearchParams(nextSearchParams, { replace: true });
  }, [itemId, searchParams, setSearchParams, storageKey]);

  useEffect(() => {
    if (!itemId) {
      return;
    }
    const group = items.find(({ id }) => String(id) === itemId);
    setValue(group);
  }, [itemId, items, setValue]);

  const handleChange = (item: T) => {
    setValue(item);

    const nextSearchParams = new URLSearchParams(searchParams);
    nextSearchParams.set(storageKey, item.id);
    nextSearchParams.delete('recordingId');
    nextSearchParams.delete('page');
    setSearchParams(nextSearchParams, { replace: true });

    setLocalStorageItem(storageKey, item.id);
  };

  return { handleChange };
};
