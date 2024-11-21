from django.urls import path
from django.views.generic import TemplateView

urlpatterns = [
    path(r"terms/", TemplateView.as_view(template_name="terms.html")),
    path(r"privacy/", TemplateView.as_view(template_name="privacy.html")),
]
