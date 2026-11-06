# Деплой на GPU через reverse SSH-туннель: Selectel VPS + Windows/LM Studio

Практическое руководство: как подключить облачный VPS (Selectel, Ubuntu,
Docker) к домашней Windows-машине с **LM Studio** и **RTX 3060** через
**обратный SSH-туннель**, чтобы backend в Docker ходил в локальную модель.
Схема повторяет паттерн соседнего проекта VetAssistant и закрывает
задачу **T-123** (`TODO.md`): local-first маршрутизация `BackendRouter`
(итерация 58, [`app/llm/router.py`](../../app/llm/router.py)) + настройки
`LLM_LOCAL_*` (итерация 59).

**Дата:** 2026-11-06 · **Итерация:** 59 · **Уровень:** ops / deployment

> **Что получится.** Порт LM Studio (`127.0.0.1:8034`) «публикуется» на
> внутреннем адресе VPS (`172.17.0.1:8034` или `172.18.0.1:8034`), и
> backend-контейнер ходит туда как в обычный OpenAI-совместимый API.
> Пока ПК с GPU включён — `club` обслуживается локально; ПК выключен —
> роутер за ~2 секунды уходит в облачный DeepSeek.
>
> **Честная оговорка.** Гайд написан по коду и конфигурации репозитория;
> шаги проверок ниже нужно выполнить на реальном стенде. Live-путь
> «туннель → LM Studio» в CI не прогонялся — это отмечено в
> [`reports/v1.5.0/ITERATION-58.md`](reports/v1.5.0/ITERATION-58.md) §5.

Мини-шпаргалка (детали — ниже):

```powershell
# Windows: запустить туннель (держать окно открытым или поставить в автозапуск)
ssh -N -R 172.17.0.1:8034:127.0.0.1:8034 root@<VPS_IP>
```

```bash
# VPS: туннель слушает нужный адрес и LM Studio отвечает
ss -ltnp | grep 8034
curl -fsS http://172.17.0.1:8034/v1/models
```

```dotenv
# VPS: .env backend-контейнера
LLM_LOCAL_BACKEND_URL=http://172.17.0.1:8034/v1
LLM_LOCAL_TIMEOUT_S=2
LLM_ROUTER_COOLDOWN_S=60
```

## Оглавление

1. [Назначение и схема](#1-назначение-и-схема)
2. [Требования](#2-требования)
3. [Подготовка VPS (Ubuntu)](#3-подготовка-vps-ubuntu)
   - [3.1. Пользователь для туннеля и ключи](#31-пользователь-для-туннеля-и-ключи)
   - [3.2. sshd: GatewayPorts и AllowTcpForwarding](#32-sshd-gatewayports-и-allowtcpforwarding)
   - [3.3. Firewall (UFW)](#33-firewall-ufw)
   - [3.4. Docker и адрес, на который биндить туннель](#34-docker-и-адрес-на-который-биндить-туннель)
4. [Подготовка Windows](#4-подготовка-windows)
   - [4.1. LM Studio](#41-lm-studio)
   - [4.2. SSH-клиент и ключ](#42-ssh-клиент-и-ключ)
   - [4.3. SSH-конфиг](#43-ssh-конфиг)
5. [Запуск туннеля](#5-запуск-туннеля)
   - [5.1. Ручной запуск](#51-ручной-запуск)
   - [5.2. Проверка, что туннель слушает нужный адрес](#52-проверка-что-туннель-слушает-нужный-адрес)
   - [5.3. Автозапуск и автореконнект](#53-автозапуск-и-автореконнект)
6. [Проверка туннеля](#6-проверка-туннеля)
   - [6.1. На VPS](#61-на-vps)
   - [6.2. Из контейнера backend](#62-из-контейнера-backend)
   - [6.3. На уровне приложения](#63-на-уровне-приложения)
   - [6.4. Чек-лист](#64-чек-лист)
7. [Конфигурация приложения на VPS](#7-конфигурация-приложения-на-vps)
8. [Поведение при выключении ПК](#8-поведение-при-выключении-пк)
   - [8.1. Маршрутизация по уровням](#81-маршрутизация-по-уровням)
   - [8.2. Честно о таймаутах](#82-честно-о-таймаутах)
9. [Безопасность](#9-безопасность)
10. [Траблшутинг](#10-траблшутинг)
11. [Rollback](#11-rollback)
12. [Ссылки](#12-ссылки)

---

## 1. Назначение и схема

Домашний ПК с RTX 3060 не имеет публичного IP и живёт за NAT. VPS имеет
публичный адрес и входящий SSH, поэтому соединение инициирует **домашняя
машина**, а порт «пробрасывается» на VPS уже изнутри SSH-сессии. Docker
на VPS видит этот порт как адрес на своём bridge-gateway.

```text
        Домашняя Windows-машина                              Selectel VPS (Ubuntu)
┌──────────────────────────────────────┐           ┌──────────────────────────────────────────┐
│  LM Studio                           │           │  sshd                                    │
│    127.0.0.1:8034  (/v1/models)      │           │    слушает 172.17.0.1:8034               │
│    модель qwen/qwen3-4b-2507         │           │         ▲                                │
│         ▲                            │           │    docker bridge (172.17/172.18.0.0/16)  │
│         │  target туннеля             │           │  ┌──────┴───────────────────────────┐   │
│  ssh.exe │ (Windows)                  │  SSH :22  │  │ backend (Docker, сервис app)     │   │
│         └────────────────────────────┼──────────►│  │ LLM_LOCAL_BACKEND_URL=           │   │
│  ssh -N -R 172.17.0.1:8034:          │  исходящий│  │   http://172.17.0.1:8034/v1      │   │
│          127.0.0.1:8034 root@VPS_IP  │  с дома   │  │ BackendRouter: local → DeepSeek  │   │
└──────────────────────────────────────┘           └──────────────────────────────────────────┘
```

Ключевые стороны команды туннеля:

| Часть `-R <bind>:<port>:<target>` | Где вычисляется | Что означает |
| :--- | :--- | :--- |
| `172.17.0.1` (bind) | на **VPS** | адрес на VPS, где sshd откроет listening-сокет; его и увидят контейнеры |
| `8034` (port) | на **VPS** | порт этого listening-сокета |
| `127.0.0.1:8034` (target) | на **Windows** | куда ssh-клиент отправляет соединения: локальный API LM Studio |

**Почему local-first.** Стоимость: локальная генерация бесплатна, облачные
токены не тратятся. Приватность: `SPEC.md` §10 — при доступном GPU партия
`club` не покидает хост (у `beginner` облако запрещено в принципе, см. §8).
Скорость и зависимость: ответ не ждёт внешний API, хотя туннель добавляет
RTT «дом ↔ VPS», а скорость получения ответа ограничена **исходящим**
каналом домашнего интернета (текст ответа идёт с ПК на VPS).

**Что делает `BackendRouter`.** Для уровня `club` фабрика собирает
`BackendRouter([local, deepseek])`: сначала локальный клиент с коротким
таймаутом и без ретраев, при ошибке соединения/таймауте — облачный
DeepSeek и circuit-breaker на 60 с. Детали поведения — в §8, устройство —
[`app/llm/router.py`](../../app/llm/router.py).

---

## 2. Требования

| Компонент | Версия / состояние | Зачем |
| :--- | :--- | :--- |
| **Windows** | 10/11, PowerShell 5.1+ | клиент туннеля; LM Studio |
| **OpenSSH-клиент** | встроенный `C:\Windows\System32\OpenSSH\ssh.exe` (`ssh -V`) | `ssh -R` |
| **LM Studio** | API server включён, порт `8034`, модель загружена | локальный OpenAI-совместимый API |
| **GPU** | NVIDIA RTX 3060 12 GB (или лучше), драйвер | генерация |
| **VPS** | Selectel, Ubuntu 22.04/24.04 | публичный хост |
| **Доступ на VPS** | root или sudo-пользователь + SSH-ключ | настройка sshd, Docker |
| **Docker** | Engine 24+ / Compose v2 | backend по [`docker-compose.prod.yml`](../../docker-compose.prod.yml) |
| **Входящий SSH** | 22/tcp открыт у провайдера и в UFW | приём туннеля |
| **Исходящий SSH** | с домашнего ПК разрешён | установка туннеля с дома |

Порты: на VPS наружу нужен **только 22**; `8034` наружу не публикуется
(см. §9). На домашнем ПК LM Studio слушает `127.0.0.1:8034`, больше ничего
открывать не нужно.

---

## 3. Подготовка VPS (Ubuntu)

Все команды — на VPS (`ssh root@<VPS_IP>`), в каталоге с клонированным
репозиторием (например, `/opt/ai-chess-annotator`).

### 3.1. Пользователь для туннеля и ключи

Можно ходить `root`-ом, но лучше отдельный пользователь без sudo: он нужен
только для `-N -R` (порт-форвардинг). `root` оставьте для администрирования.

```bash
# отдельный пользователь (рекомендуется)
sudo adduser --disabled-password --gecos "" tunnel
sudo install -d -m 700 -o tunnel -g tunnel /home/tunnel/.ssh
sudo install -m 600 -o tunnel -g tunnel /dev/null /home/tunnel/.ssh/authorized_keys
```

Публичный ключ с Windows добавите в §4.2. Если оставляете вход под
`root` — пропустите этот шаг.

### 3.2. sshd: GatewayPorts и AllowTcpForwarding

Проверьте текущее состояние (Ubuntu по умолчанию `GatewayPorts no`):

```bash
sudo sshd -T | grep -Ei '^(gatewayports|allowtcpforwarding|permitrootlogin|passwordauthentication) '
```

Создайте drop-in (Ubuntu 22.04/24.04 подключает `/etc/ssh/sshd_config.d/*.conf`):

```bash
sudo tee /etc/ssh/sshd_config.d/10-gpu-tunnel.conf >/dev/null <<'EOF'
# T-123: reverse SSH-туннель до домашней LM Studio.
# Без clientspecified sshd игнорирует адрес из -R и слушает только loopback.
GatewayPorts clientspecified
AllowTcpForwarding yes
EOF

sudo sshd -t                       # синтаксис
sudo systemctl restart ssh         # на Ubuntu служба называется ssh
sudo sshd -T | grep -Ei '^(gatewayports|allowtcpforwarding) '
# ожидаемо: gatewayports clientspecified / allowtcpforwarding yes
```

**Почему это критично.** С `GatewayPorts no` sshd **игнорирует** адрес из
`-R` и открывает listener только на loopback VPS (`127.0.0.1`/`::1`).
Туннель при этом выглядит работающим, но контейнер обращается к
`172.17.0.1:8034` и получает `Connection refused`. Симптом на клиенте
`Warning: remote port forwarding failed for listen port 8034` появляется,
когда sshd **вообще не смог** создать listener: порт-форвардинг запрещён
(`AllowTcpForwarding no`), порт `8034` уже кем-то занят на VPS (старый
туннель, локальный сервис) или сервер отклонил запрошенный адрес. Поэтому
после правки конфига проверяйте не только вывод `ssh`, но и
`sudo ss -ltnp | grep 8034` (§5.2).

Вместо `clientspecified` можно `GatewayPorts yes`, но тогда sshd биндит
`0.0.0.0` — порт становится слышен на всех интерфейсах VPS (закрывайте
файрволом, §9). `clientspecified` безопаснее: адрес выбирает клиент.

Если завели пользователя `tunnel` — при желании ограничьте его в том же
drop-in (необязательно, но уменьшает поверхность):

```text
Match User tunnel
    AllowTcpForwarding yes
    PermitTTY no
    AllowAgentForwarding no
    X11Forwarding no
```

### 3.3. Firewall (UFW)

Наружу — только SSH. Порт туннеля (`8034`) наружу **не открываем**: он
должен быть доступен лишь с самого VPS и из docker-сетей.

```bash
sudo ufw allow OpenSSH          # или: sudo ufw allow 22/tcp
sudo ufw enable
sudo ufw status verbose
# 8034 в правилах быть не должно — это внутренний адрес туннеля
```

Замечание: Docker публикует свои порты через iptables в обход UFW, но
`8034` публикует **sshd**, а не Docker, поэтому правило INPUT здесь
работает. Не добавляйте `ufw allow 8034`, даже если хочется «чтобы точно
работало»: это откроет Qwen-модель интернету.

### 3.4. Docker и адрес, на который биндить туннель

Backend работает в контейнере compose-сети. Ему нужен адрес **хоста VPS**
в этой сети. Посмотрите его напрямую у контейнера `app` — это самый
надёжный способ:

```bash
cd /opt/ai-chess-annotator
docker compose -f docker-compose.prod.yml ps -q app
# подставьте id контейнера в команду ниже
docker inspect -f '{{range $net, $cfg := .NetworkSettings.Networks}}{{$net}} gateway={{$cfg.Gateway}}{{"\n"}}{{end}}' <container_id>
# пример вывода: ai-chess-annotator_default gateway=172.18.0.1
```

Альтернативные способы (если контейнер ещё не запущен):

```bash
# default bridge Docker (docker0) — обычно 172.17.0.1
ip -4 addr show docker0
docker network inspect bridge -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}'

# compose-сеть проекта — обычно 172.18.0.1 (если 172.17.0.0/16 занят docker0)
docker network ls
docker network inspect ai-chess-annotator_default -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}'
```

**Запомните этот IP.** Он понадобится дважды и должен совпадать:
в команде `ssh -R <IP>:8034:...` (bind на VPS) и в
`LLM_LOCAL_BACKEND_URL=http://<IP>:8034/v1` (§7). Если backend в compose-сети,
берите её gateway (`172.18.0.1`); вариант `172.17.0.1` — для default bridge.
Оба адреса — локальные для VPS; наружу они не маршрутизируются.

Адрес должен существовать на VPS **до** запуска туннеля: sshd биндится в
уже настроенный интерфейс. Поэтому сначала поднимите docker-сеть/backend
(`docker compose -f docker-compose.prod.yml up -d`), затем туннель.

> Почему в TODO.md (T-123) в примере встречается `172.18.0.1:8035`: порт —
> произвольный, в этом проекте LM Studio и `.env.example` используют
> **8034**, поэтому гайд использует 8034. Порт должен совпадать в
> bind-части `-R`, в target `-R` и в `LLM_LOCAL_BACKEND_URL`.

---

## 4. Подготовка Windows

### 4.1. LM Studio

1. Установите LM Studio и загрузите модель (в `.env.example` —
   `VLLM_MODEL=qwen/qwen3-4b-2507`; идентификатор можно посмотреть в
   `/v1/models`, см. ниже).
2. Вкладка **Developer → Local Server** (названия вкладки/секции немного
   отличаются между версиями LM Studio): порт **8034**, сервер запущен.
   Тумблер «Serve on Local Network» для туннеля **не обязателен**:
   target туннеля — `127.0.0.1:8034`, то есть loopback самой Windows.
   Включайте его только если LM Studio нужен другим устройствам в LAN.
3. Проверьте API локально (в PowerShell именно `curl.exe`, а не алиас
   `curl` → `Invoke-WebRequest`):

```powershell
curl.exe -sS http://127.0.0.1:8034/v1/models
# ожидаем JSON вида:
# {"data":[{"id":"qwen/qwen3-4b-2507","object":"model",...}],"object":"list"}
```

4. Если модель не загружена, LM Studio может подгрузить её по JIT или
   вернуть ошибку — надёжнее загрузить модель заранее. Если в настройках
   сервера включено требование API-токена, пропишите его на VPS в
   `VLLM_API_KEY` (по умолчанию приложение шлёт заглушку `lm-studio`).
5. Идентификатор модели из `/v1/models` должен совпадать с `VLLM_MODEL`
   на VPS, иначе LM Studio ответит 404 `model not found`.

### 4.2. SSH-клиент и ключ

Проверьте наличие встроенного клиента:

```powershell
ssh -V
# OpenSSH_for_Windows_8.x / 9.x
```

Если `ssh` не найден: **Settings → Apps → Optional features → Add a
feature → OpenSSH Client** (или `Add-WindowsCapability -Online -Name
OpenSSH.Client~~~~0.0.1.0` из PowerShell от администратора).

Ключ (ed25519; `-C` — комментарий, чтобы отличать ключ в `authorized_keys`):

```powershell
ssh-keygen -t ed25519 -C "chess-gpu-tunnel" -f $env:USERPROFILE\.ssh\id_ed25519
# Enter — без пароля (для автозапуска, см. §5.3); пароль — если будете
# запускать вручную и использовать ssh-agent
```

Добавьте публичный ключ на VPS. Рабочий однострочник:

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@<VPS_IP> "mkdir -p ~/.ssh && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys 2>/dev/null; cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Если после этого вход не работает или ключ ругается на формат (в
PowerShell 5.1 пайп может добавить `\r`), используйте надёжный обходной
путь через `scp`:

```powershell
scp $env:USERPROFILE\.ssh\id_ed25519.pub root@<VPS_IP>:/tmp/id_ed25519.pub
ssh root@<VPS_IP> "cat /tmp/id_ed25519.pub >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && rm /tmp/id_ed25519.pub"
```

Для пользователя `tunnel` вместо `root`: замените `root` на `tunnel`,
каталог — `/home/tunnel/.ssh` (владелец `tunnel`), ключ добавляйте с
`sudo -u tunnel` или от самого пользователя.

Проверьте вход по ключу без пароля и без интерактива:

```powershell
ssh -o BatchMode=yes root@<VPS_IP> "hostname; ss -ltnp | grep 8034 || echo 'listener: none'"
```

### 4.3. SSH-конфиг

Создайте/дополните `%USERPROFILE%\.ssh\config`
(`$env:USERPROFILE\.ssh\config`):

```text
Host vps-gpu
    HostName <VPS_IP>
    User root
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
    ExitOnForwardFailure yes
    TCPKeepAlive yes
```

- `ServerAliveInterval 30` + `ServerAliveCountMax 3` — клиент шлёт keepalive
  каждые 30 с и рвёт соединение после 3 пропусков (≈90 с «тишины»); это
  лечит «мёртвые» туннели после смены сети/сна/роутера.
- `ExitOnForwardFailure yes` — если VPS не смог открыть listener (занятый
  порт, запрещён форвардинг), ssh **завершится**, а не будет висеть без
  туннеля. Это обязательное условие корректного автореконнекта (§5.3).
- `TCPKeepAlive yes` — TCP keepalive на уровне сокета (не путать с
  `ServerAliveInterval`).

Первый запуск `ssh` сделайте вручную: он спросит отпечаток host key —
подтвердите, чтобы запись попала в `known_hosts`. Автозапуск
(Планировщик/NSSM) такого вопроса задать не может и без записи в
`known_hosts` отвалится.

---

## 5. Запуск туннеля

### 5.1. Ручной запуск

На **Windows** (PowerShell или cmd; окно должно оставаться открытым):

```powershell
ssh -N -R 172.17.0.1:8034:127.0.0.1:8034 root@<VPS_IP>
```

С SSH-конфигом (§4.3) и алиасом — короче:

```powershell
ssh -N -R 172.17.0.1:8034:127.0.0.1:8034 vps-gpu
```

Замените `172.17.0.1` на адрес, найденный в §3.4 (`172.17.0.1` для
default bridge или, например, `172.18.0.1` для compose-сети).

| Флаг | Что делает |
| :--- | :--- |
| `-N` | не выполнять удалённую команду, только форвардинг (нет shell) |
| `-R <bind>:<port>:<target>` | reverse-форвардинг: открыть порт на VPS и проксировать в target на Windows |
| `-v` | подробный лог (добавьте при отладке) |

В успешном варианте команда **не печатает ничего** и продолжает работать.
Если появилось `Warning: remote port forwarding failed for listen port 8034`
(при `ExitOnForwardFailure yes` ssh сразу завершится) — см. §10.

### 5.2. Проверка, что туннель слушает нужный адрес

На VPS сразу после запуска:

```bash
sudo ss -ltnp | grep 8034
# ожидаемо:
# LISTEN 0 128 172.17.0.1:8034 0.0.0.0:* users:(("sshd",pid=...,fd=...))
```

- `172.17.0.1:8034` — всё правильно, контейнеры смогут подключиться.
- `127.0.0.1:8034` — sshd слушает только loopback: не применён
  `GatewayPorts clientspecified` (§3.2) или туннель запущен без bind-адреса.
- пусто — туннель не запущен/упал; смотрите вывод `ssh` на Windows.

### 5.3. Автозапуск и автореконнект

Туннель должен подниматься сам после перезагрузки и восстанавливаться
после обрывов. Три варианта — от простого к «как сервис».

**Вариант A. Планировщик заданий Windows (прямой `ssh.exe`).**
Самый простой: при падении `ssh` Планировщик перезапустит задачу, но с
задержкой (в примере — до минуты). Требование: `ExitOnForwardFailure yes`,
иначе ssh будет «успешно» висеть без туннеля, и Планировщик ничего не
заметит. Если нужен реконнект за ~5 секунд — вариант B.

```powershell
$ssh = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
$action  = New-ScheduledTaskAction -Execute $ssh `
    -Argument '-N -R 172.17.0.1:8034:127.0.0.1:8034 vps-gpu'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'ChessAnnotator GPU tunnel' `
    -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited
```

Проверка/управление:

```powershell
Get-ScheduledTask -TaskName 'ChessAnnotator GPU tunnel'
Start-ScheduledTask  -TaskName 'ChessAnnotator GPU tunnel'
Stop-ScheduledTask   -TaskName 'ChessAnnotator GPU tunnel'
Unregister-ScheduledTask -TaskName 'ChessAnnotator GPU tunnel' -Confirm:$false
```

**Вариант B (рекомендуется). PowerShell-цикл с реконнектом.**
Скрипт `C:\Tools\chess-gpu-tunnel.ps1` держит туннель живым: упал `ssh` —
пауза 5 секунд и повтор. Цикл запускается из Планировщика при входе.

```powershell
# C:\Tools\chess-gpu-tunnel.ps1
$sshExe = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
$sshArgs = @(
    '-N',
    '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=30',
    '-o', 'ServerAliveCountMax=3',
    '-R', '172.17.0.1:8034:127.0.0.1:8034',
    'vps-gpu'
)
$log = Join-Path $env:LOCALAPPDATA 'chess-gpu-tunnel.log'
while ($true) {
    "$(Get-Date -Format s) starting tunnel" | Add-Content $log
    & $sshExe @sshArgs *>> $log
    "$(Get-Date -Format s) ssh exited, retry in 5 s" | Add-Content $log
    Start-Sleep -Seconds 5
}
```

Регистрация скрипта в Планировщике (при входе пользователя, без окна):

```powershell
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $ps `
    -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Tools\chess-gpu-tunnel.ps1'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'ChessAnnotator GPU tunnel' `
    -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited
```

**Вариант C. NSSM (служба Windows).** Нужен, если туннель должен работать
до входа пользователя (например, после ребута без логина). Учтите: служба
под LocalSystem **не видит** `C:\Users\<вы>\.ssh`, поэтому либо задайте
аккаунт службы, либо укажите абсолютный путь к ключу флагом `-i`.

```powershell
nssm install LMStudioTunnel "$env:SystemRoot\System32\OpenSSH\ssh.exe" `
    "-N -o ExitOnForwardFailure=yes -i C:\Users\<you>\.ssh\id_ed25519 -R 172.17.0.1:8034:127.0.0.1:8034 vps-gpu"
nssm set LMStudioTunnel Start SERVICE_AUTO_START
nssm set LMStudioTunnel AppExit Default Restart
nssm start LMStudioTunnel
nssm status LMStudioTunnel
```

**Про пароль ключа и автозапуск.** Служба/задача не может ввести пароль
ключа. Варианты: (а) отдельный ключ **без пароля** только для туннеля
(файл в профиле пользователя, доступ — ACL Windows; на VPS при желании
ограничьте `from=`/`permitlisten` в `authorized_keys`, §9); (б) ключ с
паролем + Windows `ssh-agent`, но агент работает в рамках сессии
пользователя — после перезагрузки ключ надо добавить заново, и
автозапуск «до логина» с ним не сработает. Практичный выбор для дома —
вариант (а) + ключ только на туннель.

**Про сон Windows.** При уходе ПК в сон туннель рвётся; после пробуждения
цикл из варианта B восстановит его за ~5 с. Чтобы ПК-«сервер» не засыпал,
в электропитании выставьте «Never» для сна при питании от сети. LM Studio
при пробуждении может перезагрузить модель (первый запрос будет медленнее).

---

## 6. Проверка туннеля

Проверяйте по цепочке «VPS → контейнер → приложение»: так ошибка сразу
локализуется.

### 6.1. На VPS

```bash
sudo ss -ltnp | grep 8034
curl -fsS --max-time 5 http://172.17.0.1:8034/v1/models | head -c 400
echo
```

Ожидаемо: listener на нужном адресе и JSON LM Studio со списком моделей.
`Connection refused` — туннеля нет/он на loopback/порт другой.
`curl: (28)` (timeout) — туннель есть, но Windows-сторона не отвечает
(LM Studio не запущен, ПК уснул, сеть).

### 6.2. Из контейнера backend

В образе есть `curl` (его использует healthcheck), поэтому проверяем прямо
в работающем контейнере:

```bash
cd /opt/ai-chess-annotator
docker compose -f docker-compose.prod.yml exec -T app \
    curl -fsS --max-time 5 http://172.17.0.1:8034/v1/models
```

Если сервис `app` не запущен или нужен чистый тест сети — отдельным
контейнером в той же compose-сети:

```bash
docker run --rm --network ai-chess-annotator_default \
    curlimages/curl -fsS --max-time 5 http://172.17.0.1:8034/v1/models
```

(имя сети возьмите из `docker network ls`; в командах выше замените
`172.17.0.1` на ваш bind-IP из §3.4).

### 6.3. На уровне приложения

1. Убедитесь, что контейнер получил адрес (после правки `.env` и
   пересоздания, §7):

```bash
docker compose -f docker-compose.prod.yml exec -T app env | grep -E 'LLM_LOCAL|VLLM_BASE'
```

2. Проверьте логи: при первом `club`-запросе при доступном GPU **не
   должно** быть события `local_gpu_unavailable`:

```bash
docker compose -f docker-compose.prod.yml logs --since 30m app \
    | grep -E 'local_gpu_unavailable|llm_router_|llm_warmup|llm_completed'
```

3. Сделайте реальный запрос уровня `club` из UI/API (комментарий к уже
   проанализированной партии). В `llm_calls` должна появиться запись с
   `provider=lmstudio`:

```bash
# подставьте POSTGRES_USER/POSTGRES_DB из своего .env, если они не chess/chessannotator
docker compose -f docker-compose.prod.yml exec -T postgres \
    psql -U chess -d chessannotator -c \
    "SELECT provider, model, level, status, latency_ms, created_at
       FROM llm_calls ORDER BY id DESC LIMIT 5;"
```

Если запрос ушёл в DeepSeek (`provider=deepseek`), при живом LM Studio —
почти всегда сработал таймаут 2 с или неверный адрес; см. §8.2 и §10.

### 6.4. Чек-лист

- [ ] На Windows `curl.exe http://127.0.0.1:8034/v1/models` отвечает JSON.
- [ ] `sudo ss -ltnp` на VPS показывает `172.17.0.1:8034` (или ваш bind-IP).
- [ ] `curl` с VPS на этот адрес отдаёт JSON.
- [ ] `curl` **из контейнера** `app` отдаёт JSON.
- [ ] В `.env` контейнера `LLM_LOCAL_BACKEND_URL` равен этому адресу.
- [ ] `docker compose up -d app` пересоздал контейнер (не `restart`).
- [ ] `llm_calls` пишет `provider=lmstudio` при включённом ПК.
- [ ] После выключения ПК `club` уходит в DeepSeek, `beginner` — нет (§8).
- [ ] Туннель поднимается автоматически после перезагрузки Windows.

---

## 7. Конфигурация приложения на VPS

В `.env` на VPS (compose-сервис `app` читает его как `env_file`):

```dotenv
APP_ENV=prod

# ── T-123: локальный GPU через reverse SSH-туннель ────────────────
# Адрес LM Studio глазами backend-контейнера: listener sshd на VPS.
# Должен совпадать с bind-адресом из ssh -R (см. §3.4, §5.1).
LLM_LOCAL_BACKEND_URL=http://172.17.0.1:8034/v1

# Fail-fast таймаут локального вызова (секунды). 2 с — быстрый fallback,
# но для длинной генерации может быть мало: см. §8.2.
LLM_LOCAL_TIMEOUT_S=2
# Circuit-breaker: сколько секунд не дёргать локальный GPU после сбоя.
LLM_ROUTER_COOLDOWN_S=60

# Модель — ровно id из /v1/models LM Studio.
VLLM_MODEL=qwen/qwen3-4b-2507

# Облачный резерв для club и основной для gm.
DEEPSEEK_API_KEY=sk-...
```

| Переменная | Что задаёт | Значение по умолчанию |
| :--- | :--- | :--- |
| `LLM_LOCAL_BACKEND_URL` | адрес локального LLM (в туннеле — VPS-сторона) | пусто → используется `VLLM_BASE_URL` |
| `LLM_LOCAL_TIMEOUT_S` | таймаут HTTP-запроса локального клиента `club` | `2` |
| `LLM_ROUTER_COOLDOWN_S` | cooldown circuit-breaker'а роутера | `60` |
| `VLLM_BASE_URL` | исторический адрес LM Studio (legacy) | `http://127.0.0.1:8034/v1` |
| `LLM_TIMEOUT_S` | таймаут прочих LLM-запросов (DeepSeek-fallback, `beginner`/`gm`) | `120` |
| `DEEPSEEK_API_KEY` | ключ облака; без него `club` остаётся local-only | пусто |

Пояснения:

- **`LLM_LOCAL_BACKEND_URL` — единственная настройка «где LM Studio».**
  На dev-машине приложение по умолчанию ходит на `http://127.0.0.1:8034/v1`
  (это же значение — дефолт `VLLM_BASE_URL` в `.env.example`). На VPS
  `127.0.0.1` означает сам контейнер, поэтому адрес обязателен: вход
  туннеля.
- Если `LLM_LOCAL_BACKEND_URL` пуст/не задан, используется legacy
  `VLLM_BASE_URL` (совместимость с dev-окружениями). В
  [`docker-compose.prod.yml`](../../docker-compose.prod.yml) эта переменная
  по умолчанию равна `http://host.docker.internal:8034/v1`
  (`extra_hosts: host-gateway`) — это тоже gateway docker-сети, но лучше
  задать URL явно, чтобы он не зависел от дефолтов compose.
- В итерации 58 переменная таймаута называлась
  `LLM_ROUTER_LOCAL_TIMEOUT_S`; в 59 она переименована в
  `LLM_LOCAL_TIMEOUT_S`. Старое имя конфигурация больше не читает
  (неизвестные ключи игнорируются) — переименуйте в `.env`.
- Правки `.env` применяйте **пересозданием** контейнера: `restart`
  сохраняет старое окружение.

```bash
cd /opt/ai-chess-annotator
docker compose -f docker-compose.prod.yml up -d app
docker compose -f docker-compose.prod.yml exec -T app env | grep LLM_LOCAL_BACKEND_URL
```

`worker`/`postgres`/`redis` пересоздавать не нужно; `worker` LLM не
использует.

---

## 8. Поведение при выключении ПК

### 8.1. Маршрутизация по уровням

Роутер включается **только для `club`** и **только если задан
`DEEPSEEK_API_KEY`**. Остальные уровни — прямые клиенты (см.
[`app/llm/factory.py`](../../app/llm/factory.py)):

| Уровень | Бэкенд(ы) | ПК выключен / туннель упал | В облако? |
| :--- | :--- | :--- | :--- |
| `beginner` | LM Studio напрямую | ошибка LLM (таймаут/сеть), fallback отсутствует | **нет, по дизайну** (приватность, SPEC §10) |
| `club` | LM Studio → DeepSeek (`BackendRouter`) | 2 с fail-fast → DeepSeek + circuit-breaker | да, только при недоступности локального |
| `gm` | DeepSeek напрямую | работает (облако) | всегда |

Что происходит в `BackendRouter`
([`app/llm/router.py`](../../app/llm/router.py)):

1. Первый запрос при мёртвом туннеле ловит `httpx.ConnectError` /
   `httpx.TimeoutException` / `LLMTimeoutError` (включая обёрнутые в
   `LLMError` причины). HTTP 4xx/«кривой ответ» **не** считаются
   недоступностью и пробрасываются наружу.
2. Пишется warning `local_gpu_unavailable` (поля: `primary`, `fallback`,
   `error`, `cooldown_s`), запрос уходит в DeepSeek.
3. Открывается circuit на `LLM_ROUTER_COOLDOWN_S` (60 с): следующие
   запросы не ждут мёртвый GPU, а сразу идут в fallback (лог
   `llm_router_circuit_open`).
4. По истечении cooldown primary пробуется снова. Успех закрывает цепь и
   пишет `llm_router_primary_recovered` — после включения ПК local-first
   восстанавливается автоматически, без рестарта приложения.
5. Если ключа DeepSeek нет — роутер остаётся local-only (лог
   `llm_router_fallback_disabled`), `club` ведёт себя как `beginner`.
6. Прогрев при старте идёт через `beginner` (прямой локальный клиент) и
   **best-effort**: при выключенном ПК в логе будет `llm_warmup_failed`,
   приложение всё равно стартует.

Практический эффект: при выключенном ПК `club` деградирует до облака с
задержкой ~2 с на первом запросе и «пробным» 2-секундным запросом раз в
60 с; `beginner` честно отдаёт ошибку, а не тайком уходит в облако.

### 8.2. Честно о таймаутах

`LLM_LOCAL_TIMEOUT_S=2` — это **не «пинг» перед генерацией**, а таймаут
самого HTTP-запроса локального клиента: в фабрике для `club` локальный
клиент создаётся с `timeout_s=LLM_LOCAL_TIMEOUT_S` и `max_retries=0`.
`LLM_TIMEOUT_S` (120 с) действует для облачного fallback и для прямых
клиентов `beginner`/`gm`, но **не растягивает** локальный вызов `club`.

Что это значит на практике:

- Короткий таймаут нужен, чтобы fallback был быстрым: мёртвый туннель
  обнаруживается за секунды, а не за 120.
- Не-стримовый вызов ждёт весь ответ; если LM Studio генерирует дольше
  2 с, запрос будет признан таймаутом и уйдёт в DeepSeek **даже при
  живом GPU**. Стрим мягче: чанки идут часто, и 2-секундные паузы между
  ними редки.
- В отчёте итерации 58 прямо отмечено, что live-путь «локальный →
  облачный» проверен только mock-тестами, а не на железе
  ([`ITERATION-58.md`](reports/v1.5.0/ITERATION-58.md) §5). Поэтому на
  реальном стенде обязательно проверьте `latency_ms` в `llm_calls` и
  наличие `local_gpu_unavailable`.

Рекомендация: измерьте реальное время клубной генерации на вашей 3060
(значение `latency_ms` удачных локальных вызовов) и выберите
`LLM_LOCAL_TIMEOUT_S` чуть выше p95 (например, 30–90 с при длинных
пакетах фактов и медленном туннеле). Цена — медленнее fallback при
выключенном ПК (ожидание равно таймауту, но не чаще раза в
`LLM_ROUTER_COOLDOWN_S`). Если приоритет — мгновенный fallback, оставьте
2 с и учитывайте, что длинные `club`-ответы будут обслуживаться облаком.
Для туннеля через домашний интернет это первое, что стоит подкрутить.

---

## 9. Безопасность

- **Только ключи.** Вход по паролю отключите: `PasswordAuthentication no`,
  `PermitRootLogin prohibit-password` (или dedicated user с
  `AuthenticationMethods publickey`). Проверка:
  `sudo sshd -T | grep -Ei 'passwordauthentication|permitrootlogin'`.
- **Не публикуйте 8034.** Bind только на внутренний адрес
  (`172.17.0.1`/`172.18.0.1`), в UFW правило для 8034 **не создавать**.
  При `GatewayPorts yes` (вместо `clientspecified`) sshd биндит `0.0.0.0` —
  тогда полагайтесь на UFW.
- **У LM Studio нет аутентификации.** `VLLM_API_KEY=lm-studio` — заглушка,
  а не защита: любой, кто дотянется до 8034, сможет гонять модель.
  Периметр — SSH-туннель и файрвол, а не ключ LM Studio. Если в LM Studio
  включён собственный API-токен — задайте его в `VLLM_API_KEY`.
- **Ограничьте key-опции.** В `~/.ssh/authorized_keys` на VPS можно
  привязать ключ к домашнему IP и разрешить только нужный forward
  (OpenSSH 7.8+):

  ```text
  from="203.0.113.77",permitlisten="172.17.0.1:8034",port-forwarding ssh-ed25519 AAAA... chess-gpu-tunnel
  ```

  `permitlisten` ограничивает, какой адрес/порт клиент может открыть;
  `from=` — с какого IP можно войти. Если домашний IP динамический —
  `from=` не используйте, оставьте `permitlisten`.
- **`Match User tunnel`** (см. §3.2): без TTY/X11/agent forwarding —
  туннельному пользователю нельзя выдавать shell-возможности.
- **fail2ban** (опционально, если 22 открыт всему интернету):

  ```bash
  sudo apt-get install -y fail2ban
  sudo systemctl enable --now fail2ban
  sudo fail2ban-client status sshd
  ```

- **Логи без промптов.** Роутер и LLM-клиенты пишут только провайдера,
  модель, токены, задержку и тип ошибки — тексты партий/промптов в логи
  не попадают. Не включайте `-v`-логирование ssh в постоянную эксплуатацию
  и не логируйте тела HTTP-запросов на прокси.
- **Шифрование.** Трафик между домом и VPS идёт внутри SSH (порт 22) и
  шифрован; отдельный TLS для 8034 не нужен и не должен появляться на
  публичном интерфейсе.
- **Ключи.** Отдельный ключ только для туннеля; при компрометации —
  удалить строку из `authorized_keys`, перезапустить туннель с новым ключом.
  Не храните приватный ключ в репозитории (он и так в `.gitignore`-зоне
  пользовательского профиля).

---

## 10. Траблшутинг

| Симптом | Вероятная причина | Что делать |
| :--- | :--- | :--- |
| `Warning: remote port forwarding failed for listen port 8034`, ssh завершается | `AllowTcpForwarding no`; порт 8034 уже занят на VPS; `GatewayPorts` не `clientspecified` | `sudo ss -ltnp \| grep 8034`, `sudo sshd -T \| grep -Ei 'gatewayports\|allowtcpforwarding'`, освободить/сменить порт, повторить после `systemctl restart ssh` |
| `ss` показывает `127.0.0.1:8034` вместо `172.17.0.1:8034` | `GatewayPorts no` (дефолт) — адрес из `-R` игнорируется | Добавить `GatewayPorts clientspecified` (§3.2), `restart ssh`, перезапустить туннель |
| С VPS `curl` → `Connection refused` | туннель не запущен/упал; bind на другом адресе/порту | Проверить окно ssh и `ss`; перезапустить туннель; сверить IP из §3.4 |
| С VPS `curl` → timeout, `ss` слушает | Windows-сторона не отвечает: LM Studio выключен, ПК спит, сеть дома упала | `curl.exe http://127.0.0.1:8034/v1/models` на Windows; разбудить ПК; проверить `ServerAliveInterval` |
| С VPS работает, **из контейнера** — нет | bind не на той сети (например, `127.0.0.1` или docker0, а контейнер в compose-сети); приложение смотрит на `127.0.0.1` | Определить gateway контейнера (§3.4), биндить его; задать `LLM_LOCAL_BACKEND_URL` равным bind-адресу |
| Приложение пишет `local_gpu_unavailable` при живом LM Studio | неверный `LLM_LOCAL_BACKEND_URL`; 2-секундный таймаут; `VLLM_MODEL` не совпадает | `docker compose exec app env \| grep LLM_LOCAL`; curl из контейнера; см. §8.2 — поднять `LLM_LOCAL_TIMEOUT_S`; сверить модель с `/v1/models` |
| Клубные комментарии стабильно уходят в DeepSeek | не-стримовая генерация дольше `LLM_LOCAL_TIMEOUT_S=2` | Поднять `LLM_LOCAL_TIMEOUT_S` (30–90 с) и пересоздать `app`; смотреть `latency_ms` в `llm_calls` |
| LM Studio отвечает 404 `model not found` | `VLLM_MODEL` не равен id из `/v1/models` | `curl.exe -sS http://127.0.0.1:8034/v1/models`, поправить `VLLM_MODEL` на VPS |
| SSH рвётся через несколько минут простоя | NAT/роутер дома убивает idle-сессию; нет keepalive | `ServerAliveInterval 30`, `ServerAliveCountMax 3` (§4.3), автореконнект-цикл (§5.3) |
| После сна ПК туннель «висит», но не работает | keepalive не заметил мёртвое соединение; LM Studio не перезагрузил модель | Вариант B (§5.3) перезапускает ssh за 5 с; первый запрос после сна может быть медленным |
| `Permission denied (publickey)` | ключ не добавлен/не тот пользователь; права на `~/.ssh`; пайп `type` добавил `\r` | Повторить добавление через `scp` (§4.2), `chmod 700 ~/.ssh`, `chmod 600 authorized_keys`, `ssh -v` |
| `ssh: connect to host ... port 22: Connection refused/timeout` | порт 22 закрыт у провайдера/в UFW; неверный IP | Проверить security group Selectel и `ufw status`; `ss -ltnp \| grep :22` |
| Правки `.env` не применились | `docker compose restart` не перечитывает `.env` | `docker compose -f docker-compose.prod.yml up -d app` (пересоздание) |
| `Warning: remote port forwarding failed` после добавления drop-in | sshd не перезапущен или drop-in не подхвачен | `sudo sshd -t`, `sudo systemctl restart ssh`, `sudo sshd -T \| grep gatewayports` |
| В логах `llm_warmup_failed` при старте | ПК выключен/туннель не поднят — это ожидаемо | Ничего не делать; прогрев best-effort, приложение работает |
| Порт 8034 на VPS занят другим сервисом | старый туннель/локальный процесс | `sudo ss -ltnp \| grep 8034`, остановить процесс или взять другой порт (например, 18034) во всех трёх местах: `-R`, `.env`, `.env.example` |

Отладка SSH-туннеля: запустите на Windows `ssh -v -N -R ...` и смотрите
строки `remote forward`, `tcpip-forward`, `bind`. На VPS полезно
`sudo journalctl -u ssh -f` в момент подключения.

---

## 11. Rollback

Все шаги обратимы; схема БД не меняется, `llm_calls`/`commentaries`
остаются как есть.

**Остановить туннель.**

```powershell
# если запускали вручную — закройте окно или Ctrl+C
Stop-ScheduledTask -TaskName 'ChessAnnotator GPU tunnel'
# если задача больше не нужна:
Unregister-ScheduledTask -TaskName 'ChessAnnotator GPU tunnel' -Confirm:$false
# NSSM-вариант:
nssm stop LMStudioTunnel
nssm remove LMStudioTunnel confirm
```

После остановки listener на VPS исчезнет в течение секунд —
`ss -ltnp | grep 8034` пусто.

**Вернуть приложение к прежнему поведению.**

- Убрать/опустошить адрес туннеля — тогда локальный клиент пойдёт по
  legacy `VLLM_BASE_URL`:

  ```dotenv
  LLM_LOCAL_BACKEND_URL=
  ```

  В prod-compose это `http://host.docker.internal:8034/v1`, то есть без
  туннеля локальный вызов будет падать; `club` останется на DeepSeek
  (fallback), `beginner` будет отдавать ошибку — ожидаемое поведение
  «локального GPU нет».
- Вернуть dev-режим (LM Studio на этой же машине):
  `LLM_LOCAL_BACKEND_URL=http://127.0.0.1:8034/v1`.
- Если ПК надолго выключен и не хочется платить 2-секундным ожиданием на
  пробу раз в минуту — увеличьте cooldown на время:
  `LLM_ROUTER_COOLDOWN_S=600` (и пересоздайте `app`). Не убирайте
  `DEEPSEEK_API_KEY`, иначе `club` останется без резерва.
- Применить: `docker compose -f docker-compose.prod.yml up -d app`.

**Вернуть sshd-настройки VPS (если туннель больше не нужен).**

```bash
sudo rm /etc/ssh/sshd_config.d/10-gpu-tunnel.conf
sudo sshd -t
sudo systemctl restart ssh
sudo sshd -T | grep -Ei '^(gatewayports|allowtcpforwarding) '
# снова gatewayports no / allowtcpforwarding yes (дефолт Ubuntu)
```

**Убрать доступ.** Удалите строку туннельного ключа из
`~/.ssh/authorized_keys` (VPS) и, при желании, сам ключ на Windows
(`Remove-Item $env:USERPROFILE\.ssh\id_ed25519*`). Если заводили
пользователя `tunnel` и он не нужен — `sudo deluser --remove-home tunnel`.

**Проверка после rollback.** `curl` из контейнера на адрес туннеля должен
давать `Connection refused` (это нормально), а `club`-запрос — уходить в
`provider=deepseek` в `llm_calls`. Первый такой запрос подождёт
`LLM_LOCAL_TIMEOUT_S` секунд, затем сработает fallback.

---

## 12. Ссылки

- [`SPEC.md`](SPEC.md) §10 (Non-functional) — приватность и fallback
  локальная LLM → облачная (`BackendRouter`, T-123).
- [`reports/v1.5.0/ITERATION-58.md`](reports/v1.5.0/ITERATION-58.md) —
  `BackendRouter`, circuit-breaker, известное ограничение «live-путь не
  проверен на железе» (§5).
- [`app/llm/router.py`](../../app/llm/router.py) — `BackendRouter`,
  `is_unavailable_error`, логи `local_gpu_unavailable` /
  `llm_router_circuit_open` / `llm_router_primary_recovered`.
- [`app/llm/factory.py`](../../app/llm/factory.py) — маршрутизация
  `beginner` / `club` / `gm`, сборка локального клиента с
  `LLM_LOCAL_TIMEOUT_S`.
- [`app/config.py`](../../app/config.py) — `llm_local_backend_url`,
  `llm_local_timeout_s`, `llm_router_cooldown_s`, legacy `vllm_base_url`.
- [`TODO.md`](../../TODO.md) — **T-123** «Cloud Deployment via Reverse SSH
  GPU Tunnel» (паттерн VetAssistant, DoD, статус итераций 58–59).
- [`GETTING_STARTED.md`](GETTING_STARTED.md) §7 —
  production-развёртывание в Docker (общий контекст VPS).
- [`.env.example`](../../.env.example) и
  [`docker-compose.prod.yml`](../../docker-compose.prod.yml) — переменные
  окружения и сервис `app`.
