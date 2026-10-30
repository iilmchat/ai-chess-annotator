import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import type { PgnPosition } from "../lib/pgn";
import { DEMO_PGN } from "../lib/pgn";
import type { AnalyzeResponse, Level } from "../types";

interface AnalysisState {
  pgn: string;
  level: Level;
  analysis: AnalyzeResponse | null;
  positions: PgnPosition[];
  initialFen: string;
  selectedPly: number;
  /**
   * Текст текущего комментария (T-073): из него `AnalysisWorkspace` строит
   * стрелки на доске. Не персистится и живёт ровно столько, сколько
   * отображается разбор; панель комментария обновляет его после генерации,
   * правки и при смене анализа/уровня.
   */
  commentaryText: string | null;
  setPgn: (pgn: string) => void;
  setLevel: (level: Level) => void;
  setAnalysis: (
    analysis: AnalyzeResponse,
    positions: PgnPosition[],
    initialFen: string,
  ) => void;
  setSelectedPly: (ply: number) => void;
  setCommentaryText: (text: string | null) => void;
  reset: () => void;
}

export const useAnalysisStore = create<AnalysisState>()(
  persist(
    (set) => ({
      pgn: DEMO_PGN,
      level: "club",
      analysis: null,
      positions: [],
      initialFen: "",
      selectedPly: 0,
      commentaryText: null,
      setPgn: (pgn) =>
        set({
          pgn,
          analysis: null,
          positions: [],
          initialFen: "",
          selectedPly: 0,
        }),
      setLevel: (level) => set({ level }),
      setAnalysis: (analysis, positions, initialFen) =>
        set({
          analysis,
          positions,
          initialFen,
          selectedPly: positions.length,
          // Новый разбор — старый комментарий к нему не относится.
          commentaryText: null,
        }),
      setSelectedPly: (selectedPly) => set({ selectedPly }),
      setCommentaryText: (commentaryText) => set({ commentaryText }),
      reset: () =>
        set({
          pgn: DEMO_PGN,
          level: "club",
          analysis: null,
          positions: [],
          initialFen: "",
          selectedPly: 0,
          commentaryText: null,
        }),
    }),
    {
      name: "chessannotator.analysis",
      storage: createJSONStorage(() => sessionStorage),
      // commentaryText намеренно не сохраняется: это производное состояние
      // панели комментария, а не часть разбора.
      partialize: (state) => ({
        pgn: state.pgn,
        level: state.level,
        analysis: state.analysis,
        positions: state.positions,
        initialFen: state.initialFen,
        selectedPly: state.selectedPly,
      }),
    },
  ),
);
