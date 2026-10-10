from django.urls import path

from . import webapp

app_name = "tgbot"

urlpatterns = [
    path("app/", webapp.app, name="app"),
    path("api/meta/", webapp.api_meta, name="meta"),
    path("api/balances/", webapp.api_balances, name="balances"),
    path("api/day/", webapp.api_day, name="day"),
    path("api/cp/search/", webapp.api_cp_search, name="cp_search"),
    path("api/cp/", webapp.api_cp, name="cp"),
    path("api/fx/", webapp.api_fx, name="fx"),
    path("api/send/", webapp.api_send, name="send"),
]
