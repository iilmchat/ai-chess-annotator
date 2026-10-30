/**
 * T-071: загрузка PGN из файла.
 *
 * Модуль намеренно не зависит от React/DOM-глобалей на верхнем уровне:
 * `validatePgnFile` — чистая функция, а `readPgnFile` принимает фабрику
 * `FileReader`, чтобы её можно было проверить в node-окружении Vitest.
 */

/** Максимальный размер загружаемого PGN — 1 МБ. */
export const MAX_PGN_FILE_BYTES = 1024 * 1024;

/** Причина, по которой файл не принят (или не прочитан). */
export type PgnFileError =
  | "too_large"
  | "wrong_type"
  | "empty"
  | "read_error";

/**
 * Проверить файл до чтения. `null` — файл подходит.
 *
 * Порядок проверок важен: слишком большой файл сообщаем раньше, чем
 * неправильное расширение (размер однозначнее и не зависит от имени).
 */
export function validatePgnFile(file: {
  name: string;
  size: number;
}): PgnFileError | null {
  if (file.size > MAX_PGN_FILE_BYTES) {
    return "too_large";
  }
  if (!file.name.toLowerCase().endsWith(".pgn")) {
    return "wrong_type";
  }
  if (file.size === 0) {
    return "empty";
  }
  return null;
}

/**
 * Прочитать выбранный файл как текст.
 *
 * Сначала валидация (`validatePgnFile`), затем `FileReader.readAsText`.
 * Успех — обрезанный по краям текст; пустой результат после trim тоже
 * считается ошибкой `"empty"`. Ошибка чтения — `"read_error"`.
 *
 * @param file файл из `<input type="file">`
 * @param readerFactory фабрика FileReader (в тестах подменяется стабом)
 */
export function readPgnFile(
  file: File,
  readerFactory: () => FileReader = () => new FileReader(),
): Promise<string> {
  const reason = validatePgnFile(file);
  if (reason !== null) {
    return Promise.reject(reason);
  }

  return new Promise<string>((resolve, reject) => {
    const reader = readerFactory();
    reader.onload = () => {
      const text =
        typeof reader.result === "string" ? reader.result.trim() : "";
      if (!text) {
        reject("empty");
        return;
      }
      resolve(text);
    };
    reader.onerror = () => reject("read_error");
    reader.readAsText(file);
  });
}

/** Ключ i18n-сообщения для причины отказа. */
export function pgnFileErrorMessageKey(reason: PgnFileError): string {
  switch (reason) {
    case "too_large":
      return "pgn.uploadTooLarge";
    case "wrong_type":
      return "pgn.uploadWrongType";
    case "empty":
      return "pgn.uploadEmpty";
    case "read_error":
      return "pgn.uploadError";
  }
}
