import csv
import io
import time
from datetime import timedelta

from django.contrib import messages
from django.db.models import Exists, OuterRef, Prefetch, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from academics.models import BandwidthPolicy, Semester
from accounts.audit import audit
from accounts.decorators import admin_required
from accounts.models import User
from network.mikrotik import RouterError, get_router
from network.models import Session
from network.services import enforce_user_off, refresh_user_on_router
from instructors import approval, masterlist
from instructors.masterlist import MasterlistError, generate_password
from instructors.models import SemesterAccount, Instructor

from .forms import MasterlistUploadForm, ResetPasswordForm, InstructorForm

CREDENTIALS_TTL = 600  # seconds the credentials sheet stays available in the admin's session


def _warn(request, text):
    if text:
        messages.warning(request, text)


# --------------------------------------------------------------------------------------
# list / detail
# --------------------------------------------------------------------------------------
@admin_required
def instructor_list(request):
    qs = (
        Instructor.objects.select_related("user")
        .prefetch_related(
            Prefetch(
                "accounts",
                queryset=SemesterAccount.objects.filter(semester__is_active=True).select_related("policy"),
                to_attr="current_accounts",
            )
        )
        .annotate(online=Exists(Session.objects.filter(instructor=OuterRef("pk"), is_active=True)))
    )
    f = {
        "status": request.GET.get("status", ""),
        "registration": request.GET.get("registration", ""),
    }
    if f["status"] in ("active", "inactive"):
        qs = qs.filter(user__is_active=f["status"] == "active")
    if f["registration"] in (Instructor.PENDING, Instructor.APPROVED, Instructor.REJECTED):
        qs = qs.filter(approval_status=f["registration"])
    return render(
        request,
        "adminpanel/instructors/list.html",
        {
            "instructors": qs,
            "filters": f,
            "policies": BandwidthPolicy.objects.all(),
            "semester": Semester.get_active(),
            "default_policy": BandwidthPolicy.get_default(),
        },
    )


@admin_required
def instructor_detail(request, pk):
    instructor = get_object_or_404(Instructor.objects.select_related("user"), pk=pk)
    accounts = instructor.accounts.select_related("semester__sy", "policy")
    sessions = instructor.sessions.select_related("semester_account__semester")
    current = sessions.filter(is_active=True).first()
    daily = (
        sessions.filter(login_time__gte=timezone.now() - timedelta(days=30))
        .annotate(day=TruncDate("login_time"))
        .values("day")
        .annotate(mb=Sum("consumption"))
        .order_by("day")
    )
    chart = {"labels": [d["day"].strftime("%b %d") for d in daily], "data": [round(d["mb"] or 0, 2) for d in daily]}
    return render(
        request,
        "adminpanel/instructors/detail.html",
        {
            "instructor": instructor,
            "accounts": accounts,
            "current_account": instructor.current_account,
            "current_session": current,
            "history": sessions.filter(is_active=False)[:50],
            "chart": chart,
            "policies": BandwidthPolicy.objects.all(),
            "default_policy": BandwidthPolicy.get_default(),
            "reset_form": ResetPasswordForm(),
        },
    )


# --------------------------------------------------------------------------------------
# add / edit
# --------------------------------------------------------------------------------------
@admin_required
def instructor_add(request):
    form = InstructorForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        instructor, password = form.save()
        audit(request, "instructor.created", instructor.user.username)
        messages.success(
            request,
            f"Instructor {instructor.user.get_full_name()} added. Temporary password: {password} "
            "(shown only now; the instructor must change it at first login).",
        )
        if not Semester.get_active():
            messages.warning(request, "There is no active semester, so no internet account was created yet.")
        return redirect("adminpanel:instructor_detail", pk=instructor.pk)
    return render(request, "adminpanel/instructors/form.html", {"form": form, "title": "Add instructor"})


@admin_required
def instructor_edit(request, pk):
    instructor = get_object_or_404(Instructor.objects.select_related("user"), pk=pk)
    was_off = not instructor.user.is_active
    form = InstructorForm(request.POST or None, instructor=instructor)
    if request.method == "POST" and form.is_valid():
        instructor, _ = form.save()
        audit(request, "instructor.updated", instructor.user.username, {"fields": sorted(form.changed_data)})
        now_off = not instructor.user.is_active
        if now_off and not was_off:
            _warn(request, enforce_user_off(instructor.user))
        messages.success(request, "Instructor saved.")
        return redirect("adminpanel:instructor_detail", pk=instructor.pk)
    return render(
        request, "adminpanel/instructors/form.html", {"form": form, "title": f"Edit {instructor.user.get_full_name()}", "instructor": instructor}
    )


