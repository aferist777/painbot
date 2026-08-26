"""Default source configuration. Edited freely — sources are seeded from here
on first run and afterwards live in the `sources` table.

The constants are defaults. The functions at the bottom are what the rest of
the code calls: they let the panel override a value without an edit here.
"""
from app.admin.state import tune

# --- Reddit -----------------------------------------------------------------
# Group 1: developer pain — people who will build the thing themselves
DEV_SUBS = [
    "webdev", "devops", "sysadmin", "selfhosted", "homelab",
    "ExperiencedDevs", "dataengineering", "learnprogramming",
    "opensource", "privacy", "programming",
]

# Group 2: business pain — people who pay money for software
BIZ_SUBS = [
    "SaaS", "microsaas", "Entrepreneur", "EntrepreneurRideAlong",
    "smallbusiness", "startups", "indiehackers", "SideProject",
    "freelance", "agency", "msp", "ProductManagement",
    "marketing", "analytics", "ecommerce", "nocode", "automate",
]

# Group 3: the archive seam — "somebody please build this"
IDEA_SUBS = [
    "SomebodyMakeThis", "AppIdeas", "Lightbulb", "CrazyIdeas",
]

REDDIT_SUBS = DEV_SUBS + BIZ_SUBS + IDEA_SUBS

# Arctic Shift searches by token, not by phrase, so the Reddit vocabulary is
# single words that people use when describing a chore they live with.
REDDIT_PAIN_WORDS = [
    "manually",
    "tedious",
    "spreadsheet",
    "workaround",
    "annoying",
    "wish",
]
REDDIT_MIN_SCORE = 5
# Arctic Shift answers in 7-11 seconds per request whatever you ask, and rejects
# limit=100 outright. Twelve requests per subreddit put a full sweep at almost
# three hours, so each run takes a slice of the vocabulary and the slice rotates
# by day — three nights cover it all.
REDDIT_WORDS_PER_RUN = 2

# --- Hacker News (Algolia, no key required) ---------------------------------
# Popularity is the wrong signal here: the top-voted HN threads are community
# chatter ("Tell HN: Merry Christmas"), not pain. What works is searching for
# the language people use when describing a chore. Comments carry most of it —
# and comments have no `points` in the index, so never filter them by score.
PAIN_PHRASES = [
    '"wish there was a tool"',
    '"there is no good tool"',
    '"no easy way to"',
    '"doing it manually"',
    '"do it by hand"',
    '"copy paste between"',
    '"spend hours"',
    '"waste hours"',
    '"keeping track of" spreadsheet',
]

HN_QUERIES = [{"tags": "comment", "query": phrase} for phrase in PAIN_PHRASES] + [
    {"tags": "ask_hn", "query": "tedious manual process"},
    {"tags": "story", "query": '"scratch my own itch"'},
]
HN_HITS = 30

# Vintage sweep: the same pain language inside a random historical window.
# The payoff is not the old idea but why_now — what exists today that did not
# exist then.
HN_VINTAGE_YEARS = (2008, 2017)
HN_VINTAGE_PHRASES = PAIN_PHRASES[:6]

# --- GitHub -----------------------------------------------------------------
GITHUB_QUERIES = [
    'label:"feature request" state:open comments:>15',
    'label:"help wanted" state:open comments:>10',
]

# --- screening --------------------------------------------------------------
SCREEN_BATCH = 20
SCORE_THRESHOLD = 60      # below this a pain never reaches the inbox
# Enough material is enough: when the inbox holds this many pains there is no
# point gathering more, and a single run never brings in more raw posts than
# this either.
INBOX_CAP = 300

SCORE_BATCH_CHARS = 1200   # per-item body budget handed to the screener


# ------------------------------------------------------------------ accessors
# REDDIT_SUBS is deliberately absent: subreddits are seeded into the `sources`
# table on first run and after that the table is the truth — the panel adds and
# disables them there, where the per-source statistics already live.


def pain_words() -> list:
    return tune("src.reddit_words", REDDIT_PAIN_WORDS)


def reddit_min_score() -> int:
    return tune("src.reddit_min_score", REDDIT_MIN_SCORE)


def words_per_run() -> int:
    return tune("src.words_per_run", REDDIT_WORDS_PER_RUN)


def hn_phrases() -> list:
    return tune("src.hn_phrases", PAIN_PHRASES)


def hn_hits() -> int:
    return tune("src.hn_hits", HN_HITS)


def hn_queries() -> list:
    return [{"tags": "comment", "query": phrase} for phrase in hn_phrases()] + [
        {"tags": "ask_hn", "query": "tedious manual process"},
        {"tags": "story", "query": '"scratch my own itch"'},
    ]


def vintage_phrases() -> list:
    return hn_phrases()[:6]


def github_queries() -> list:
    return tune("src.github_queries", GITHUB_QUERIES)


def screen_batch() -> int:
    return tune("screen.batch", SCREEN_BATCH)


def score_threshold() -> int:
    return tune("screen.threshold", SCORE_THRESHOLD)


def inbox_cap() -> int:
    return tune("screen.inbox_cap", INBOX_CAP)


def batch_chars() -> int:
    return tune("screen.batch_chars", SCORE_BATCH_CHARS)
