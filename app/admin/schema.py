"""What the panel shows, declared once.

Every field names where its default lives, so the panel never invents a value:
it either shows the code default or the override that was saved on top of it.
The three storage kinds decide how soon an edit bites:

    tune   read when it is used      — сразу
    cfg    read by app.config once   — после перезапуска
    plain  the keys that predate the panel, read live by their own module
"""
from typing import Any, Optional

from app import config
from app.admin.state import cfg, clear_tune, set_cfg, set_tune, tune
from app.collect import defaults as src
from app.db.repo import sget, sset
from app.llm import script as script_mod, show, social
from app.media import edit as edit_mod
from app.jobs import scheduler as plan


class Field:
    def __init__(
        self,
        key: str,
        label: str,
        kind: str = "line",
        default: Any = "",
        store: str = "tune",
        hint: str = "",
        options: Optional[list] = None,
    ):
        self.key = key
        self.label = label
        self.kind = kind
        self.default = default
        self.store = store
        self.hint = hint
        self.options = options

    # ------------------------------------------------------------ read/write

    def value(self) -> Any:
        if self.store == "tune":
            return tune(self.key, self.default)
        if self.store == "cfg":
            return cfg(self.key) or getattr(config, self.key, self.default)
        return sget(self.key) or self.default

    def write(self, raw: Any) -> None:
        value = self._cast(raw)
        if self.store == "tune":
            set_tune(self.key, value)
        elif self.store == "cfg":
            set_cfg(self.key, str(value))
        else:
            sset(self.key, "1" if value is True else "0" if value is False else value)

    def reset(self) -> None:
        if self.store == "tune":
            clear_tune(self.key)

    def _cast(self, raw: Any) -> Any:
        if self.kind == "int":
            return int(raw)
        if self.kind == "float":
            return float(raw)
        if self.kind == "bool":
            return bool(raw)
        if self.kind == "lines":
            if isinstance(raw, str):
                return [line.strip() for line in raw.splitlines() if line.strip()]
            return list(raw)
        if self.kind == "range":
            return [int(raw[0]), int(raw[1])]
        return str(raw).strip()

    def json(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "store": self.store,
            "hint": self.hint,
            "options": self.options,
            "value": self.value(),
            "default": list(self.default) if isinstance(self.default, tuple) else self.default,
            "restart": self.store == "cfg",
        }


class Section:
    def __init__(self, sid: str, title: str, note: str = "", fields=None, widget: str = ""):
        self.id = sid
        self.title = title
        self.note = note
        self.fields = fields or []
        self.widget = widget  # a section the browser draws itself

    def json(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "note": self.note,
            "widget": self.widget,
            "fields": [f.json() for f in self.fields],
        }


EDGE_VOICES = [
    ["ru-RU-DmitryNeural", "Дмитрий"],
    ["ru-RU-SvetlanaNeural", "Светлана"],
]

DAYS = [["mon", "понедельник"], ["tue", "вторник"], ["wed", "среда"],
        ["thu", "четверг"], ["fri", "пятница"], ["sat", "суббота"], ["sun", "воскресенье"]]


