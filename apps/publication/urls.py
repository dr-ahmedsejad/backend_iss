from django.urls import path

from .views import HistoriqueView, PlanView, PublierView

urlpatterns = [
    path('plan/',       PlanView.as_view(),       name='publication-plan'),
    path('publier/',    PublierView.as_view(),    name='publication-publier'),
    path('historique/', HistoriqueView.as_view(), name='publication-historique'),
]
