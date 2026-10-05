from django.contrib import admin
from ..models.fx_model import Fx
from core.admins.base_admin import AppModelAdmin

@admin.register(Fx)
class FxAdmin(AppModelAdmin):
    list_display = ["name","badge", "code", "sid", "numeric_code",]

    

    