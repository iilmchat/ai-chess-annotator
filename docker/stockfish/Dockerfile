FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends stockfish socat \
 && rm -rf /var/lib/apt/lists/*

EXPOSE 5000
# UCI поверх TCP: один процесс socat, форкается на каждое соединение
CMD ["socat", "TCP-LISTEN:5000,reuseaddr,fork", "EXEC:/usr/games/stockfish"]