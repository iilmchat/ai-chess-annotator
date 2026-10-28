import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  tokens?: number;
}

interface ChatState {
  messages: Record<string, ChatMessage[]>;
  append: (key: string, message: ChatMessage) => void;
  setMessages: (key: string, messages: ChatMessage[]) => void;
  clear: (key: string) => void;
}

export function conversationKey(
  analysisId: string | null,
  level: string,
): string {
  return analysisId ? `${analysisId}:${level}` : "";
}

export const useChatStore = create<ChatState>()(
  persist(
    (set) => ({
      messages: {},
      append: (key, message) =>
        set((state) => ({
          messages: {
            ...state.messages,
            [key]: [...(state.messages[key] ?? []), message],
          },
        })),
      setMessages: (key, messages) =>
        set((state) => ({
          messages: { ...state.messages, [key]: messages },
        })),
      clear: (key) =>
        set((state) => {
          const next = { ...state.messages };
          delete next[key];
          return { messages: next };
        }),
    }),
    {
      name: "chessannotator.chat",
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({ messages: state.messages }),
    },
  ),
);