# --------------------------------------------------------------------------------------
# single-instructor actions
# --------------------------------------------------------------------------------------
@admin_required
@require_POST
def instructor_toggle_active(request, pk):
    instructor = get_object_or_404(Instructor.objects.select_related("user"), pk=pk)
    user = instructor.user
    user.is_active = not user.is_active
    user.save(update_fields=["is_active", "updated_at"])
    if user.is_active:
        audit(request, "instructor.activated", instructor.user.username)
        messages.success(request, f"{user.get_full_name()} activated.")
    else:
        audit(request, "instructor.deactivated", instructor.user.username)
        messages.success(request, f"{user.get_full_name()} deactivated and disconnected.")
        _warn(request, enforce_user_off(user))
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect("adminpanel:instructor_detail", pk=pk)


@admin_required
@require_POST
def instructor_reset_password(request, pk):
    instructor = get_object_or_404(Instructor.objects.select_related("user"), pk=pk)
    form = ResetPasswordForm(request.POST)
    if form.is_valid():
        password = form.cleaned_data["password"] or generate_password()
        instructor.user.set_password(password)
        instructor.user.must_change_password = True
        instructor.user.save(update_fields=["password", "must_change_password", "updated_at"])
        audit(request, "instructor.password_reset", instructor.user.username)
        messages.success(
            request,
            f"Temporary password for {instructor.user.username}: {password} — shown only now. "
            "The instructor must change it at next login.",
        )
    return redirect("adminpanel:instructor_detail", pk=pk)


def assign_policy(instructors, policy):
    """Set the policy on each instructor's account for the active semester (creating it if needed)."""
    semester = Semester.get_active()
    if semester is None:
        raise MasterlistError("There is no active semester.")
    n = 0
    for s in instructors:
        acct, made = SemesterAccount.objects.get_or_create(instructor=s, semester=semester, defaults={"policy": policy})
        if not made and acct.policy_id != policy.id:
            acct.policy = policy
            acct.save(update_fields=["policy"])
        n += 1
    return n


@admin_required
@require_POST
def instructor_assign_policy(request, pk):
    instructor = get_object_or_404(Instructor, pk=pk)
    policy = get_object_or_404(BandwidthPolicy, pk=request.POST.get("policy"))
    try:
        assign_policy([instructor], policy)
    except MasterlistError as e:
        messages.error(request, str(e))
    else:
        audit(request, "instructor.policy_assigned", instructor.user.username, {"policy": policy.name})
        messages.success(request, f"Policy set to {policy.name}. It applies the next time the instructor connects.")
    return redirect("adminpanel:instructor_detail", pk=pk)


@admin_required
@require_POST
def account_toggle(request, pk):
    acct = get_object_or_404(SemesterAccount.objects.select_related("instructor__user", "semester"), pk=pk)
    acct.is_active = not acct.is_active
    acct.save(update_fields=["is_active"])
    audit(request, "account.enabled" if acct.is_active else "account.disabled", acct.instructor.user.username, {"semester": str(acct.semester)})
    if acct.is_active:
        messages.success(request, "Semester account enabled.")
    else:
        messages.success(request, "Semester account disabled.")
        _warn(request, enforce_user_off(acct.instructor.user))
    return redirect("adminpanel:instructor_detail", pk=acct.instructor_id)


@admin_required
@require_POST
def instructor_push_router(request, pk):
    instructor = get_object_or_404(Instructor.objects.select_related("user"), pk=pk)
    err = refresh_user_on_router(instructor.user)
    if err:
        messages.error(request, err)
    else:
        messages.success(request, "Router updated for this instructor.")
    return redirect("adminpanel:instructor_detail", pk=pk)