def sections() -> list[Section]:
    """Built fresh on every request: the defaults come from the live modules."""
    return [
        Section("keys", "Ключи", "Значения не приезжают в браузер — только по клику.",
                widget="keys"),

        Section("models", "Модели", "Читается один раз при старте.", [
            Field("LLM_PROVIDER", "Провайдер", "select", "kie", "cfg",
                  options=[["kie", "kie.ai"], ["openrouter", "OpenRouter"],
                           ["anthropic", "Anthropic"]]),
            Field("MODEL_SCREEN", "Отбор болей", "line", config.MODEL_SCREEN, "cfg"),
            Field("MODEL_IDEATE", "Идеи", "line", config.MODEL_IDEATE, "cfg"),
            Field("MODEL_WRITE", "Тексты и сценарий", "line", config.MODEL_WRITE, "cfg"),
            Field("KIE_BASE", "Адрес kie.ai", "line", config.KIE_BASE, "cfg",
                  "слаг модели подставляется в {model}"),
        ]),

        Section("voice", "Голос", "Меняется сразу, на следующей озвучке.", [
            Field("tts_provider", "Движок", "select", "edge", "plain",
                  options=[["edge", "Edge (бесплатно)"], ["eleven", "ElevenLabs"]]),
            Field("eleven_voice", "Голос ElevenLabs", "line", "", "plain", "id голоса"),
            Field("eleven_model", "Модель ElevenLabs", "line", "eleven_v3", "plain"),
            Field("edge_voice", "Голос Edge", "select", "ru-RU-DmitryNeural", "plain",
                  options=EDGE_VOICES),
            Field("voice_tempo", "Темп речи", "float", 1.15, "plain",
                  "1.0 — как есть, 1.15 — рабочий"),
            Field("burn_subs", "Вжигать субтитры в видео", "bool", False, "plain",
                  "выключено: субтитры делаешь в инстаграме"),
        ]),

        Section("show", "Шоу", "Постоянные фразы передачи и форма выпуска.", [
            Field("show.greeting", "Приветствие", "line", show.GREETING),
            Field("show.greeting_screen", "Надпись на первом кадре", "line",
                  show.GREETING_ON_SCREEN),
            Field("show.anchor", "Закрытие", "line", show.ANCHOR),
            Field("show.cta", "Призыв в подписи Instagram", "line", show.CTA),
            Field("show.dares_evening", "Подначки · вечер", "lines", show.DARES["evening"]),
            Field("show.dares_weekend", "Подначки · выходные", "lines", show.DARES["weekend"]),
            Field("show.dares_week", "Подначки · неделя", "lines", show.DARES["week"]),
            Field("show.dares_habit", "Подначки про привычку", "lines", show.DARES_HABIT,
                  hint="каждый выпуск, где номер даёт остаток 1 при делении на 4"),
            Field("show.dares_calm", "Спокойные закрытия", "lines", show.DARES_CALM,
                  hint="каждый третий выпуск закрывается без вызова"),
            Field("show.min_feasibility", "Минимальная выполнимость", "int",
                  show.MIN_FEASIBILITY, hint="ниже задание не выдаётся как тренировка"),
            Field("script.target_range", "Длина ролика, секунды", "range",
                  script_mod.TARGET_RANGE),
            Field("script.chars_per_second", "Символов в секунду речи", "float",
                  script_mod.CHARS_PER_SECOND, hint="замерено на темпе 1.15"),
            Field("script.min_beat", "Минимальный бит, секунды", "float",
                  script_mod.MIN_BEAT_SECONDS),
            Field("social.ig_limit", "Потолок подписи Instagram", "int", social.IG_LIMIT),
            Field("social.threads_limit", "Потолок поста Threads", "int",
                  social.THREADS_LIMIT, hint="лимит площадки — 500"),
        ]),

        Section("prompts", "Промпты", "Применяются на следующей генерации.",
                widget="prompts"),

        Section("sources", "Источники", "", widget="sources", fields=[
            Field("src.reddit_words", "Слова поиска в Reddit", "lines", src.REDDIT_PAIN_WORDS,
                  hint="Arctic Shift ищет по токенам, фразы не работают"),
            Field("src.words_per_run", "Слов за один прогон", "int", src.REDDIT_WORDS_PER_RUN,
                  hint="срез ротируется по дню — полный круг за несколько ночей"),
            Field("src.reddit_min_score", "Минимальный рейтинг поста", "int",
                  src.REDDIT_MIN_SCORE),
            Field("src.hn_phrases", "Фразы поиска в Hacker News", "lines", src.PAIN_PHRASES),
            Field("src.hn_hits", "Результатов на запрос HN", "int", src.HN_HITS),
            Field("src.github_queries", "Запросы в GitHub", "lines", src.GITHUB_QUERIES),
        ]),

        Section("screen", "Отбор", "", [
            Field("screen.threshold", "Порог оценки", "int", src.SCORE_THRESHOLD,
                  hint="ниже — боль не попадает в инбокс"),
            Field("screen.inbox_cap", "Потолок инбокса", "int", src.INBOX_CAP),
            Field("screen.batch", "Размер пачки", "int", src.SCREEN_BATCH),
            Field("screen.batch_chars", "Символов текста на пост", "int",
                  src.SCORE_BATCH_CHARS),
        ]),

        Section("video", "Видео", "Геометрия читается при старте.", [
            Field("FRAME_W", "Ширина", "int", config.FRAME_W, "cfg"),
            Field("FRAME_H", "Высота", "int", config.FRAME_H, "cfg"),
            Field("FRAME_FPS", "Кадров в секунду", "int", config.FRAME_FPS, "cfg"),
            Field("video.zoom", "Наезд камеры", "float", edit_mod.ZOOM,
                  hint="0.14 — заметно, но не укачивает"),
            Field("video.music_volume", "Громкость музыки", "float", edit_mod.MUSIC_VOLUME),
        ]),

        Section("schedule", "Расписание", "Читается при старте планировщика.", [
            Field("plan.collect_day", "День сбора", "select", plan.COLLECT_DAY,
                  options=DAYS),
            Field("plan.collect_hour", "Час сбора", "int", plan.COLLECT_HOUR),
            Field("plan.digest_hour", "Час утренней сводки", "int", plan.DIGEST_HOUR),
        ]),
    ]


def field(key: str) -> Optional[Field]:
    for section in sections():
        for item in section.fields:
            if item.key == key:
                return item
    return None
