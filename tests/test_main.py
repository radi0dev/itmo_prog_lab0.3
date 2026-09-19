#!/usr/bin/env python3
# pytest tests/test_main.py -v

import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.normpath(os.path.join(TESTS_DIR, "..", "src"))
sys.path.insert(0, SRC_DIR)

_token_path = os.path.join(SRC_DIR, "token.txt")
_old_cwd = os.getcwd()
_we_created_token = False

os.chdir(SRC_DIR) # main.py читает token.txt и users.json относительно cwd
if not os.path.exists(_token_path):
    with open(_token_path, "w", encoding="utf-8") as f:
        f.write("TOKEN=test-token\n")
    _we_created_token = True

import main # импорт после подготовки token.txt

# удаляем за собой фиктивный токен, если оригинала небыло

if _we_created_token:
    os.remove(_token_path)
os.chdir(_old_cwd)

# свой временный users.json на каждый тест

@pytest.fixture
def users_file(tmp_path, monkeypatch):
    path = tmp_path / "users.json"
    monkeypatch.setattr(main, "USERS_FILE", str(path))
    return path

# weather_text

def test_weather_text_known_code():
    assert main.weather_text(0) == "ясно"
    assert main.weather_text(61) == "дождь слабый"

def test_weather_text_unknown_code():
    assert main.weather_text(12345) == "код 12345"

# check_time_format

def test_check_time_format_valid():
    assert main.check_time_format("08:00") == (8, 0)
    assert main.check_time_format("23:59") == (23, 59)
    assert main.check_time_format("00:00") == (0, 0)

@pytest.mark.parametrize("text", [
    "8:00",
    "08:0",
    "24:00",
    "12:60",
    "abc",
    "08-00",
    "",
])def test_check_time_format_invalid(text):
    assert main.check_time_format(text) is None

# check_coordinates

def test_check_coordinates_valid():
    assert main.check_coordinates("59.93,30.36") == (59.93, 30.36)
    assert main.check_coordinates("59.93, 30.36") == (59.93, 30.36)
    assert main.check_coordinates("-33.86,151.20") == (-33.86, 151.2)

@pytest.mark.parametrize("text", [
    "59.93",
    "59.93;30.36",
    "100,30.36",
    "59.93,200",
    "abc,def",
])def test_check_coordinates_invalid(text):
    assert main.check_coordinates(text) is None

# check_timezone

def test_check_timezone_valid():
    assert main.check_timezone("Europe/Moscow") == "Europe/Moscow"
    assert main.check_timezone("UTC") == "UTC"

@pytest.mark.parametrize("text", [
    "Not/AZone",
    "blah",
    "",
])def test_check_timezone_invalid(text):
    assert main.check_timezone(text) is None

# has_time / has_position / has_timezone

def test_has_time():
    assert main.has_time(None) is False
    assert main.has_time({}) is False
    assert main.has_time({"time": "08:00"}) is True

def test_has_position():
    assert main.has_position(None) is False
    assert main.has_position({"latitude": 1}) is False # долготы нет
    assert main.has_position({"latitude": 1, "longitude": 2}) is True

def test_has_timezone():
    assert main.has_timezone(None) is False
    assert main.has_timezone({}) is False
    assert main.has_timezone({"timezone": "UTC"}) is True

# missing_settings_text
def test_missing_settings_text_all_missing():
    text = main.missing_settings_text(None)
    assert "/settime" in text
    assert "/setpos" in text
    assert "/settz" in text

def test_missing_settings_text_partial():
    user = {"time": "08:00", "latitude": 1, "longitude": 2} # часового пояса нет
    text = main.missing_settings_text(user)
    assert "/settz" in text
    assert "/settime" not in text
    assert "/setpos" not in text

# commands_text

def test_commands_text_contains_all_commands():
    text = main.commands_text()
    for command in main.COMMANDS:
        assert "/" + command.command in text

# format_table

def test_format_table_with_data():
    data = {
        "date": "2026-09-18",
        "average_temperature": 15.5,
        "hourly_forecast": [
            {"time": "2026-09-18T12:00", "temperature_2m": 16, "wind_speed_10m": 5, "weather": "ясно"},
        ],
    }
    text = main.format_table(data)
    assert "2026-09-18" in text
    assert "15.5" in text
    assert "12:00" in text
    assert "ясно" in text
    assert "<pre>" in text and "</pre>" in text