# --------------------------------------------------------------------------------------
# registration approval
# --------------------------------------------------------------------------------------
@admin_required
@require_POST
def instructor_approve(request, pk):
    instructor = get_object_or_404(Instructor.objects.select_related("user"), pk=pk)
    policy = BandwidthPolicy.objects.filter(pk=request.POST.get("policy")).first() or BandwidthPolicy.get_default()
    account = approval.approve(instructor, request.user, policy)
    audit(request, "instructor.approved", instructor.user.username, {"policy": policy.name if policy else None})
    messages.success(request, f"{instructor.user.get_full_name()} approved.")
    if account is None:
        messages.warning(
            request,
            "No internet account was created because there is no active semester or no bandwidth policy. "
            "Activate a semester, then assign a policy on the instructor's page.",
        )
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect("adminpanel:instructor_detail", pk=pk)


@admin_required
@require_POST
def instructor_reject(request, pk):
    instructor = get_object_or_404(Instructor.objects.select_related("user"), pk=pk)
    if instructor.approval_status != Instructor.PENDING:
        messages.error(request, "Only a pending registration can be rejected. Use Deactivate to stop an existing account.")
        return redirect("adminpanel:instructor_detail", pk=pk)
    approval.reject(instructor, request.user)
    audit(request, "instructor.rejected", instructor.user.username)
    messages.success(request, f"Registration of {instructor.user.get_full_name()} rejected.")
    return redirect("adminpanel:instructor_detail", pk=pk)


# --------------------------------------------------------------------------------------
# bulk actions
# --------------------------------------------------------------------------------------
@admin_required
@require_POST
def instructor_bulk(request):
    ids = request.POST.getlist("ids")
    action = request.POST.get("action", "")
    instructors = list(Instructor.objects.filter(pk__in=ids).select_related("user"))
    if not instructors:
        messages.error(request, "Select at least one instructor first.")
        return redirect("adminpanel:instructor_list")
    n = len(instructors)
    if action == "disable":
        for s in instructors:
            s.user.is_active = False
            s.user.save(update_fields=["is_active", "updated_at"])
        try:
            with get_router() as r:
                for s in instructors:
                    enforce_user_off(s.user, router=r)
        except RouterError as e:
            messages.warning(request, f"Saved, but the router could not be updated now ({e}). Run sync_router later.")
        audit(request, "instructor.bulk_disable", f"{n} instructors", {"ids": [s.pk for s in instructors]})
        messages.success(request, f"{n} instructor(s) disabled and disconnected.")
    elif action == "enable":
        User.objects.filter(instructor__in=instructors).update(is_active=True)
        audit(request, "instructor.bulk_enable", f"{n} instructors", {"ids": [s.pk for s in instructors]})
        messages.success(request, f"{n} instructor(s) enabled.")
    elif action == "approve":
        done = 0
        for s in instructors:
            if s.approval_status != Instructor.APPROVED:
                approval.approve(s, request.user)
                done += 1
        audit(request, "instructor.bulk_approve", f"{done} registrations", {"ids": [s.pk for s in instructors]})
        messages.success(request, f"{done} registration(s) approved with the default policy.")
        if done and not Semester.get_active():
            messages.warning(request, "There is no active semester, so no internet accounts were created yet.")
    elif action == "reject":
        done = 0
        for s in instructors:
            if s.approval_status == Instructor.PENDING:
                approval.reject(s, request.user)
                done += 1
        audit(request, "instructor.bulk_reject", f"{done} registrations", {"ids": [s.pk for s in instructors]})
        messages.success(request, f"{done} pending registration(s) rejected.")
    elif action == "policy":
        policy = BandwidthPolicy.objects.filter(pk=request.POST.get("policy")).first()
        if policy is None:
            messages.error(request, "Choose a bandwidth policy.")
        else:
            try:
                assign_policy(instructors, policy)
            except MasterlistError as e:
                messages.error(request, str(e))
            else:
                audit(request, "instructor.bulk_policy", f"{n} instructors", {"policy": policy.name, "ids": [s.pk for s in instructors]})
                messages.success(request, f"Policy {policy.name} assigned to {n} instructor(s).")
    else:
        messages.error(request, "Choose an action.")
    return redirect("adminpanel:instructor_list")


