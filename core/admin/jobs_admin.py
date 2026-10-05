from django.contrib import admin, messages
from django.http import Http404,HttpResponse
from pathlib import Path
from django.db import models
from django_json_widget.widgets import JSONEditorWidget
from unfold.contrib.forms.widgets import WysiwygWidget
from unfold.admin import ModelAdmin

from ..models.jobs import Jobs
from django.http import HttpRequest
from unfold.decorators import action
import subprocess
import sys
from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

@admin.register(Jobs)
class JobsAdmin(ModelAdmin):
    
    list_display = [
        "name",
        "jobtype",
        "scope",
        "status",
        "lastrun",
    ]

    list_filter = [
        "jobtype",
        "scope",
        "status",
    ]

    search_fields = [
        "name",
        "command",
        "description",
    ]

    ordering = [
        "jobtype",
        "name",
    ] 
    
    readonly_fields = [
    "status",
    "lastrun",
    "logfile",
    ]

    list_filter_sheet = False
    list_fullwidth = True
    warn_unsaved_form = True
    change_form_show_cancel_button = True

    formfield_overrides = {
        models.TextField: {
            "widget": WysiwygWidget,
        },
        models.JSONField: {
            "widget": JSONEditorWidget,
        },
    }

    fieldsets = (
        (
            "Задача",
            {
                "fields": [
                    ("name", "jobtype"),
                    ("scope", "status"),
                    "description",
                ],
            },
        ),

        (
            "Выполнение",
            {
                "classes": ["tab"],
                "fields": [
                    "command",
                    "param",
                ],
            },
        ),

        (
            "Состояние",
            {
                "classes": ["tab"],
                "fields": [
                    "lastrun",
                    "logfile",
                ],
            },
        ),
    )
    
    #Действия
    
    actions_row = [
        "run_command",
        "open_log",
    ]
    @action(
        description="Запустить",
        permissions=["run_command"],
        url_path="run-command",
        icon="play_arrow",
    )
    def run_command(self, request: HttpRequest, object_id: int):
        job = self.get_object(request, object_id)
        if not job:
            raise Http404("Задача не найдена")
        
        subprocess.Popen(
            [
                sys.executable,
                "manage.py",
                "run_job",
                str(job.id),
            ],

            cwd=settings.BASE_DIR,

        )

        messages.success(
            request,
            f'Задача "{job.name}" запущена',
        )

        return redirect(
            reverse("admin:core_jobs_changelist")
        )

    def has_run_command_permission(self, request: HttpRequest):
        return True

    @action(
        description="Открыть лог",
        permissions=["open_log"],
        url_path="open-log",
        attrs={"target": "_blank"},
        icon="description",
    )
    def open_log(self, request: HttpRequest, object_id: int):

        job = self.get_object(request, object_id)

        if not job or not job.logfile:
            raise Http404("Лог отсутствует")

        path = Path(job.logfile.path)

        if not path.exists():
            raise Http404("Файл лога не найден")

        text = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        return HttpResponse(
            f"<pre>{text}</pre>",
            content_type="text/html; charset=utf-8",
        )

    def has_open_log_permission(self, request: HttpRequest):
        return True
