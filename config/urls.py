from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import staticfiles_urlpatterns


urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("admin/", admin.site.urls),
    path("tg/", include("tgbot.urls")),

    path(
        "django_plotly_dash/",
        include("django_plotly_dash.urls"),
    ),
]


if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()   # статика и под gunicorn, только при DEBUG=True
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )