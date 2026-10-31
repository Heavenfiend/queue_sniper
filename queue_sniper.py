#!/usr/bin/env python3
"""
Следит за FunAndChecks и сразу записывается в новую очередь, как только она появится.

Работает через тот же API, что и сам сайт:
  GET  /api/me/available-queue-events   — очереди, доступные твоей группе
  GET  /api/me/queue-events             — очереди, где ты уже записан
  POST /api/queues/{id}/join            — записаться

Нужен только Python 3.8+ (без дополнительных библиотек).

Запуск:
  python queue_sniper.py --site https://адрес-сайта --email you@mail.ru
  python queue_sniper.py --site https://адрес-сайта --email you@mail.ru --match "криптограф"
  python queue_sniper.py --site https://funandchecks.ru --subject-id 2 --match "криптограф" --check

Пароль спросит при запуске (или возьмёт из переменной окружения FC_PASSWORD).
Остановить — Ctrl+C.
"""
import argparse
import getpass
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def alarm() -> None:
    """Громко сообщить об успехе: звуковой сигнал в терминале (на Windows — beep)."""
    try:
        import winsound  # только Windows
        sound = Path(__file__).with_name("assets") / "success.wav"
        if sound.is_file():
            winsound.PlaySound(str(sound), winsound.SND_FILENAME)
            return
        for _ in range(3):
            winsound.Beep(1200, 250)
    except Exception:
        print("\a\a\a", end="", flush=True)


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class FunAndChecks:
    def __init__(self, site: str, email: str, password: str, timeout: float = 15):
        self.base = site.rstrip("/")
        self.email = email
        self.password = password
        self.timeout = timeout
        self.access = None
        self.refresh = None

    # ---------- низкоуровневые запросы ----------
    def _raw(self, method: str, path: str, body=None, auth: bool = True):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if auth and self.access:
            req.add_header("Authorization", f"Bearer {self.access}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(text) if text.strip() else None)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code == 429:
                retry = e.headers.get("Retry-After") or "10"
                try:
                    wait = max(0, float(retry))
                except ValueError:
                    try:
                        wait = max(0, parsedate_to_datetime(retry).timestamp() - time.time())
                    except (TypeError, ValueError, OverflowError):
                        wait = 10
                if not math.isfinite(wait):
                    wait = 10
                log(f"Сервер просит притормозить (429), жду {wait} с")
                time.sleep(wait)
            raise ApiError(e.code, text)

    def request(self, method: str, path: str, body=None):
        """Запрос с авторизацией; при 401 обновляет токен и повторяет один раз."""
        if not self.access:
            self.login()
        try:
            return self._raw(method, path, body)
        except ApiError as e:
            if e.status != 401:
                raise
        self.renew()
        return self._raw(method, path, body)

    # ---------- авторизация ----------
    def login(self, quiet=False) -> None:
        _, data = self._raw("POST", "/api/auth/login",
                            {"email": self.email, "password": self.password}, auth=False)
        self._set_tokens(data)
        if not quiet:
            log("Вход выполнен")

    def _set_tokens(self, data) -> None:
        if not isinstance(data, dict) or not all(
            isinstance(data.get(key), str) and data[key]
            for key in ("accessToken", "refreshToken")
        ):
            raise ValueError("Сайт вернул неожиданный формат авторизации")
        self.access, self.refresh = data["accessToken"], data["refreshToken"]

    def renew(self) -> None:
        """Access-токен живёт ~2 часа: обновляем по refresh, а если не вышло — входим заново."""
        if self.refresh:
            try:
                _, data = self._raw("POST", "/api/auth/refresh",
                                    {"refreshToken": self.refresh}, auth=False)
                self._set_tokens(data)
                log("Токен обновлён")
                return
            except (ApiError, ValueError):
                pass
        self.login()

    # ---------- очереди ----------
    def available(self) -> list:
        return self.request("GET", "/api/me/available-queue-events?includePast=false")[1] or []

    def mine(self) -> list:
        return self.request("GET", "/api/me/queue-events?includePast=false")[1] or []

    def details(self, event_id: int) -> dict:
        data = self.request("GET", f"/api/queues/{event_id}")[1]
        if not isinstance(data, dict) or not isinstance(data.get("subjectId"), int):
            raise ValueError("Сайт не вернул идентификатор предмета очереди")
        return data

    def join(self, event_id: int) -> None:
        self.request("POST", f"/api/queues/{event_id}/join")


def describe(ev: dict) -> str:
    return f"#{ev.get('id')} «{ev.get('name')}» ({ev.get('eventDateTime')})"


def describe_mine(ev: dict) -> str:
    statuses = {
        0: "сейчас на проверке", 1: "ожидаешь в очереди",
        2: "пропущен", 3: "завершил сдачу",
        "Checking": "сейчас на проверке", "Waiting": "ожидаешь в очереди",
        "Skipped": "пропущен", "Finished": "завершил сдачу",
    }
    status = statuses.get(ev.get("myStatus"), "записан, статус не указан")
    position = ev.get("myPosition")
    if position is not None:
        status += f", место: {position}"
    return f"«{ev.get('name') or 'Без названия'}» — {status}"


def matches(api: FunAndChecks, ev: dict, subject_id, text: str) -> bool:
    # Пустой текст не должен превращать OR в совпадение со всеми очередями
    if text and text.casefold() in (ev.get("name") or "").casefold():
        return True
    if subject_id is not None:
        # Список доступных очередей не содержит subjectId — получаем детали
        return api.details(ev["id"])["subjectId"] == subject_id
    return not text


def main(argv=None, credentials=None) -> int:
    p = argparse.ArgumentParser(description="Автозапись в новую очередь FunAndChecks")
    p.add_argument("--site", required=True, help="адрес сайта, например https://example.ru")
    p.add_argument("--email", help="почта, с которой входишь на сайт; иначе спросит при запуске")
    p.add_argument("--interval", type=float, default=5,
                   help="как часто проверять, в секундах (по умолчанию 5)")
    p.add_argument("--match", default="",
                   help="выбирать очереди, в названии которых есть этот текст "
                        "(без учёта регистра), например «криптограф»")
    p.add_argument("--subject-id", type=int,
                   help="номер предмета; вместе с --match работает как ИЛИ")
    p.add_argument("--check", action="store_true",
                   help="один раз проверить вход и очереди БЕЗ записи, затем выйти")
    p.add_argument("--keep", action="store_true",
                   help="не останавливаться после записи, а ловить и следующие очереди")
    args = p.parse_args(argv)
    if not math.isfinite(args.interval) or args.interval < 1:
        p.error("--interval должен быть конечным числом не меньше 1 секунды")
    if args.subject_id is not None and args.subject_id <= 0:
        p.error("--subject-id должен быть положительным")
    email = (args.email or (credentials[0] if credentials else input("Почта от сайта: "))).strip()
    if not email:
        p.error("почта не может быть пустой")

    password = credentials[1] if credentials else (
        os.environ.get("FC_PASSWORD") or getpass.getpass("Пароль от сайта: "))
    api = FunAndChecks(args.site, email, password)

    try:
        api.login(quiet=args.check)
    except ApiError as e:
        log(f"Не удалось войти: {e}")
        return 1
    except urllib.error.URLError as e:
        log(f"Сайт недоступен: {e.reason}")
        return 1
    except (TimeoutError, OSError, ValueError) as e:
        log(f"Не удалось подключиться: {e}")
        return 1

    filters = []
    if args.subject_id is not None:
        filters.append(f"предмет №{args.subject_id}")
    if args.match:
        filters.append(f"название содержит «{args.match}»")
    filt = ", фильтр: " + " ИЛИ ".join(filters) if filters else ", без фильтра предмета"
    if not args.check:
        log(f"Слежу за очередями раз в {args.interval:g} с{filt}. Остановить — Ctrl+C.")

    errors_in_row = 0
    last_heartbeat = time.monotonic()

    while True:
        try:
            mine = api.mine()
            joined_ids = {ev["id"] for ev in mine}
            available = api.available()
            candidates = []
            for ev in available:
                if not ev.get("allowSelfJoin") or ev["id"] in joined_ids:
                    continue
                try:
                    if matches(api, ev, args.subject_id, args.match):
                        candidates.append(ev)
                except ApiError as e:
                    if e.status != 404:
                        raise
                    log(f"Очередь {ev['id']} удалена во время проверки, пропускаю")

            if args.check:
                log(f"Вход выполнен: {email}")
                if mine:
                    log("Твои очереди:")
                    for ev in mine:
                        log(describe_mine(ev))
                else:
                    log("У тебя пока нет текущих очередей.")
                if not candidates:
                    log("Подходящих очередей для новой записи нет.")
                else:
                    log(f"Подходящих очередей для новой записи: {len(candidates)}.")
                for ev in candidates:
                    log(describe(ev))
                return 0

            for ev in candidates:
                log(f"Появилась очередь {describe(ev)} — записываюсь…")
                try:
                    api.join(ev["id"])
                    log(f"✅ ЗАПИСАН в {describe(ev)}")
                except ApiError as e:
                    if e.status == 409 and "already_joined" in e.body:
                        log(f"✅ Уже записан в {describe(ev)}")
                    elif e.status in (403, 404):
                        log(f"Запись пока недоступна в {describe(ev)}: {e}. "
                            "Проверю снова в следующем цикле.")
                        continue
                    else:
                        raise
                alarm()
                if not args.keep:
                    log("Готово, выхожу. Открой сайт, чтобы увидеть своё место.")
                    return 0

            errors_in_row = 0

            if time.monotonic() - last_heartbeat > 300:
                log("…всё ещё слежу, новых очередей нет")
                last_heartbeat = time.monotonic()

        except KeyboardInterrupt:
            raise
        except (ApiError, urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            errors_in_row += 1
            log(f"Ошибка ({errors_in_row} подряд): {e}")
            if args.check:
                log("Проверка не завершена из-за ошибки, запись не выполнялась")
                return 1
            if isinstance(e, ApiError) and e.status in (401, 403) and errors_in_row >= 3:
                log("Похоже, проблема со входом (неверный пароль или аккаунт). Останавливаюсь.")
                return 1
            # при сбоях сети — пауза растёт, но не больше минуты
            time.sleep(min(60, args.interval * errors_in_row))
            continue

        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        log("Остановлено")
