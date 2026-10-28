import i18n from "i18next";
import { initReactI18next } from "react-i18next";

const resources = {
  ru: {
    translation: {
      app: {
        title: "ChessAnnotator",
        subtitle:
          "Комментарий к партии в стиле гроссмейстера — для новичка, клуба и гроссмейстера.",
        badge: "MVP v0.1.0",
        footer: "ChessAnnotator · MVP v0.1.0",
        footerNote:
          "Все варианты из ответа LLM проверяются Stockfish перед показом.",
      },
      nav: {
        label: "Навигация",
        home: "Анализ",
        history: "История",
      },
      usage: {
        label: "{{tier}}: {{used}}/{{limit}}",
        details: "Осталось {{remaining}} токенов сегодня",
        tier: { free: "Free", pro: "Pro" },
        modal: {
          title: "Дневной лимит токенов",
          titleOver: "Лимит токенов исчерпан",
          body: "Использовано {{used}} из {{limit}} токенов за сегодня (UTC).",
          proHint:
            "Pro-тир даёт больше токенов и доступ к тяжёлым моделям. Подключение — в следующей версии.",
        },
      },
      history: {
        title: "История партий",
        subtitle: "Последние 20 разобранных партий — откройте любую для просмотра.",
        refresh: "Обновить",
        empty: "Пока нет разобранных партий. Начните с анализа на главной.",
        date: "Дата",
        white: "Белые",
        black: "Чёрные",
        event: "Турнир",
        level: "Уровень",
        open: "Открыть",
      },
      analysis: {
        title: "Разбор партии",
        level: "Уровень: {{level}}",
        backToHistory: "К истории",
        missingId: "Не указан ID партии.",
        notFound: "Партия не найдена или ещё не разобрана.",
      },
      errorBoundary: {
        title: "Что-то сломалось",
        body: "Интерфейс столкнулся с ошибкой, но приложение живое. Попробуйте перезагрузить страницу.",
        reload: "Перезагрузить",
      },
      pgn: {
        title: "Партия",
        hint: "PGN или FEN-старт",
        level: "Уровень объяснения",
        analyze: "Разобрать партию",
        analyzing: "Анализирую… до 30 с",
        example: "Пример",
        placeholder: "Вставьте PGN партии",
        help:
          "Анализ выполняет Stockfish: оценки, лучшие ходы, мотивы и рекомендация под выбранный уровень.",
      },
      summary: {
        title: "Сводка",
        blunders: "Зевков: {{count}}",
        mistakes: "Ошибок: {{count}}",
        inaccuracies: "Неточностей: {{count}}",
        level: "Уровень анализа: {{level}}",
        moves: "{{count}} полуходов",
      },
      moves: {
        title: "Ходы",
        count: "{{count}} полуходов",
        start: "Начало",
      },
      board: {
        startTitle: "Начальная позиция",
        startHint:
          "Выберите ход в списке, чтобы увидеть оценку, лучший ход и мотивы.",
        best: "Лучший ход",
        loss: "Потеря",
        recommendation: "Рекомендация",
        noMotifs: "Мотивов на этом ходе не найдено.",
        eval: "Оценка",
        cp: "{{value}} cp",
      },
      commentary: {
        title: "Комментарий",
        level: "Уровень: {{level}}",
        generate: "Сгенерировать",
        regenerate: "Сгенерировать заново",
        generating: "Генерирую…",
        needAnalysis:
          "Сначала разберите партию — комментарий строится по её анализу.",
        empty:
          "Нажмите «Сгенерировать» — комментарий появится здесь через несколько секунд.",
        validated: "валидирован",
        notValidated: "без валидации",
        attempts: "попыток: {{count}}",
        edit: "Редактировать",
        save: "Сохранить правку",
        cancel: "Отмена",
        stop: "Остановить",
        stopped: "Генерация остановлена.",
        editorLabel: "Текст комментария",
        savedEdit: "edit #{{id}}",
        clear: "Очистить чат",
      },
      chat: {
        title: "Чат по партии",
        level: "Уровень: {{level}}",
        needAnalysis: "Сначала разберите партию — чат отвечает по её анализу.",
        thinking: "Думаю…",
        placeholder: "Спросите про ход или план…",
        disabledPlaceholder: "Сначала анализ",
        ask: "Спросить",
        stop: "Остановить",
        stopped: "Ответ остановлен.",
        questionLabel: "Вопрос по партии",
        suggestions: {
          players: "Кто играл белыми и чёрными?",
          mistake: "Какой ход был главной ошибкой?",
          better: "Что было лучше в ключевой момент?",
        },
      },
      common: {
        tokens: "{{count}} токенов",
        seconds: "{{value}} с",
        unknown: "Неизвестная ошибка",
        retry: "Повторить",
        editId: "edit #{{id}}",
        close: "Закрыть",
        loading: "Загрузка…",
      },
      level: {
        beginner: "Новичок",
        club: "Клубный игрок",
        gm: "Гроссмейстер",
      },
      class: {
        blunder: "Зевок",
        mistake: "Ошибка",
        inaccuracy: "Неточность",
        good: "Хорошо",
        best: "Лучший",
        brilliant: "Блестящий",
      },
      errors: {
        emptyPgn: "Вставьте PGN партии.",
      },
    },
  },
  en: {
    translation: {
      app: {
        title: "ChessAnnotator",
        subtitle:
          "Grandmaster-style game commentary for beginners, club players and grandmasters.",
        badge: "MVP v0.1.0",
        footer: "ChessAnnotator · MVP v0.1.0",
        footerNote:
          "Every move in an LLM answer is validated by Stockfish before display.",
      },
      nav: {
        label: "Navigation",
        home: "Analysis",
        history: "History",
      },
      usage: {
        label: "{{tier}}: {{used}}/{{limit}}",
        details: "{{remaining}} tokens left today",
        tier: { free: "Free", pro: "Pro" },
        modal: {
          title: "Daily token budget",
          titleOver: "Token budget exhausted",
          body: "Used {{used}} of {{limit}} tokens today (UTC).",
          proHint:
            "Pro gives more tokens and access to heavier models. Upgrade is planned for the next release.",
        },
      },
      history: {
        title: "Game history",
        subtitle: "The last 20 analyzed games — open any of them.",
        refresh: "Refresh",
        empty: "No analyzed games yet. Start with an analysis on the home page.",
        date: "Date",
        white: "White",
        black: "Black",
        event: "Event",
        level: "Level",
        open: "Open",
      },
      analysis: {
        title: "Game analysis",
        level: "Level: {{level}}",
        backToHistory: "Back to history",
        missingId: "Game id is missing.",
        notFound: "Game not found or not analyzed yet.",
      },
      errorBoundary: {
        title: "Something went wrong",
        body: "The UI hit an error, but the app is still alive. Try reloading the page.",
        reload: "Reload",
      },
      pgn: {
        title: "Game",
        hint: "PGN or FEN start",
        level: "Explanation level",
        analyze: "Analyze game",
        analyzing: "Analyzing… up to 30 s",
        example: "Example",
        placeholder: "Paste a PGN game",
        help:
          "Stockfish computes evaluations, best moves, motifs and a level-aware recommendation.",
      },
      summary: {
        title: "Summary",
        blunders: "Blunders: {{count}}",
        mistakes: "Mistakes: {{count}}",
        inaccuracies: "Inaccuracies: {{count}}",
        level: "Analysis level: {{level}}",
        moves: "{{count}} plies",
      },
      moves: {
        title: "Moves",
        count: "{{count}} plies",
        start: "Start",
      },
      board: {
        startTitle: "Starting position",
        startHint: "Select a move to see its evaluation, best move and motifs.",
        best: "Best move",
        loss: "Loss",
        recommendation: "Recommendation",
        noMotifs: "No motifs found for this move.",
        eval: "Evaluation",
        cp: "{{value}} cp",
      },
      commentary: {
        title: "Commentary",
        level: "Level: {{level}}",
        generate: "Generate",
        regenerate: "Regenerate",
        generating: "Generating…",
        needAnalysis: "Analyze a game first — commentary is built from it.",
        empty: "Click “Generate” — the commentary will appear here shortly.",
        validated: "validated",
        notValidated: "not validated",
        attempts: "attempts: {{count}}",
        edit: "Edit",
        save: "Save edit",
        cancel: "Cancel",
        stop: "Stop",
        stopped: "Generation stopped.",
        editorLabel: "Commentary text",
        savedEdit: "edit #{{id}}",
        clear: "Clear chat",
      },
      chat: {
        title: "Game chat",
        level: "Level: {{level}}",
        needAnalysis: "Analyze a game first — the chat answers from it.",
        thinking: "Thinking…",
        placeholder: "Ask about a move or plan…",
        disabledPlaceholder: "Analyze first",
        ask: "Ask",
        stop: "Stop",
        stopped: "Answer stopped.",
        questionLabel: "Question about the game",
        suggestions: {
          players: "Who played White and Black?",
          mistake: "What was the main mistake?",
          better: "What was better in the key moment?",
        },
      },
      common: {
        tokens: "{{count}} tokens",
        seconds: "{{value}} s",
        unknown: "Unknown error",
        retry: "Retry",
        editId: "edit #{{id}}",
        close: "Close",
        loading: "Loading…",
      },
      level: {
        beginner: "Beginner",
        club: "Club player",
        gm: "Grandmaster",
      },
      class: {
        blunder: "Blunder",
        mistake: "Mistake",
        inaccuracy: "Inaccuracy",
        good: "Good",
        best: "Best",
        brilliant: "Brilliant",
      },
      errors: {
        emptyPgn: "Paste a PGN game.",
      },
    },
  },
};

const saved =
  typeof window !== "undefined" ? window.localStorage.getItem("chess.lang") : null;

void i18n.use(initReactI18next).init({
  resources,
  lng: saved === "en" ? "en" : "ru",
  fallbackLng: "ru",
  supportedLngs: ["ru", "en"],
  interpolation: { escapeValue: false },
});

export default i18n;
