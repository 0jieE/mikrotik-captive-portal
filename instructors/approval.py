"""Approval of instructor self-registrations."""
from django.utils import timezone

from academics.models import BandwidthPolicy, Semester

from .models import Instructor, SemesterAccount


def approve(instructor: Instructor, reviewer, policy: BandwidthPolicy | None = None) -> SemesterAccount | None:
    """Approve a registration and, when a semester is active, give the instructor an internet account.

    Returns the SemesterAccount (existing or new) or None when there is no active semester / no policy yet.
    """
    instructor.approval_status = Instructor.APPROVED
    instructor.reviewed_at = timezone.now()
    instructor.reviewed_by = reviewer
    instructor.save(update_fields=["approval_status", "reviewed_at", "reviewed_by"])

    semester = Semester.get_active()
    policy = policy or BandwidthPolicy.get_default()
    if semester is None or policy is None:
        return None
    account, _ = SemesterAccount.objects.get_or_create(
        instructor=instructor, semester=semester, defaults={"policy": policy}
    )
    return account


def reject(instructor: Instructor, reviewer) -> None:
    instructor.approval_status = Instructor.REJECTED
    instructor.reviewed_at = timezone.now()
    instructor.reviewed_by = reviewer
    instructor.save(update_fields=["approval_status", "reviewed_at", "reviewed_by"])


def pending_count() -> int:
    return Instructor.objects.filter(approval_status=Instructor.PENDING).count()
