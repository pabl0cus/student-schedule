import type { Pair } from '../../models/Pair';
import type { Week } from '../../types/Week';

interface LessonKeyOptions {
  scopeKey: string;
  occurrenceDate: string;
  day: string;
  pair: Pair;
}

interface LegacyLessonKeyOptions {
  scopeKey: string;
  week: Week;
  day: string;
  pair: Pair;
}

interface LessonLookupKeyOptions extends LessonKeyOptions {
  week?: Week;
}

// The service keeps this wider allowance only so previously stored v2/v3 keys remain queryable.
export const MAX_LEGACY_LESSON_KEY_LENGTH = 4096;

const HASH_SEEDS = [0x811c9dc5, 0x9e3779b9, 0x85ebca6b, 0xc2b2ae35] as const;

const hash32 = (value: string, seed: number) => {
  let hash = seed;

  for (let index = 0; index < value.length; index += 1) {
    hash = Math.imul(hash ^ value.charCodeAt(index), 0x01000193);
  }

  hash ^= hash >>> 16;
  hash = Math.imul(hash, 0x85ebca6b);
  hash ^= hash >>> 13;
  hash = Math.imul(hash, 0xc2b2ae35);
  hash ^= hash >>> 16;

  return (hash >>> 0).toString(16).padStart(8, '0');
};

const createDigest = (value: string) => HASH_SEEDS.map((seed) => hash32(value, seed)).join('');

const getPairDiscriminators = (scopeKey: string, pair: Pair) => {
  const contextualPair = pair as Pair & {
    lecturer?: { id: string };
    groups?: { id: string }[];
  };

  const [scopeType, ...scopeIdParts] = scopeKey.split(':');
  const scopeId = scopeIdParts.join(':');
  const lecturerId = contextualPair.lecturer?.id || (scopeType === 'lecturer' ? scopeId : undefined);
  const groupIds = [
    ...(contextualPair.groups?.map((group) => group.id) || []),
    ...(scopeType === 'group' ? [scopeId] : []),
  ]
    .filter(Boolean)
    .filter((groupId, index, values) => values.indexOf(groupId) === index)
    .sort();

  return { groupIds, lecturerId };
};

export const createLessonKey = ({ scopeKey, occurrenceDate, day, pair }: LessonKeyOptions) => {
  const { groupIds, lecturerId } = getPairDiscriminators(scopeKey, pair);

  const canonicalLesson = JSON.stringify({
    version: 4,
    scopeKey,
    occurrenceDate,
    day,
    time: pair.time,
    name: pair.name,
    type: pair.type,
    tag: pair.tag,
    lecturerId: lecturerId || null,
    groupIds,
    location: pair.location
      ? {
          uri: pair.location.uri,
          title: pair.location.title,
        }
      : null,
  });

  return `v4:${createDigest(canonicalLesson)}`;
};

export const createVersion3LessonKey = ({ scopeKey, occurrenceDate, day, pair }: LessonKeyOptions) => {
  const { groupIds, lecturerId } = getPairDiscriminators(scopeKey, pair);

  return JSON.stringify({
    version: 3,
    scopeKey,
    occurrenceDate,
    day,
    time: pair.time,
    name: pair.name,
    type: pair.type,
    tag: pair.tag,
    lecturerId: lecturerId || null,
    groupIds,
  });
};

export const createLegacyLessonKey = ({ scopeKey, week, day, pair }: LegacyLessonKeyOptions) => {
  const { lecturerId } = getPairDiscriminators(scopeKey, pair);

  return JSON.stringify({
    version: 2,
    owner: lecturerId ? `lecturer:${lecturerId}` : scopeKey,
    week,
    day,
    time: pair.time,
    name: pair.name,
    type: pair.type,
    tag: pair.tag,
  });
};

export const createLessonLookupKeys = ({ scopeKey, occurrenceDate, week, day, pair }: LessonLookupKeyOptions) => {
  const currentKey = createLessonKey({ scopeKey, occurrenceDate, day, pair });
  const legacyKeys = [createVersion3LessonKey({ scopeKey, occurrenceDate, day, pair })];

  if (week) {
    legacyKeys.push(createLegacyLessonKey({ scopeKey, week, day, pair }));
  }

  return [currentKey, ...legacyKeys.filter((key) => key.length <= MAX_LEGACY_LESSON_KEY_LENGTH)].filter(
    (key, index, keys) => keys.indexOf(key) === index,
  );
};
