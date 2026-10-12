"""Dashboard, live monitoring, reports and audit-log viewer."""
import csv
import io
from datetime import date, timedelta

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from academics.models import Semester
from accounts.audit import audit
from accounts.decorators import admin_required
from accounts.models import AuditLog, User
from network.mikrotik import RouterError, format_uptime, get_router
from network.models import Session
from network.services import kick_sessions
from network.sync import sync_health
from instructors.models import MB, SemesterAccount, Instructor


# --------------------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------------------
@admin_required
def dashboard(request):
    today = timezone.localdate()
    week = [today - timedelta(days=i) for i in range(6, -1, -1)]
    usage = {
        r["day"]: r["mb"]
        for r in Session.objects.filter(login_time__date__gte=week[0])
        .annotate(day=TruncDate("login_time"))
        .values("day")
        .annotate(mb=Sum("consumption"))
    }
    chart = {"labels": [d.strftime("%a %b %d") for d in week], "data": [round(usage.get(d, 0) or 0, 1) for d in week]}
    semester = Semester.get_active()
    return render(
        request,
        "adminpanel/dashboard.html",
        {
            "semester": semester,
            "n_instructors": Instructor.objects.count(),
            "n_online": Session.objects.filter(is_active=True).values("instructor").distinct().count(),
            "n_accounts": SemesterAccount.objects.filter(semester=semester, is_active=True).count() if semester else 0,
            "recent_logs": AuditLog.objects.select_related("actor")[:8],
            "chart": chart,
        },
    )


# --------------------------------------------------------------------------------------
# live monitoring (HTMX, every 5 s)
# --------------------------------------------------------------------------------------
def fmt_bps(bps):
    if bps is None:
        return "n/a"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbps"
    return f"{bps / 1000:.0f} kbps"


def fmt_bytes(n):
    n = n or 0
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def live_snapshot():
    """One router round-trip -> everything the monitoring widgets need."""
    snap = {"online": False, "error": None, "rows": [], "resources": None, "traffic": None, "mock": False, "fasttrack": False}
    try:
        with get_router() as r:
            snap["mock"] = r.is_mock
            actives = r.active_sessions()
            snap["resources"] = r.resources()
            snap["traffic"] = r.traffic()
            try:
                snap["fasttrack"] = r.fasttrack_active()
            except RouterError:
                snap["fasttrack"] = False
        snap["online"] = True
    except RouterError as e:
        snap["error"] = str(e)
        actives = []
    users = {
        u.username: u
        for u in User.objects.filter(username__in=[a["user"] for a in actives]).select_related("instructor")
    }
    for a in actives:
        u = users.get(a["user"])
        snap["rows"].append(
            {
                **a,
                "name": u.get_full_name() if u else "(not a Django user)",
                "instructor_id": u.instructor.pk if u and hasattr(u, "instructor") else None,
                "kickable": bool(u and u.is_instructor),
                "uptime": format_uptime(a["uptime_seconds"]),
                "in_h": fmt_bytes(a["bytes_in"]),
                "out_h": fmt_bytes(a["bytes_out"]),
            }
        )
    today = timezone.localdate()
    snap["sync"] = sync_health()
    snap["sessions_today"] = Session.objects.filter(login_time__date=today).count()
    snap["active_users"] = len(actives)
    t = snap["traffic"] or {}
    snap["bandwidth"] = fmt_bps(None if t.get("rx_bps") is None else t["rx_bps"] + t["tx_bps"])
    snap["bandwidth_down"] = fmt_bps(t.get("rx_bps"))
    snap["bandwidth_up"] = fmt_bps(t.get("tx_bps"))
    return snap


@admin_required
def monitoring(request):
    return render(request, "adminpanel/monitoring.html")


@admin_required
def monitoring_live(request):
    snap = live_snapshot()
    template = "adminpanel/partials/live_cards.html" if request.GET.get("view") == "cards" else "adminpanel/partials/live.html"
    return render(request, template, {"snap": snap})


@admin_required
@require_POST
def monitoring_kick(request):
    active_id = request.POST.get("active_id", "")
    username = request.POST.get("user", "")
    flash = None
    try:
        with get_router() as r:
            sessions = list(
                Session.objects.filter(is_active=True, instructor__user__username=username).select_related("instructor__user")
            )
            if sessions:
                kick_sessions(sessions, Session.ADMIN_KICK, router=r)
            else:
                r.kick(active_id, user=username)
        audit(request, "session.kicked", username, {"active_id": active_id})
        flash = ("success", f"{username} was disconnected.")
    except RouterError as e:
        flash = ("danger", f"Could not kick {username}: {e}")
    snap = live_snapshot()
    return render(request, "adminpanel/partials/live.html", {"snap": snap, "flash": flash})


# --------------------------------------------------------------------------------------
# reports
# --------------------------------------------------------------------------------------
def _parse_date(value, default):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return default