# --------------------------------------------------------------------------------------
# masterlist import
# --------------------------------------------------------------------------------------
@admin_required
def masterlist_upload(request):
    form = MasterlistUploadForm(request.POST or None, request.FILES or None)
    semester = Semester.get_active()
    if request.method == "POST" and form.is_valid():
        try:
            parsed = masterlist.parse_upload(form.cleaned_data["file"])
            results = masterlist.validate_rows(parsed)
        except MasterlistError as e:
            messages.error(request, str(e))
        else:
            request.session["masterlist"] = {
                "filename": form.cleaned_data["file"].name,
                "rows": [r.as_dict() for r in results],
            }
            return redirect("adminpanel:masterlist_preview")
    return render(request, "adminpanel/instructors/import_upload.html", {"form": form, "semester": semester, "columns": masterlist.COLUMNS})


@admin_required
def masterlist_preview(request):
    pending = request.session.get("masterlist")
    if not pending:
        messages.info(request, "Upload a masterlist first.")
        return redirect("adminpanel:masterlist_upload")
    rows = pending["rows"]
    counts = {
        "new": sum(r["status"] == "new" for r in rows),
        "existing": sum(r["status"] == "existing" for r in rows),
        "error": sum(r["status"] == "error" for r in rows),
    }
    return render(
        request,
        "adminpanel/instructors/import_preview.html",
        {
            "rows": rows,
            "filename": pending["filename"],
            "counts": counts,
            "importable": counts["new"] + counts["existing"],
            "semester": Semester.get_active(),
            "policy": BandwidthPolicy.get_default(),
        },
    )


@admin_required
@require_POST
def masterlist_confirm(request):
    pending = request.session.get("masterlist")
    if not pending:
        return redirect("adminpanel:masterlist_upload")
    rows = [r["data"] for r in pending["rows"] if r["status"] in ("new", "existing")]
    try:
        summary = masterlist.import_rows(rows)
    except MasterlistError as e:
        messages.error(request, str(e))
        return redirect("adminpanel:masterlist_preview")
    skipped = sum(r["status"] == "error" for r in pending["rows"])
    del request.session["masterlist"]
    if summary.credentials:
        request.session["credentials_sheet"] = {"rows": summary.credentials, "created": time.time()}
    audit(
        request,
        "masterlist.imported",
        pending["filename"],
        {"created": summary.created, "updated": summary.updated, "accounts_created": summary.accounts_created, "skipped": skipped},
    )
    return render(
        request,
        "adminpanel/instructors/import_done.html",
        {"summary": summary, "skipped": skipped, "has_credentials": bool(summary.credentials), "ttl_minutes": CREDENTIALS_TTL // 60},
    )


@admin_required
def masterlist_cancel(request):
    request.session.pop("masterlist", None)
    return redirect("adminpanel:masterlist_upload")


@admin_required
def credentials_sheet(request):
    sheet = request.session.get("credentials_sheet")
    if not sheet or time.time() - sheet["created"] > CREDENTIALS_TTL:
        request.session.pop("credentials_sheet", None)
        messages.error(request, "The credentials sheet has expired. Reset the instructors' passwords individually if needed.")
        return redirect("adminpanel:instructor_list")
    rows = sheet["rows"]
    fields = ["username", "name", "password"]
    fmt = request.GET.get("format", "csv")
    audit(request, "masterlist.credentials_downloaded", f"{len(rows)} instructors", {"format": fmt})
    if fmt == "xlsx":
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Credentials"
        ws.append(fields)
        for r in rows:
            ws.append([r[f] for f in fields])
        buf = io.BytesIO()
        wb.save(buf)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = 'attachment; filename="instructor_credentials.xlsx"'
        return resp
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="instructor_credentials.csv"'
    w = csv.writer(resp)
    w.writerow(fields)
    for r in rows:
        w.writerow([r[f] for f in fields])
    return resp


@admin_required
@require_POST
def credentials_clear(request):
    request.session.pop("credentials_sheet", None)
    messages.success(request, "Credentials sheet cleared from this session.")
    return redirect("adminpanel:instructor_list")


@admin_required
def masterlist_template(request):
    fmt = request.GET.get("format", "csv")
    rows = masterlist.template_rows()
    if fmt == "xlsx":
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Masterlist"
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = 'attachment; filename="masterlist_template.xlsx"'
        return resp
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="masterlist_template.csv"'
    csv.writer(resp).writerows(rows)
    return resp
