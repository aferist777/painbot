"""Constants of the show: the lines that repeat in every episode.

These are written by hand, not generated. The greeting and the sign-off are the
most repeated sentences in the whole product — a model rewriting them each time
would erode the one thing that makes separate reels feel like one series.
"""
from typing import Any

from app.admin.state import tune

GREETING = "Здарова, вайбкодер."
GREETING_ON_SCREEN = "Тренировка вайбкодинга"

# The line that closes every Instagram caption. The channel is named only in
# the Instagram profile: an @handle inside a caption would link to whoever
# holds that name on Instagram, and a bare URL there is not clickable anyway.
CTA = "Больше идей в тг — ссылка в профиле"

# The anchor never changes; the dare after it rotates.
ANCHOR = "Задание в канале."

# Betting that someone will not finish a 65-hour build "за вечер" reads as a
# lie, so the dare is picked to match the estimate we already have.
DARES = {
    "evening": [
        "Спорим, за вечер не соберёшь?",
        "Один вечер работы. Спорим, растянешь на неделю?",
        "Тут на вечер. Скажешь, что некогда?",
    ],
    "weekend": [
        "Спорим, до понедельника не добьёшь?",
        "Выходных хватит. Если не отвлечёшься.",
        "Два дня работы. Спорим, начнёшь и бросишь?",
    ],
    "week": [
        "Неделя вечеров. Спорим, сдуешься на третьем?",
        "Это не на вечер. Спорим, доведёшь?",
        "Тут на неделю. Большинство не дойдёт.",
    ],
}

# Aimed at the habit rather than the skill: these get answered with a promise,
# and a promise brings the person back to report.
DARES_HABIT = [
    "Спорим, сохранишь и забудешь?",
    "Спорим, дальше сохранёнок не уйдёт?",
    "Клод справится. Спорим, ты не начнёшь?",
]

# A dare every time turns into one note; every third episode gets a plain close.
DARES_CALM = [
    "Забирай и собирай. До завтра.",
    "Текст там же. Завтра новое.",
]

LEVELS = {5: "лёгкое", 4: "лёгкое", 3: "среднее", 2: "сложное", 1: "сложное"}
MIN_FEASIBILITY = 3  # below this a task is not honest to hand out as training


# The constants above are defaults. Everything below reads them through the
# panel, so a line can be rewritten without touching the file.

def greeting() -> str:
    return tune("show.greeting", GREETING)


def greeting_on_screen() -> str:
    return tune("show.greeting_screen", GREETING_ON_SCREEN)


def anchor() -> str:
    return tune("show.anchor", ANCHOR)


def cta() -> str:
    return tune("show.cta", CTA)


def min_feasibility() -> int:
    return tune("show.min_feasibility", MIN_FEASIBILITY)


def bucket(effort_hours: int) -> str:
    if effort_hours and effort_hours > 60:
        return "week"
    if effort_hours and effort_hours > 20:
        return "weekend"
    return "evening"


def dare(task_no: int, effort_hours: int) -> str:
    """Deterministic by episode number: no repeat two episodes running."""
    calm = tune("show.dares_calm", DARES_CALM)
    habit = tune("show.dares_habit", DARES_HABIT)
    name = bucket(effort_hours)
    pool = tune("show.dares_" + name, DARES[name])
    if calm and task_no % 3 == 0:
        return calm[(task_no // 3) % len(calm)]
    if habit and task_no % 4 == 1:
        return habit[task_no % len(habit)]
    return pool[task_no % len(pool)] if pool else ""


def closing(task_no: int, effort_hours: int) -> str:
    return (anchor() + " " + dare(task_no, effort_hours)).strip()


def level_of(solo_feasibility: Any) -> str:
    try:
        return LEVELS.get(int(solo_feasibility or 0), "среднее")
    except (TypeError, ValueError):
        return "среднее"
