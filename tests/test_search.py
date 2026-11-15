"""Find any task from My tasks and cross it off, whichever list it lives on."""
import json

from chores import db, logic
from tests.test_app import app, login, task_id  # noqa: F401
from tests.test_reminders import set_task
from tests.test_scheduling import selected


def codes(view, part="rows"):
    return [r["task"]["code"] for r in view[part]]


def chris():
    return logic.user_by_name("chris")


def test_search_finds_tasks_on_every_list_by_name_nickname_or_description(app):
    with app.app_context():
        set_task("H03", owner="chris", frequency="monthly", name="Clean the Gutters")
        set_task("H04", owner="chris", frequency="interval", repeat_every=6, repeat_unit="months",
                 repeat_start="2027-03-01", name="Chimney Flue", aliases="sweep the stack")
        set_task("H06", owner="chris", frequency="other", name="Descale Kettle", description="Run vinegar through it.")
        set_task("H07", owner="chris", frequency="once", due_date=None, name="Hang the Shelf")
        today = codes(logic.task_views(logic.dashboard("chris"))["today"])
        assert not {"H03", "H04", "H06", "H07"} & set(today)          # none of these is in Today
        assert codes(logic.search_view(chris(), "gutter")) == ["H03"]
        assert codes(logic.search_view(chris(), "  FLUE  chimney ")) == ["H04"]     # any order, any case
        assert codes(logic.search_view(chris(), "sweep stack")) == ["H04"]         # a nickname
        assert codes(logic.search_view(chris(), "vinegar")) == ["H06"]                # the description
        assert codes(logic.search_view(chris(), "shelf")) == ["H07"]
        assert codes(logic.search_view(chris(), "zzz")) == []
        assert logic.search_words(" ' ") == [] and logic.search_view(chris(), "")["rows"] == []


def test_name_matches_come_before_description_matches(app):
    with app.app_context():
        set_task("H03", owner="chris", frequency="monthly", name="Skylights", description="")
        set_task("H04", owner="chris", frequency="daily", name="Sweep", description="Porch, below the skylights.")
        assert codes(logic.search_view(chris(), "skylights")) == ["H03", "H04"]
        set_task("H06", owner="chris", frequency="daily", name="Car Seats")
        set_task("H07", owner="chris", frequency="daily", name="Carpet Shampoo")
        set_task("H08", owner="chris", frequency="daily", name="Zzscar Polish")
        found = [c for c in codes(logic.search_view(chris(), "car")) if c in ("H06", "H07", "H08")]
        assert found == ["H06", "H07", "H08"]          # whole word, then word beginning, then anywhere


def test_search_covers_shared_volunteer_and_partner_tasks_but_not_inactive_ones(app):
    with app.app_context():
        set_task("H03", owner="andrea", name="Zebra Mine")
        set_task("H04", owner="shared", name="Zebra Shared")
        set_task("H06", owner="volunteer", name="Zebra Open")
        set_task("H07", owner="chris", name="Zebra Retired", active=0)
        view = logic.search_view(chris(), "zebra")
        assert set(codes(view)) == {"H03", "H04", "H06"}
        assert {r["task"]["code"]: r["show_owner"] for r in view["rows"]} == {"H03": True, "H04": False, "H06": False}


def test_selected_days_task_appears_once_with_its_next_date(app):
    with app.app_context():
        task = selected(days="0,4", start="2026-11-12")   # Mondays and Fridays, starting tomorrow
        rows = [r for r in logic.search_view(chris(), task["name"])["rows"] if r["task"]["id"] == task["id"]]
        assert [r["scheduled_date"].isoformat() for r in rows] == ["2026-11-13"]


def test_search_page_lists_results_and_ticking_keeps_them(app):
    c = app.test_client(); token = login(c, "chris")
    with app.app_context():
        tid = set_task("H03", owner="chris", frequency="monthly", name="Clean the Gutters")["id"]
        other = set_task("H04", owner="andrea", frequency="monthly", name="Gutter Guards")["id"]
    page = c.get("/?q=gutter").text
    assert 'data-view="search"' in page and 'data-q="gutter"' in page and 'value="gutter"' in page
    panel = page.split("data-task-panel", 1)[1]
    assert f'id="task-{tid}"' in panel and f'id="task-{other}"' in panel
    assert "2 found" in panel and "Andrea’s task" in panel and "I took care of this" in panel
    assert 'aria-current=page' not in page.split('class="period-tabs"', 1)[1].split("</nav>", 1)[0]

    response = c.post(f"/tasks/{tid}/toggle", headers={"Accept": "application/json"},
                      data={"csrf": token, "done": "1", "view": "search", "q": "gutter", "board_user": "chris"})
    result = json.loads(response.text)
    assert result["ok"] and result["done"]
    assert f'id="task-{tid}"' in result["tasks_html"] and f'id="task-{other}"' in result["tasks_html"]
    assert "Undo completion" in result["tasks_html"] and "Results for" in result["tasks_html"]

    empty = c.get("/?q=qqqq").text
    assert "No task by that name." in empty and "/manage/tasks?q=qqqq" in empty
    assert 'data-view="today"' in c.get("/?q=+").text                   # nothing typed is the normal list
    assert 'data-view="today"' in c.get("/?view=search").text


def test_search_is_only_on_your_own_list_and_escapes_what_was_typed(app):
    c = app.test_client(); login(c, "chris")
    theirs = c.get("/people/andrea?q=laundry").text
    assert "data-task-search" not in theirs and 'data-view="today"' in theirs
    page = c.get('/?q="><script>alert(1)</script>').text
    assert "<script>alert(1)" not in page
    admin = app.test_client(); login(admin, "admin")
    assert admin.get("/?q=laundry").status_code == 302
