from django.contrib import messages
from django.db.models import Count, ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from academics.models import BandwidthPolicy, SchoolYear, Semester
from accounts.audit import audit
from accounts.decorators import admin_required
from network.mikrotik import RouterError, get_router
from network.services import set_active_semester, sync_policy
from instructors.models import SemesterAccount

from .forms import PolicyForm, SchoolYearForm, SemesterForm


def _form_page(request, form_class, instance, title, back, action, label, extra_template="adminpanel/simple_form.html"):
    form = form_class(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        audit(request, f"{action}.{'updated' if instance else 'created'}", str(obj))
        messages.success(request, f"{label} saved.")
        return obj, None
    return None, render(request, extra_template, {"form": form, "title": title, "back": back})


# --------------------------------------------------------------------------------------
# school years & semesters
# --------------------------------------------------------------------------------------
@admin_required
def semester_list(request):
    from network.models import Session

    active = Semester.get_active()
    impact = (
        {
            "accounts": SemesterAccount.objects.filter(semester=active, is_active=True).count(),
            "sessions": Session.objects.filter(is_active=True, semester_account__semester=active).count(),
        }
        if active
        else None
    )
    return render(
        request,
        "adminpanel/academics/semesters.html",
        {
            "impact": impact,
            "school_years": SchoolYear.objects.annotate(n=Count("semesters")),
            "semesters": Semester.objects.select_related("sy").annotate(n_accounts=Count("accounts")),
            "active": active,
        },
    )


@admin_required
def sy_form(request, pk=None):
    instance = get_object_or_404(SchoolYear, pk=pk) if pk else None
    obj, resp = _form_page(request, SchoolYearForm, instance, "Edit school year" if pk else "Add school year", "adminpanel:semester_list", "school_year", "School year")
    return redirect("adminpanel:semester_list") if obj else resp


@admin_required
@require_POST
def sy_delete(request, pk):
    sy = get_object_or_404(SchoolYear, pk=pk)
    try:
        sy.delete()
    except ProtectedError:
        messages.error(request, "This school year still has semesters. Delete those first.")
    else:
        audit(request, "school_year.deleted", str(sy))
        messages.success(request, "School year deleted.")
    return redirect("adminpanel:semester_list")


@admin_required
def semester_form(request, pk=None):
    instance = get_object_or_404(Semester, pk=pk) if pk else None
    obj, resp = _form_page(request, SemesterForm, instance, "Edit semester" if pk else "Add semester", "adminpanel:semester_list", "semester", "Semester")
    return redirect("adminpanel:semester_list") if obj else resp


@admin_required
@require_POST
def semester_delete(request, pk):
    sem = get_object_or_404(Semester, pk=pk)
    if sem.is_active:
        messages.error(request, "The active semester cannot be deleted. Activate another semester first.")
    elif sem.accounts.exists():
        messages.error(request, "This semester has instructor accounts and usage history, so it cannot be deleted.")
    else:
        sem.delete()
        audit(request, "semester.deleted", str(sem))
        messages.success(request, "Semester deleted.")
    return redirect("adminpanel:semester_list")


@admin_required
def semester_activate(request, pk):
    """GET shows the confirmation page (with impact numbers); POST performs the switch."""
    sem = get_object_or_404(Semester.objects.select_related("sy"), pk=pk)
    previous = Semester.objects.filter(is_active=True).exclude(pk=sem.pk).first()
    if sem.is_active:
        messages.info(request, f"{sem} is already the active semester.")
        return redirect("adminpanel:semester_list")
    if request.method == "POST":
        warnings = set_active_semester(sem, request)
        messages.success(request, f"{sem} is now the active semester.")
        for w in warnings:
            messages.warning(request, w)
        return redirect("adminpanel:semester_list")
    impact = None
    if previous:
        from network.models import Session

        impact = {
            "accounts": SemesterAccount.objects.filter(semester=previous, is_active=True).count(),
            "sessions": Session.objects.filter(is_active=True, semester_account__semester=previous).count(),
        }
    return render(request, "adminpanel/academics/activate.html", {"semester": sem, "previous": previous, "impact": impact})


# --------------------------------------------------------------------------------------
# bandwidth policies
# --------------------------------------------------------------------------------------
@admin_required
def policy_list(request):
    return render(
        request,
        "adminpanel/academics/policies.html",
        {"policies": BandwidthPolicy.objects.annotate(n_accounts=Count("accounts"))},
    )


@admin_required
def policy_form(request, pk=None):
    instance = get_object_or_404(BandwidthPolicy, pk=pk) if pk else None
    obj, resp = _form_page(request, PolicyForm, instance, "Edit policy" if pk else "Add policy", "adminpanel:policy_list", "policy", "Policy")
    if obj is None:
        return resp
    if obj.sync_status == BandwidthPolicy.SYNC_OK:
        obj.sync_status = BandwidthPolicy.SYNC_NEVER  # changed since last push
        obj.save(update_fields=["sync_status"])
    return redirect("adminpanel:policy_list")


@admin_required
@require_POST
def policy_delete(request, pk):
    policy = get_object_or_404(BandwidthPolicy, pk=pk)
    try:
        policy.delete()
    except ProtectedError:
        messages.error(request, "This policy is assigned to instructor accounts. Reassign them first.")
    else:
        audit(request, "policy.deleted", policy.name)
        messages.success(request, "Policy deleted (the router profile, if any, is left in place).")
    return redirect("adminpanel:policy_list")


@admin_required
@require_POST
def policy_sync(request, pk=None):
    policies = [get_object_or_404(BandwidthPolicy, pk=pk)] if pk else list(BandwidthPolicy.objects.all())
    try:
        with get_router() as r:
            results = [sync_policy(p, r) for p in policies]
    except RouterError as e:
        for p in policies:
            p.sync_status, p.sync_message = BandwidthPolicy.SYNC_FAILED, str(e)[:255]
            p.save(update_fields=["sync_status", "sync_message"])
        messages.error(request, f"Router unreachable: {e}")
        return redirect("adminpanel:policy_list")
    ok = sum(1 for good, _ in results if good)
    audit(request, "policy.synced", ", ".join(p.name for p in policies), {"ok": ok, "failed": len(results) - ok})
    if ok == len(results):
        messages.success(request, f"{ok} polic{'y' if ok == 1 else 'ies'} pushed to the router.")
    else:
        messages.warning(request, f"{ok} of {len(results)} policies synced. See the status column for errors.")
    return redirect("adminpanel:policy_list")
