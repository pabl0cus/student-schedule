import { useSearchParams } from 'react-router-dom';

const RecordingSearchInput = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get('q') || '';

  return (
    <label className="relative block w-[380px] min-w-[300px] grow max-sm:w-full max-sm:min-w-0">
      <span className="sr-only">Пошук у записах і матеріалах</span>
      <svg
        className="pointer-events-none absolute top-1/2 left-3.5 size-[18px] -translate-y-1/2 text-neutral-600"
        viewBox="0 0 20 20"
        fill="none"
        aria-hidden="true"
      >
        <circle cx="8.5" cy="8.5" r="5.5" stroke="currentColor" strokeWidth="1.6" />
        <path d="m13 13 4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <input
        type="text"
        role="searchbox"
        className="min-h-[42px] w-full rounded-lg border border-neutral-300 bg-white py-0.5 pr-3 pl-10 text-black outline-none placeholder:text-neutral-600 hover:border-basic-blue focus:border-basic-blue focus:ring-2 focus:ring-basic-blue/10"
        placeholder="Предмет, викладач або слово з транскрипції"
        value={query}
        maxLength={200}
        autoComplete="off"
        onChange={(event) => {
          const nextSearchParams = new URLSearchParams(searchParams);
          if (event.target.value) {
            nextSearchParams.set('q', event.target.value);
          } else {
            nextSearchParams.delete('q');
          }
          nextSearchParams.delete('page');
          setSearchParams(nextSearchParams, { replace: true });
        }}
      />
    </label>
  );
};

export default RecordingSearchInput;
