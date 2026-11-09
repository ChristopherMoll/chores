"""Task management (the CMS): deal tasks, edit cards, import/export.

Open to every logged-in user. Technical settings live in admin.py instead."""
import csv
import io
from datetime import date, datetime

from flask import (Blueprint, Response, abort, flash, g, redirect, render_template, request,
                   url_for)

from . import logic, periods
from .auth import login_required
from .db import get_db

bp = Blueprint("manage", __name__, url_prefix="/manage")

GROUPS = ["Home", "Out", "Caregiving", "Magic", "Wild"]
DUE_FIELDS = ("due_date", "due_weekday", "due_monthday", "due_time")
FIELDS = ("code", "grp", "name", "description", "frequency", "owner", "hours_month", "notes", "active") + DUE_FIELDS


def _parse_hours(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        raise ValueError(f"'{raw}' isn't a number of hours.")
    if v < 0 or v > 744:
        raise ValueError("Hours per month must be between 0 and 744.")
    return round(v, 2)


def _valid_owner(v):
    return v in dict(logic.owner_choices())


def parse_due(freq, due_date="", due_weekday="", due_monthday="", due_time=""):
    """Validate due settings, keeping only the one that applies to this frequency."""
    out = dict.fromkeys(DUE_FIELDS)
    try:
        if freq == "once" and due_date:
            out["due_date"] = date.fromisoformat(due_date).isoformat()
        elif freq == "weekly" and due_weekday != "":
            out["due_weekday"] = int(due_weekday)
            if not 0 <= out["due_weekday"] <= 6:
                raise ValueError
        elif freq == "monthly" and due_monthday != "":
            out["due_monthday"] = int(due_monthday)
            if not 1 <= out["due_monthday"] <= 31:
                raise ValueError
        elif freq == "daily" and due_time:
            out["due_time"] = datetime.strptime(due_time, "%H:%M").strftime("%H:%M")
    except ValueError:
        raise ValueError(f"That due setting doesn't look right for a {freq} task.")
    return out


# ------------------------------------------------------------------ tasks ---

@bp.get("/")
@login_required
def index():
    return redirect(url_for("manage.tasks"))


@bp.get("/tasks")
@login_required
def tasks():
    f = {k: request.args.get(k, "") for k in ("grp", "frequency", "owner", "active", "q")}
    sql, args = "SELECT * FROM tasks WHERE 1=1", []
    if f["grp"]:
        sql += " AND grp = ?"; args.append(f["grp"])
    if f["frequency"]:
        sql += " AND frequency = ?"; args.append(f["frequency"])
    if f["owner"]:
        sql += " AND owner = ?"; args.append(f["owner"])
    if f["active"] in ("0", "1"):
        sql += " AND active = ?"; args.append(int(f["active"]))
    if f["q"]:
        sql += " AND (name LIKE ? OR description LIKE ? OR code LIKE ?)"; args += [f"%{f['q']}%"] * 3
    rows = get_db().execute(sql + " ORDER BY CASE grp WHEN 'Home' THEN 1 WHEN 'Out' THEN 2"
                            " WHEN 'Caregiving' THEN 3 WHEN 'Magic' THEN 4 ELSE 5 END, code", args).fetchall()
    summary = get_db().execute(
        "SELECT owner, COUNT(*) AS n, COALESCE(SUM(CASE WHEN frequency != 'once' THEN hours_month END), 0) AS h"
        " FROM tasks WHERE active = 1 GROUP BY owner"
    ).fetchall()
    return render_template("manage/tasks.html", rows=rows, f=f, groups=GROUPS,
                           owners=logic.owner_choices(), freqs=periods.FREQUENCIES,
                           summary={r["owner"]: r for r in summary})


@bp.post("/tasks/bulk")
@login_required
def tasks_bulk():
    """Save the deal grid: owner, frequency, hours and active for every row shown."""
    db = get_db()
    ids = request.form.getlist("id", type=int)
    errors, changed = [], 0
    for tid in ids:
        owner = request.form.get(f"owner_{tid}")
        freq = request.form.get(f"frequency_{tid}")
        active = 1 if request.form.get(f"active_{tid}") else 0
        try:
            hours = _parse_hours(request.form.get(f"hours_{tid}"))
        except ValueError as e:
            errors.append(f"Row {tid}: {e}")
            continue
        if not _valid_owner(owner) or freq not in periods.FREQUENCIES:
            errors.append(f"Row {tid}: bad owner or frequency.")
            continue
        cur = db.execute(
            "UPDATE tasks SET owner = ?, frequency = ?, hours_month = ?, active = ?, updated_at = datetime('now'),"
            " due_date = CASE WHEN ? = 'once' THEN due_date END,"          # due settings for any other
            " due_weekday = CASE WHEN ? = 'weekly' THEN due_weekday END,"  # frequency no longer apply
            " due_monthday = CASE WHEN ? = 'monthly' THEN due_monthday END,"
            " due_time = CASE WHEN ? = 'daily' THEN due_time END"
            " WHERE id = ? AND (owner IS NOT ? OR frequency IS NOT ? OR hours_month IS NOT ? OR active IS NOT ?)",
            (owner, freq, hours, active, freq, freq, freq, freq, tid, owner, freq, hours, active))
        changed += cur.rowcount
    db.commit()
    for e in errors:
        flash(e, "error")
    flash(f"Saved. {changed} task{'s' if changed != 1 else ''} changed.", "ok")
    return redirect(request.referrer or url_for("manage.tasks"))


def _task_form(task=None):
    form = request.form
    data = {k: form.get(k, "").strip() for k in ("code", "grp", "name", "description", "frequency", "owner", "notes")}
    data["active"] = 1 if form.get("active") else 0
    errs = []
    try:
        data["hours_month"] = _parse_hours(form.get("hours_month"))
    except ValueError as e:
        errs.append(str(e))
    if not data["code"] or not data["name"]:
        errs.append("Code and name are required.")
    if data["grp"] not in GROUPS:
        errs.append("Pick a group.")
    data.update(dict.fromkeys(DUE_FIELDS))
    if data["frequency"] not in periods.FREQUENCIES:
        errs.append("Pick a frequency.")
    else:
        try:
            data.update(parse_due(data["frequency"], *(form.get(k, "").strip() for k in DUE_FIELDS)))
        except ValueError as e:
            errs.append(str(e))
    if not _valid_owner(data["owner"]):
        errs.append("Pick an owner.")
    dupe = get_db().execute("SELECT id FROM tasks WHERE code = ?", (data["code"],)).fetchone()
    if dupe and (task is None or dupe["id"] != task["id"]):
        errs.append(f"Code {data['code']} is already used.")
    return data, errs


def _next_code(grp):
    letter = grp[0]
    rows = get_db().execute("SELECT code FROM tasks WHERE code LIKE ?", (f"{letter}%",)).fetchall()
    nums = [int(r["code"][1:]) for r in rows if r["code"][1:].isdigit()]
    return f"{letter}{(max(nums) + 1 if nums else 1):02d}"


@bp.route("/tasks/new", methods=["GET", "POST"])
@login_required
def task_new():
    if request.method == "POST":
        data, errs = _task_form()
        if not errs:
            db = get_db()
            db.execute(f"INSERT INTO tasks({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})",
                       [data[k] for k in FIELDS])
            db.commit()
            flash(f"Added {data['name']}.", "ok")
            return redirect(url_for("manage.tasks"))
        for e in errs:
            flash(e, "error")
        task = data
    else:
        grp = request.args.get("grp", "Home")
        task = {"code": _next_code(grp if grp in GROUPS else "Home"), "grp": grp, "name": "", "description": "",
                "frequency": "weekly", "owner": logic.VOLUNTEER, "hours_month": None, "notes": "", "active": 1,
                **dict.fromkeys(DUE_FIELDS)}
    return render_template("manage/task_form.html", task=task, new=True, groups=GROUPS, weekdays=periods.WEEKDAYS,
                           owners=logic.owner_choices(), freqs=periods.FREQUENCIES)


@bp.route("/tasks/<int:task_id>", methods=["GET", "POST"])
@login_required
def task_edit(task_id):
    db = get_db()
    task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        abort(404)
    if request.method == "POST":
        data, errs = _task_form(task)
        if not errs:
            db.execute(f"UPDATE tasks SET {', '.join(k + ' = ?' for k in FIELDS)}, updated_at = datetime('now')"
                       " WHERE id = ?", [data[k] for k in FIELDS] + [task_id])
            db.commit()
            flash(f"Saved {data['name']}.", "ok")
            return redirect(url_for("manage.tasks"))
        for e in errs:
            flash(e, "error")
        task = dict(data, id=task_id)
    n = db.execute("SELECT COUNT(*) FROM completions WHERE task_id = ?", (task_id,)).fetchone()[0]
    return render_template("manage/task_form.html", task=task, new=False, groups=GROUPS, completions=n,
                           weekdays=periods.WEEKDAYS, owners=logic.owner_choices(), freqs=periods.FREQUENCIES)


@bp.post("/tasks/<int:task_id>/delete")
@login_required
def task_delete(task_id):
    db = get_db()
    t = db.execute("SELECT name FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if t:
        db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        db.commit()
        flash(f"Deleted {t['name']} and its history.", "ok")
    return redirect(url_for("manage.tasks"))


# --------------------------------------------------------- import/export ---

@bp.get("/export.csv")
@login_required
def export_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Code", "Group", "Task", "What it covers", "Frequency", "Owner", "Hours / Month", "Notes", "Active",
                "Due Date", "Due Weekday", "Due Day", "Due Time"])
    for t in get_db().execute("SELECT * FROM tasks ORDER BY grp, code"):
        w.writerow([t["code"], t["grp"], t["name"], t["description"], t["frequency"],
                    logic.owner_label(t["owner"]), t["hours_month"] if t["hours_month"] is not None else "",
                    t["notes"], "yes" if t["active"] else "no",
                    t["due_date"] or "", periods.WEEKDAYS[t["due_weekday"]] if t["due_weekday"] is not None else "",
                    t["due_monthday"] or "", t["due_time"] or ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=chores-tasks.csv"})


def _import_due(task, row, new_freq):
    """Due columns from one import row, as field updates. Nothing changes if they're blank."""
    cells = {k: row.get(c, "") for k, c in zip(DUE_FIELDS, ("due date", "due weekday", "due day", "due time"))}
    if not new_freq and not any(cells.values()):
        return {}
    if cells["due_weekday"]:
        names = [w.lower() for w in periods.WEEKDAYS]
        short = [w[:3] for w in names]
        given = cells["due_weekday"].lower()
        if given not in names + short:
            raise ValueError(f"unknown weekday '{cells['due_weekday']}'.")
        cells["due_weekday"] = (names.index(given) if given in names else short.index(given))
    merged = {k: (cells[k] if cells[k] != "" else ("" if task[k] is None else task[k])) for k in DUE_FIELDS}
    merged = {k: str(v) for k, v in merged.items()}
    merged["due_date"] = merged["due_date"][:10]     # spreadsheets turn dates into timestamps
    merged["due_time"] = merged["due_time"][:5]
    return parse_due(new_freq or task["frequency"], **merged)


def apply_import(rows):
    """Update tasks by Code from spreadsheet rows (dicts keyed by header). Returns (updated, problems).

    Recognised columns: Code (required), Owner, Hours / Month, Notes, Frequency, Active,
    Due Date (YYYY-MM-DD), Due Weekday (Monday...), Due Day (1-31), Due Time (HH:MM).
    Blank cells leave the existing value alone. Unknown codes are reported, not created.
    """
    labels = {label.lower(): value for value, label in logic.owner_choices()}
    labels.update({"needs volunteer": logic.VOLUNTEER, "volunteer": logic.VOLUNTEER, "shared": logic.SHARED})
    db, updated, problems = get_db(), 0, []
    for i, raw in enumerate(rows, start=2):
        row = {str(k).strip().lower(): ("" if v is None else str(v).strip()) for k, v in raw.items() if k}
        code = row.get("code", "").upper()
        if not code:
            continue
        task = db.execute("SELECT * FROM tasks WHERE code = ?", (code,)).fetchone()
        if not task:
            problems.append(f"Row {i}: no task with code {code}.")
            continue
        sets = {}
        if row.get("owner"):
            o = labels.get(row["owner"].lower())
            if o:
                sets["owner"] = o
            else:
                problems.append(f"Row {i} ({code}): unknown owner '{row['owner']}'.")
        h = row.get("hours / month", row.get("hours", ""))
        if h:
            try:
                sets["hours_month"] = _parse_hours(h)
            except ValueError as e:
                problems.append(f"Row {i} ({code}): {e}")
        if row.get("notes"):
            sets["notes"] = row["notes"]
        if row.get("frequency", "").lower() in periods.FREQUENCIES:
            sets["frequency"] = row["frequency"].lower()
        if row.get("active", "").lower() in ("yes", "no", "1", "0", "true", "false"):
            sets["active"] = 1 if row["active"].lower() in ("yes", "1", "true") else 0
        try:
            sets.update(_import_due(task, row, sets.get("frequency")))
        except ValueError as e:
            problems.append(f"Row {i} ({code}): {e}")
        if sets:
            db.execute(f"UPDATE tasks SET {', '.join(k + ' = ?' for k in sets)}, updated_at = datetime('now')"
                       " WHERE id = ?", list(sets.values()) + [task["id"]])
            updated += 1
    db.commit()
    return updated, problems


def read_upload(storage):
    name = (storage.filename or "").lower()
    data = storage.read()
    if name.endswith(".csv"):
        return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb["Tasks"] if "Tasks" in wb.sheetnames else wb.active
        it = ws.iter_rows(values_only=True)
        header = [str(h) if h is not None else "" for h in next(it)]
        return [dict(zip(header, r)) for r in it]
    raise ValueError("Upload a .xlsx or .csv file.")


@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_tasks():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a file first.", "error")
        else:
            try:
                updated, problems = apply_import(read_upload(f))
                flash(f"Updated {updated} tasks.", "ok")
                for p in problems[:20]:
                    flash(p, "error")
                return redirect(url_for("manage.tasks"))
            except Exception as e:   # bad file of any kind: report, don't crash
                flash(f"Couldn't read that file: {e}", "error")
    return render_template("manage/import.html")
