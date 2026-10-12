from django.urls import path

from . import views, views_academics as acad, views_admins as adm, views_instructors as stu

app_name = "adminpanel"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    # instructors
    path("instructors/", stu.instructor_list, name="instructor_list"),
    path("instructors/add/", stu.instructor_add, name="instructor_add"),
    path("instructors/bulk/", stu.instructor_bulk, name="instructor_bulk"),
    path("instructors/import/", stu.masterlist_upload, name="masterlist_upload"),
    path("instructors/import/preview/", stu.masterlist_preview, name="masterlist_preview"),
    path("instructors/import/confirm/", stu.masterlist_confirm, name="masterlist_confirm"),
    path("instructors/import/cancel/", stu.masterlist_cancel, name="masterlist_cancel"),
    path("instructors/import/template/", stu.masterlist_template, name="masterlist_template"),
    path("instructors/import/credentials/", stu.credentials_sheet, name="credentials_sheet"),
    path("instructors/import/credentials/clear/", stu.credentials_clear, name="credentials_clear"),
    path("instructors/<int:pk>/", stu.instructor_detail, name="instructor_detail"),
    path("instructors/<int:pk>/edit/", stu.instructor_edit, name="instructor_edit"),
    path("instructors/<int:pk>/toggle-active/", stu.instructor_toggle_active, name="instructor_toggle_active"),
    path("instructors/<int:pk>/reset-password/", stu.instructor_reset_password, name="instructor_reset_password"),
    path("instructors/<int:pk>/assign-policy/", stu.instructor_assign_policy, name="instructor_assign_policy"),
    path("instructors/<int:pk>/push-router/", stu.instructor_push_router, name="instructor_push_router"),
    path("instructors/<int:pk>/approve/", stu.instructor_approve, name="instructor_approve"),
    path("instructors/<int:pk>/reject/", stu.instructor_reject, name="instructor_reject"),
    path("accounts/<int:pk>/toggle/", stu.account_toggle, name="account_toggle"),
    # admins
    path("admins/", adm.admin_list, name="admin_list"),
    path("admins/add/", adm.admin_add, name="admin_add"),
    path("admins/<int:pk>/edit/", adm.admin_edit, name="admin_edit"),
    path("admins/<int:pk>/toggle-active/", adm.admin_toggle_active, name="admin_toggle_active"),
    # school years / semesters
    path("semesters/", acad.semester_list, name="semester_list"),
    path("school-years/add/", acad.sy_form, name="sy_add"),
    path("school-years/<int:pk>/edit/", acad.sy_form, name="sy_edit"),
    path("school-years/<int:pk>/delete/", acad.sy_delete, name="sy_delete"),
    path("semesters/add/", acad.semester_form, name="semester_add"),
    path("semesters/<int:pk>/edit/", acad.semester_form, name="semester_edit"),
    path("semesters/<int:pk>/delete/", acad.semester_delete, name="semester_delete"),
    path("semesters/<int:pk>/activate/", acad.semester_activate, name="semester_activate"),
    # policies
    path("policies/", acad.policy_list, name="policy_list"),
    path("policies/add/", acad.policy_form, name="policy_add"),
    path("policies/sync-all/", acad.policy_sync, name="policy_sync_all"),
    path("policies/<int:pk>/edit/", acad.policy_form, name="policy_edit"),
    path("policies/<int:pk>/delete/", acad.policy_delete, name="policy_delete"),
    path("policies/<int:pk>/sync/", acad.policy_sync, name="policy_sync"),
    # monitoring
    path("monitoring/", views.monitoring, name="monitoring"),
    path("monitoring/live/", views.monitoring_live, name="monitoring_live"),
    path("monitoring/kick/", views.monitoring_kick, name="monitoring_kick"),
    # reports & logs
    path("reports/", views.reports, name="reports"),
    path("reports/export.csv", views.report_csv, name="report_csv"),
    path("reports/export.pdf", views.report_pdf, name="report_pdf"),
    path("logs/", views.audit_logs, name="audit_logs"),
]
