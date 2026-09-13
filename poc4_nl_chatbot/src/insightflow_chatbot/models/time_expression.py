"""The closed relative-time vocabulary the planner is allowed to choose from. See hld.md's
"Relative dates resolve deterministically, not via LLM date arithmetic" and class_diagram.md's
resolved "TimeExpression enum coverage" question: 7 values, no parametric LAST_N_DAYS(n) in v1,
until a real benchmark question set demonstrates the fixed enum can't express something asked.
"""
from enum import Enum


class TimeExpression(str, Enum):
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    THIS_QUARTER = "this_quarter"
    LAST_QUARTER = "last_quarter"
    THIS_YEAR = "this_year"
    LAST_YEAR = "last_year"
    ALL_TIME = "all_time"
