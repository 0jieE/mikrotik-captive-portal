from django.contrib import admin
from django.urls import include, path

from accounts import views as account_views

urlpatterns = [
    path("", account_views.home, name="home"),
    path("django-admin/", admin.site.urls),  # Django admin: superusers only
    path("accounts/", include("accounts.urls")),
    path("portal/", include("portal.urls")),
    path("admin-panel/", include("adminpanel.urls")),
]
