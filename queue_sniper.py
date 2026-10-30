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

Пароль спросит при запуске (или возьмёт из переменной окружения FC_PASSWORD).
Остановить — Ctrl+C.
"""
import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def alarm() -> None:
    """Громко сообщить об успехе: звуковой сигнал в терминале (на Windows — beep)."""
    try:
        import winsound  # только Windows
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
                wait = int(e.headers.get("Retry-After") or 10)
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
    def login(self) -> None:
        _, data = self._raw("POST", "/api/auth/login",
                            {"email": self.email, "password": self.password}, auth=False)
        self.access, self.refresh = data["accessToken"], data["refreshToken"]
        log("Вход выполнен")

    def renew(self) -> None:
        """Access-токен живёт ~2 часа: обновляем по refresh, а если не вышло — входим заново."""
        if self.refresh:
            try:
                _, data = self._raw("POST", "/api/auth/refresh",
                                    {"refreshToken": self.refresh}, auth=False)
                self.access, self.refresh = data["accessToken"], data["refreshToken"]
                log("Токен обновлён")
                return
            except ApiError:
                pass
        self.login()

    # ---------- очереди ----------
    def available(self) -> list:
        return self.request("GET", "/api/me/available-queue-events?includePast=false")[1] or []

    def mine(self) -> list:
        return self.request("GET", "/api/me/queue-events?includePast=false")[1] or []

    def join(self, event_id: int) -> None:
        self.request("POST", f"/api/queues/{event_id}/join")


def describe(ev: dict) -> str:
    return f"#{ev.get('id')} «{ev.get('name')}» ({ev.get('eventDateTime')})"


def main() -> int:
    p = argparse.ArgumentParser(description="Автозапись в новую очередь FunAndChecks")
    p.add_argument("--site", required=True, help="адрес сайта, например https://example.ru")
    p.add_argument("--email", required=True, help="почта, с которой входишь на сайт")
    p.add_argument("--interval", type=float, default=5,
                   help="как часто проверять, в секундах (по умолчанию 5)")
    p.add_argument("--match", default="",
                   help="записываться только в очереди, в названии которых есть этот текст "
                        "(без учёта регистра), например «криптограф»")
    p.add_argument("--keep", action="store_true",
                   help="не останавливаться после записи, а ловить и следующие очереди")
    args = p.parse_args()

    password = os.environ.get("FC_PASSWORD") or getpass.getpass("Пароль от сайта: ")
    api = FunAndChecks(args.site, args.email, password)
    match = args.match.casefold()

    try:
        api.login()
    except ApiError as e:
        log(f"Не удалось войти: {e}")
        return 1
    except urllib.error.URLError as e:
        log(f"Сайт недоступен: {e.reason}")
        return 1

    filt = f", фильтр по названию: «{args.match}»" if args.match else ""
    log(f"Слежу за очередями раз в {args.interval:g} с{filt}. Остановить — Ctrl+C.")

    skipped: set = set()   # очереди, куда сервер отказал (чужая группа и т.п.) — не долбим их
    errors_in_row = 0
    last_heartbeat = time.monotonic()

    while True:
        try:
            joined_ids = {ev["id"] for ev in api.mine()}
            candidates = [
                ev for ev in api.available()
                if ev.get("allowSelfJoin")
                and ev["id"] not in joined_ids
                and ev["id"] not in skipped
                and match in (ev.get("name") or "").casefold()
            ]
            errors_in_row = 0

            for ev in candidates:
                log(f"Появилась очередь {describe(ev)} — записываюсь…")
                try:
                    api.join(ev["id"])
                    log(f"✅ ЗАПИСАН в {describe(ev)}")
                except ApiError as e:
                    if e.status == 409 and "already_joined" in e.body:
                        log(f"✅ Уже записан в {describe(ev)}")
                    elif e.status in (403, 404):
                        log(f"Сервер не пускает в {describe(ev)}: {e}. Больше не пробую.")
                        skipped.add(ev["id"])
                        continue
                    else:
                        raise
                alarm()
                if not args.keep:
                    log("Готово, выхожу. Открой сайт, чтобы увидеть своё место.")
                    return 0

            if time.monotonic() - last_heartbeat > 300:
                log("…всё ещё слежу, новых очередей нет")
                last_heartbeat = time.monotonic()

        except KeyboardInterrupt:
            raise
        except (ApiError, urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            errors_in_row += 1
            log(f"Ошибка ({errors_in_row} подряд): {e}")
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
