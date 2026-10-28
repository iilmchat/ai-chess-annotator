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
  setPgn: (pgn: string) => void;
  setLevel: (level: Level) => void;
  setAnalysis: (
    analysis: AnalyzeResponse,
    positions: PgnPosition[],
    initialFen: string,
  ) => void;
  setSelectedPly: (ply: number) => void;
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
        }),
      setSelectedPly: (selectedPly) => set({ selectedPly }),
      reset: () =>
        set({
          pgn: DEMO_PGN,
          level: "club",
          analysis: null,
          positions: [],
          initialFen: "",
          selectedPly: 0,
        }),
    }),
    {
      name: "chessannotator.analysis",
      storage: createJSONStorage(() => sessionStorage),
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
