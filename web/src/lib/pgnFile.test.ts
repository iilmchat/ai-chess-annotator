import { describe, expect, it } from "vitest";

import type { PgnFileError } from "./pgnFile";
import {
  MAX_PGN_FILE_BYTES,
  pgnFileErrorMessageKey,
  readPgnFile,
  validatePgnFile,
} from "./pgnFile";

/**
 * `File` в node-окружении Vitest отсутствует, а `readPgnFile` использует
 * только `name`/`size` (сам текст отдаёт подставной FileReader), поэтому
 * достаточно структурного стаба.
 */
function pgnFile(name = "game.pgn", size = 128): File {
  return { name, size } as unknown as File;
}

/** Минимальный интерфейс FileReader, который реально дёргает readPgnFile. */
interface FakeFileReader {
  result: string | null;
  onload: (() => void) | null;
  onerror: (() => void) | null;
  readAsText: (file: File) => void;
}

interface FakeReaderOptions {
  result?: string | null;
  fail?: boolean;
}

function fakeReader(options: FakeReaderOptions = {}): FakeFileReader {
  const reader: FakeFileReader = {
    result: options.result ?? null,
    onload: null,
    onerror: null,
    readAsText: () => {
      // Колбэки уже навешены: readPgnFile выставляет их до readAsText.
      if (options.fail) {
        reader.onerror?.();
        return;
      }
      reader.onload?.();
    },
  };
  return reader;
}

/**
 * Приводим стаб к FileReader через `unknown`: структурно он повторяет только
 * ту часть API, которую модуль действительно использует.
 */
function asFileReader(reader: FakeFileReader): FileReader {
  return reader as unknown as FileReader;
}

describe("validatePgnFile", () => {
  it("принимает обычный .pgn", () => {
    expect(validatePgnFile({ name: "game.pgn", size: 128 })).toBeNull();
  });

  it("принимает расширение в верхнем регистре (.PGN)", () => {
    expect(validatePgnFile({ name: "GAME.PGN", size: 128 })).toBeNull();
  });

  it("отклоняет .txt как wrong_type", () => {
    expect(validatePgnFile({ name: "game.txt", size: 128 })).toBe("wrong_type");
  });

  it("отклоняет файл без расширения", () => {
    expect(validatePgnFile({ name: "game", size: 128 })).toBe("wrong_type");
  });

  it("отклоняет пустой .pgn как empty", () => {
    expect(validatePgnFile({ name: "game.pgn", size: 0 })).toBe("empty");
  });

  it("принимает ровно 1 МБ", () => {
    expect(
      validatePgnFile({ name: "game.pgn", size: MAX_PGN_FILE_BYTES }),
    ).toBeNull();
  });

  it("отклоняет 1 МБ + 1 байт как too_large", () => {
    expect(
      validatePgnFile({ name: "game.pgn", size: MAX_PGN_FILE_BYTES + 1 }),
    ).toBe("too_large");
  });

  it("too_large имеет приоритет над wrong_type", () => {
    expect(
      validatePgnFile({ name: "game.txt", size: MAX_PGN_FILE_BYTES + 1 }),
    ).toBe("too_large");
  });

  it("wrong_type имеет приоритет над empty", () => {
    expect(validatePgnFile({ name: "game.txt", size: 0 })).toBe("wrong_type");
  });
});

describe("pgnFileErrorMessageKey", () => {
  it("маппит все причины на ключи i18n", () => {
    const cases: Array<[PgnFileError, string]> = [
      ["too_large", "pgn.uploadTooLarge"],
      ["wrong_type", "pgn.uploadWrongType"],
      ["empty", "pgn.uploadEmpty"],
      ["read_error", "pgn.uploadError"],
    ];

    for (const [reason, key] of cases) {
      expect(pgnFileErrorMessageKey(reason)).toBe(key);
    }
  });
});

describe("readPgnFile", () => {
  it("читает текст и обрезает пробелы по краям", async () => {
    const reader = fakeReader({ result: "  1. e4 e5 1-0\n\n" });

    await expect(
      readPgnFile(pgnFile(), () => asFileReader(reader)),
    ).resolves.toBe("1. e4 e5 1-0");
  });

  it("сохраняет переводы строк внутри PGN", async () => {
    const reader = fakeReader({
      result: '[Event "Demo"]\n\n1. e4 e5 1-0',
    });

    await expect(
      readPgnFile(pgnFile(), () => asFileReader(reader)),
    ).resolves.toBe('[Event "Demo"]\n\n1. e4 e5 1-0');
  });

  it("отклоняет ошибку чтения как read_error", async () => {
    const reader = fakeReader({ fail: true });

    await expect(
      readPgnFile(pgnFile(), () => asFileReader(reader)),
    ).rejects.toBe("read_error");
  });

  it("отклоняет пустой результат чтения как empty", async () => {
    const reader = fakeReader({ result: "" });

    await expect(
      readPgnFile(pgnFile(), () => asFileReader(reader)),
    ).rejects.toBe("empty");
  });

  it("отклоняет результат из одних пробелов как empty", async () => {
    const reader = fakeReader({ result: " \n\t " });

    await expect(
      readPgnFile(pgnFile(), () => asFileReader(reader)),
    ).rejects.toBe("empty");
  });

  it("не создаёт FileReader для слишком большого файла", async () => {
    let created = false;
    const factory = () => {
      created = true;
      return asFileReader(fakeReader({ result: "1. e4" }));
    };

    await expect(
      readPgnFile(pgnFile("game.pgn", MAX_PGN_FILE_BYTES + 1), factory),
    ).rejects.toBe("too_large");
    expect(created).toBe(false);
  });

  it("не создаёт FileReader для файла с неверным расширением", async () => {
    let created = false;
    const factory = () => {
      created = true;
      return asFileReader(fakeReader({ result: "1. e4" }));
    };

    await expect(readPgnFile(pgnFile("game.txt"), factory)).rejects.toBe(
      "wrong_type",
    );
    expect(created).toBe(false);
  });

  it("отклоняет пустой файл до чтения", async () => {
    await expect(
      readPgnFile(pgnFile("game.pgn", 0), () =>
        asFileReader(fakeReader({ result: "" })),
      ),
    ).rejects.toBe("empty");
  });
});