def build_report(request):
    today = timezone.localdate()
    start = _parse_date(request.GET.get("start"), today - timedelta(days=29))
    end = _parse_date(request.GET.get("end"), today)
    sessions = Session.objects.filter(login_time__date__gte=start, login_time__date__lte=end)

    def name_of(row):
        u = User(first_name=row["instructor__user__first_name"], last_name=row["instructor__user__last_name"],
                 middle_name=row["instructor__user__middle_name"], extension_name=row["instructor__user__extension_name"])
        return u.get_full_name()

    per_instructor = []
    for r in (
        sessions.values(
            "instructor_id", "instructor__user__username",
            "instructor__user__first_name", "instructor__user__last_name", "instructor__user__middle_name", "instructor__user__extension_name",
        )
        .annotate(n=Count("id"), mb=Sum("consumption"))
        .order_by("-mb")
    ):
        per_instructor.append(
            {"username": r["instructor__user__username"], "name": name_of(r),
             "sessions": r["n"], "mb": round(r["mb"] or 0, 2)}
        )
    daily_map = {
        r["day"]: (r["n"], r["mb"])
        for r in sessions.annotate(day=TruncDate("login_time")).values("day").annotate(n=Count("id"), mb=Sum("consumption"))
    }
    days = (end - start).days + 1
    daily = []
    for i in range(min(days, 366)):
        d = start + timedelta(days=i)
        n, mb = daily_map.get(d, (0, 0))
        daily.append({"day": d, "sessions": n, "mb": round(mb or 0, 2)})
    log = sessions.select_related("instructor__user").order_by("-login_time")
    total_mb = round(sum(s["mb"] for s in per_instructor), 2)
    return {
        "start": start, "end": end,
        "per_instructor": per_instructor, "daily": daily,
        "top": per_instructor[:10], "log": log, "total_mb": total_mb,
        "total_sessions": sessions.count(),
        "chart": {"labels": [d["day"].strftime("%b %d") for d in daily], "data": [d["mb"] for d in daily]},
        "top_chart": {"labels": [s["name"] for s in per_instructor[:10]], "data": [s["mb"] for s in per_instructor[:10]]},
    }


@admin_required
def reports(request):
    ctx = build_report(request)
    ctx["log"] = ctx["log"][:500]
    ctx["query"] = request.GET.urlencode()
    return render(request, "adminpanel/reports.html", ctx)


@admin_required
def report_csv(request):
    data = build_report(request)
    kind = request.GET.get("type", "instructors")
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="cdk_usage_{kind}_{data["start"]}_{data["end"]}.csv"'
    w = csv.writer(resp)
    if kind == "daily":
        w.writerow(["date", "sessions", "usage_mb"])
        for r in data["daily"]:
            w.writerow([r["day"], r["sessions"], r["mb"]])
    elif kind == "sessions":
        w.writerow(["username", "name", "login_time", "logout_time", "ip", "mac", "usage_mb", "logout_reason"])
        for s in data["log"]:
            w.writerow([
                s.instructor.user.username, s.instructor.user.get_full_name(),
                timezone.localtime(s.login_time).strftime("%Y-%m-%d %H:%M:%S"),
                timezone.localtime(s.logout_time).strftime("%Y-%m-%d %H:%M:%S") if s.logout_time else "",
                s.ip_address or "", s.mac_address, round(s.consumption, 2), s.logout_reason,
            ])
    else:
        w.writerow(["username", "name", "sessions", "usage_mb"])
        for r in data["per_instructor"]:
            w.writerow([r["username"], r["name"], r["sessions"], r["mb"]])
    audit(request, "report.exported", kind, {"format": "csv"})
    return resp


@admin_required
def report_pdf(request):
    from xhtml2pdf import pisa

    data = build_report(request)
    data["log"] = list(data["log"][:300])
    data["generated"] = timezone.localtime()
    data["semester"] = Semester.get_active()
    html = render_to_string("adminpanel/report_pdf.html", data)
    buf = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buf)
    if result.err:
        messages.error(request, "Could not generate the PDF.")
        return render(request, "adminpanel/reports.html", data)
    audit(request, "report.exported", "usage", {"format": "pdf"})
    resp = HttpResponse(buf.getvalue(), content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="cdk_usage_report_{data["start"]}_{data["end"]}.pdf"'
    return resp


# --------------------------------------------------------------------------------------
# audit logs
# --------------------------------------------------------------------------------------
@admin_required
def audit_logs(request):
    qs = AuditLog.objects.select_related("actor")
    f = {k: request.GET.get(k, "").strip() for k in ("actor", "action", "start", "end", "q")}
    if f["actor"]:
        qs = qs.filter(actor__username__icontains=f["actor"])
    if f["action"]:
        qs = qs.filter(action=f["action"])
    if f["start"]:
        qs = qs.filter(created_at__date__gte=_parse_date(f["start"], date.min))
    if f["end"]:
        qs = qs.filter(created_at__date__lte=_parse_date(f["end"], date.max))
    if f["q"]:
        qs = qs.filter(Q(target__icontains=f["q"]) | Q(action__icontains=f["q"]))
    return render(
        request,
        "adminpanel/logs.html",
        {
            "logs": qs[:2000],
            "filters": f,
            "actions": AuditLog.objects.order_by("action").values_list("action", flat=True).distinct(),
            "capped": qs.count() > 2000,
        },
    )
