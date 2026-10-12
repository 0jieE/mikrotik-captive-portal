from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.staff_login, name="staff_login"),
    path("logout/", views.logout_view, name="logout"),
    path("password/", views.change_password, name="change_password"),
]
