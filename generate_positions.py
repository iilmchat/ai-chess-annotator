import json
import glob
import chess.pgn
import chess.engine
from pathlib import Path
from app.config import Settings

# Загружаем настройки из .env
cfg = Settings()
STOCKFISH_PATH = cfg.stockfish_path

PGN_DIR = Path("data/gold_pgns")
OUTPUT_FILE = Path("data-chess/positions.jsonl")

def main():
    if not STOCKFISH_PATH:
        print("❌ Ошибка: STOCKFISH_PATH не задан в .env")
        return

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    pgn_files = glob.glob(str(PGN_DIR / "*.pgn"))
    if not pgn_files:
        print("Нет PGN файлов в data/gold_pgns/.")
        return

    print(f"Запускаем Stockfish: {STOCKFISH_PATH}")
    try:
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    except Exception as e:
        print(f"❌ Не удалось запустить Stockfish: {e}")
        return

    count = 0
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f_out:
        for pgn_file in pgn_files[:5]: # Берем 5 файлов
            with open(pgn_file, encoding="utf-8") as f_in:
                game = chess.pgn.read_game(f_in)
                if not game:
                    continue

                opening = game.headers.get("Opening", "Unknown")
                board = game.board()

                for move in game.mainline_moves():
                    move_number = board.fullmove_number
                    fen = board.fen()

                    # Глубина 12 для скорости
                    info = engine.analyse(board, chess.engine.Limit(depth=12))
                    best_move = info["pv"][0].uci() if info["pv"] else None
                    score = info["score"].white().score(mate_score=10000) / 100.0

                    if best_move:
                        record = {
                            "fen": fen,
                            "best_move": best_move,
                            "evaluation": score,
                            "move_number": move_number,
                            "opening": opening
                        }
                        f_out.write(json.dumps(record, ensure_ascii=False) + "\n")
                        count += 1

                    board.push(move)
                    if count >= 1000:
                        break
            if count >= 1000:
                break

    engine.quit()
    print(f"✅ Готово! Записано {count} позиций в {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
