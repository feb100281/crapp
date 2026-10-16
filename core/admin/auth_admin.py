"""
Пользователи и группы в стиле Unfold + матрица прав вместо двух списков.
"""

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, Permission, User
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from ..admins.base_admin import AppModelAdmin
from ..admins.perm_matrix import PermissionMatrixWidget

admin.site.unregister(User)
admin.site.unregister(Group)


class PermMatrixMixin:
    matrix_fields = ("permissions", "user_permissions")

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name in self.matrix_fields:
            kwargs["widget"] = PermissionMatrixWidget
            kwargs["queryset"] = Permission.objects.select_related("content_type")
            field = super().formfield_for_manytomany(db_field, request, **kwargs)
            field.help_text = ""
            field.label = "Права"
            return field
        return super().formfield_for_manytomany(db_field, request, **kwargs)


@admin.register(Group)
class GroupAdmin(PermMatrixMixin, BaseGroupAdmin, AppModelAdmin):
    filter_horizontal = ()
    list_display = ("name", "users_count", "perms_count")
    search_fields = ("name",)

    @admin.display(description="Пользователей")
    def users_count(self, obj):
        return obj.user_set.count()

    @admin.display(description="Прав")
    def perms_count(self, obj):
        return obj.permissions.count()


@admin.register(User)
class UserAdmin(PermMatrixMixin, BaseUserAdmin, AppModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    filter_horizontal = ("groups",)
    list_display = ("username", "first_name", "last_name", "email", "is_active", "is_staff", "is_superuser", "last_login")
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
