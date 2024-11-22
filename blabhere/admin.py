from django.contrib import admin
from firebase_admin.auth import delete_user, delete_users
from django.utils.translation import gettext_lazy as _

from blabhere.models import User


class ReportedUserListFilter(admin.SimpleListFilter):
    # Human-readable title which will be displayed in the
    # right admin sidebar just above the filter options.
    title = _("Reported Users")

    # Parameter for the filter that will be used in the URL query.
    parameter_name = "reported"

    def lookups(self, request, model_admin):
        """
        Returns a list of tuples. The first element in each
        tuple is the coded value for the option that will
        appear in the URL query. The second element is the
        human-readable name for the option that will appear
        in the right sidebar.
        """
        return [
            (True, _("Reported")),
        ]

    def queryset(self, request, queryset):
        """
        Returns the filtered queryset based on the value
        provided in the query string and retrievable via
        `self.value()`.
        """
        if self.value():
            return queryset.filter(reported_by__isnull=False)


class UserModelAdmin(admin.ModelAdmin):
    search_fields = ["username"]
    list_filter = [ReportedUserListFilter]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def delete_model(self, request, obj):
        delete_user(obj.username)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        users = [user.username for user in queryset.all()]
        delete_users(users)
        super().delete_queryset(request, queryset)


admin.site.register(User, UserModelAdmin)