def test_format_table_no_data():
    data = {"date": "2026-09-18", "average_temperature": None, "hourly_forecast": []}
    text = main.format_table(data)
    assert "нет данных" in text

# хранение пользователей (users.json)

def test_load_users_returns_empty_dict_if_file_missing(users_file):
    assert main.load_users() == {}

def test_load_users_returns_empty_dict_on_broken_json(users_file):
    users_file.write_text("это не json", encoding="utf-8")
    assert main.load_users() == {}

def test_set_user_time_creates_new_user(users_file):
    main.set_user_time(111, "08:00")
    user = main.get_user(111)
    assert user["time"] == "08:00"
    assert user["active"] is True

def test_set_user_time_keeps_other_fields(users_file):
    main.set_user_position(111, 59.93, 30.36)
    main.set_user_time(111, "08:00")
    user = main.get_user(111)
    assert user["latitude"] == 59.93
    assert user["longitude"] == 30.36
    assert user["time"] == "08:00"

def test_set_user_position(users_file):
    main.set_user_position(222, 55.75, 37.62)
    user = main.get_user(222)
    assert user["latitude"] == 55.75
    assert user["longitude"] == 37.62

def test_set_user_timezone(users_file):
    main.set_user_timezone(333, "Europe/Moscow")
    user = main.get_user(333)
    assert user["timezone"] == "Europe/Moscow"

def test_get_user_unknown_returns_none(users_file):
    assert main.get_user(999) is None

def test_deactivate_user_active(users_file):
    main.set_user_time(444, "08:00") # active=True
    result = main.deactivate_user(444)
    assert result is True
    assert main.get_user(444)["active"] is False

def test_deactivate_user_not_active(users_file):
   # пользователя вообще нет
    assert main.deactivate_user(555) is False

   # пользователь есть, но уже неактивен
    main.set_user_time(555, "08:00")
    main.deactivate_user(555)
    assert main.deactivate_user(555) is False

def test_get_active_users(users_file):
    main.set_user_time(1, "08:00")
    main.set_user_time(2, "09:00")
    main.deactivate_user(2)

    active = main.get_active_users()
    assert "1" in active
    assert "2" not in active

# получение и обработка погоды

class FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def raise_for_status(self):
        pass # в тестах всегда считаем ответ успешным

    def json(self):
        return self._json_data

def test_get_weather_parses_response(monkeypatch):
    fake_json = {
        "daily": {"time": ["2026-09-18"], "temperature_2m_mean": [14.2]},
        "hourly": {
            "time": ["2026-09-18T00:00", "2026-09-18T01:00"],
            "temperature_2m": [10, 11],
            "wind_speed_10m": [3, 4],
            "weather_code": [0, 61],
        },
    }

    def fake_get(url, params, timeout):
        return FakeResponse(fake_json)

    monkeypatch.setattr(main.requests, "get", fake_get)

    result = main.get_weather("2026-09-18", 59.93, 30.36, "Europe/Moscow")

    assert result["date"] == "2026-09-18"
    assert result["average_temperature"] == 14.2
    assert len(result["hourly_forecast"]) == 2
    assert result["hourly_forecast"][0]["weather"] == "ясно"
    assert result["hourly_forecast"][1]["weather"] == "дождь слабый"

def test_build_forecast_text_success(monkeypatch):
    fake_data = {
        "date": "2026-09-18",
        "average_temperature": 10,
        "hourly_forecast": [],
    }
    monkeypatch.setattr(main, "get_weather", lambda day, lat, lon, tz: fake_data)

    text = main.build_forecast_text(59.93, 30.36, "Europe/Moscow")
    assert "2026-09-18" in text

def test_build_forecast_text_error(monkeypatch):
    def broken_get_weather(day, lat, lon, tz):
        raise RuntimeError("сеть недоступна")

    monkeypatch.setattr(main, "get_weather", broken_get_weather)

    text = main.build_forecast_text(59.93, 30.36, "Europe/Moscow")
    assert "Не удалось получить прогноз погоды" in text
    assert "сеть недоступна" in text
