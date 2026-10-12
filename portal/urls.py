from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup, name="signup"),
    path("signup/done/", views.signup_done, name="signup_done"),
    path("", views.dashboard, name="dashboard"),
    path("live/", views.dashboard_live, name="dashboard_live"),
    path("history/", views.history, name="history"),
    path("connect/", views.connect, name="connect"),
    path("disconnect/", views.disconnect, name="disconnect"),
    path("logout/", views.logout_view, name="logout"),
]
