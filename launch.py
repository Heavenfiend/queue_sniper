"""Интерактивный запуск QSniper с рабочего стола."""
import getpass
import sys

import local_settings as settings
import queue_sniper as sniper


def enter_credentials():
    email = input("Почта от сайта: ").strip()
    password = getpass.getpass("Пароль от сайта: ")
    settings.save_credentials(email, password)
    print("Данные входа сохранены с шифрованием Windows.")
    return email, password


def account_menu():
    while True:
        try:
            credentials = settings.load_credentials()
        except ValueError as exc:
            print(exc)
            credentials = None
        print("\nДанные входа")
        if credentials:
            print(f"Почта: {credentials[0]}")
            print(f"Пароль: {credentials[1]}")
        else:
            print("Почта и пароль ещё не сохранены.")
        print("1. Изменить почту и пароль" if credentials else "1. Ввести почту и пароль")
        print("0. Вернуться в главное меню")
        choice = input("Выбери действие [0]: ").strip() or "0"
        if choice == "0":
            return
        if choice == "1":
            try:
                enter_credentials()
            except (OSError, ValueError) as exc:
                print(f"Ошибка: {exc}")
        else:
            print("Неизвестное действие")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        interval = settings.load_interval()
    except ValueError as exc:
        print(exc)
        interval = 5
    while True:
        try:
            print("\nQSniper — Основы криптографии")
            print("Фильтр: предмет №2 ИЛИ название содержит «криптограф»")
            print(f"Пауза между проверками: {interval:g} с")
            print("1. Проверить подключение БЕЗ записи")
            print("2. Включить автозапись до первой успешной записи")
            print("3. Посмотреть / изменить почту и пароль")
            print("4. Изменить интервал проверки")
            print("5. Послушать сигнал успешной записи")
            print("6. Удалить сохранённые данные входа")
            print("0. Выйти\n")
            choice = input("Выбери режим [1]: ").strip() or "1"
            if choice == "0":
                return 0
            if choice == "3":
                account_menu()
            elif choice == "4":
                interval = settings.save_interval(input("Пауза между проверками в секундах (не меньше 1): "))
                print(f"Сохранено: {interval:g} с. Это частота проверки, а не задержка после появления очереди.")
            elif choice == "5":
                sniper.alarm()
            elif choice == "6":
                settings.forget_credentials()
                print("Сохранённые данные входа удалены.")
            elif choice in ("1", "2"):
                credentials = settings.load_credentials()
                if credentials is None:
                    print("Сначала сохраним вход — он будет использоваться и при следующих запусках.")
                    credentials = enter_credentials()
                if choice == "2":
                    print(f"Автозапись: пауза между проверками {interval:g} с. Остановка — Ctrl+C.")
                    print("Подходящая уже существующая очередь тоже может быть выбрана.")
                    print("Оставь компьютер включённым, без сна; окно не закрывай.\n")
                arguments = ["--site", "https://funandchecks.ru", "--subject-id", "2",
                             "--match", "криптограф", "--interval", str(interval)]
                if choice == "1":
                    arguments.append("--check")
                result = sniper.main(arguments, credentials=credentials)
                del credentials
                if result:
                    print("Операция завершилась с ошибкой. Данные входа можно изменить в пункте 3.")
            else:
                print("Неизвестный режим")
        except KeyboardInterrupt:
            print("\nОперация остановлена. Возврат в главное меню.")
        except EOFError:
            return 0
        except (OSError, ValueError) as exc:
            print(f"Ошибка: {exc}")


if __name__ == "__main__":
    sys.exit(main())
