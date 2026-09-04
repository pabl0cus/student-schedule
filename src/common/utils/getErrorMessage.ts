export const getErrorMessage = (error: unknown, fallback = 'Сталася невідома помилка') =>
  error instanceof Error ? error.message : fallback;
